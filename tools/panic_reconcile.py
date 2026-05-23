#!/usr/bin/env python3
"""
panic_reconcile.py — put the obligation chart into the panic-SITE frame.

Joins a *current-build* panic survey (tools/panic_survey_branch.json, produced
by panic_survey.py against the branch binary) to the source obligation
dispositions. Because the survey re-derives each panic's source location from
the same code the markers live in, the join is by current file:line — no stale
address problem.

Output: the branch's panic sites partitioned (stdlib / deduped / annotated),
and each distinct annotated SITE classified by obligation status
(locally-proven / failing / trusted / blocked / not-checked), so the SITE frame
and the obligation frame reconcile.

Run: tools/.venv/bin/python3 tools/panic_reconcile.py [--errors-dir tools/flux_audit_logs_clean]
"""
import argparse, json, re, tomllib
from collections import Counter, defaultdict
from pathlib import Path

CRATES = {
    "kernel": "kernel", "libraries/tickv": "tickv",
    "capsules/core": "capsules-core", "capsules/extra": "capsules-extra",
    "libraries/tock-cells": "tock-cells",
    "chips/nrf52": "nrf52", "chips/nrf52840": "nrf52840", "chips/nrf5x": "nrf5x",
    "arch/cortex-m": "cortexm", "arch/cortex-v7m": "cortexv7m",
}
FN_RE = re.compile(r"\bfn\s+([A-Za-z_]\w*)\s*[(<]")
TRUSTED_RE = re.compile(r"#\[flux_rs::trusted(_impl)?\b")


def load_includes(cp):
    try:
        inc = tomllib.load(open(Path(cp) / "Cargo.toml", "rb"))["package"]["metadata"]["flux"].get("include")
    except Exception:
        inc = None
    files, defs = set(), set()
    if inc:
        for e in inc:
            if e.startswith("def:"): defs.add(e[4:])
            elif not e.startswith("span:"): files.add(e)
    return inc is None, files, defs


def crate_of(relpath):
    for cp in CRATES:
        if relpath.startswith(cp + "/"):
            return cp
    return None


def fn_is_trusted(path, fn_name):
    try:
        L = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return False
    for i, line in enumerate(L):
        m = FN_RE.search(line)
        if m and m.group(1) == fn_name:
            for j in range(i, max(i - 10, -1), -1):
                if TRUSTED_RE.search(L[j]):
                    return True
    return False


def site_assert_state(path, line):
    """Look around the panic line for FLUX-OPT / live / commented assert."""
    try:
        L = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return None, False
    lo, hi = max(line - 8, 0), min(line + 2, len(L))
    flux_opt = any("FLUX-OPT" in L[k] for k in range(lo, hi))
    live = any("flux_support::assert" in L[k] and not L[k].lstrip().startswith("//")
               for k in range(lo, hi))
    commented = any("flux_support::assert" in L[k] and L[k].lstrip().startswith("//")
                    for k in range(lo, hi))
    return ("opt" if flux_opt else "live" if live else "commented" if commented else "none"), live


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--survey", default="tools/panic_survey_branch.json")
    ap.add_argument("--errors-dir", default="tools/flux_audit_logs_clean")
    args = ap.parse_args()

    sites = json.load(open(args.survey))["sites"]

    # erroring functions from the clean audit
    err_fns = set()
    if args.errors_dir:
        for cp, name in CRATES.items():
            log = Path(args.errors_dir) / f"{name}.log"
            if not log.exists(): continue
            for ln in log.read_text(errors="replace").splitlines():
                m = re.match(r"([^\s:]+\.rs):(\d+):", ln)
                if m and "error[E0999]" in ln:
                    f = m.group(1).lstrip("./")
                    if Path(f).exists():
                        FL = Path(f).read_text(errors="replace").splitlines()
                        fn = "<none>"
                        for j in range(min(int(m.group(2)), len(FL)) - 1, -1, -1):
                            fm = FN_RE.search(FL[j])
                            if fm: fn = fm.group(1); break
                        err_fns.add((f, fn))

    inc_cache = {cp: load_includes(cp) for cp in CRATES}

    # --- partition + dedup by (file,line) ---
    top = Counter()
    groups = defaultdict(list)        # (file,line) -> sites (user source)
    for s in sites:
        ef = s["effective_frame"]
        f = (ef.get("file") or "").lstrip("./")
        if not f or "/rustc/" in f or f.startswith("/rustc"):
            top["stdlib-unannotatable"] += 1
            continue
        if not crate_of(f):
            top["user-source (untracked crate)"] += 1
            continue
        groups[(f, ef.get("line"), ef.get("func", "").split("::")[-1])].append(s)

    distinct = len(groups)
    deduped = sum(len(v) - 1 for v in groups.values())

    # --- classify each distinct annotated site ---
    site_status = Counter()
    failing_sites = []
    for (f, line, fn), grp in groups.items():
        cp = crate_of(f)
        relpath = f[len(cp) + 1:]
        alli, files, defs = inc_cache[cp]
        included = alli or relpath in files or any(d in fn for d in defs)
        trusted = fn_is_trusted(f, fn)
        errs = (f, fn) in err_fns
        state, live = site_assert_state(f, line or 0)
        if trusted:
            site_status["trusted"] += 1
        elif not included:
            site_status["not-checked (fn not in Cargo.toml include)"] += 1
        elif errs:
            site_status["FAILING (included, not trusted, Flux rejects)"] += 1
            failing_sites.append((f, line, fn))
        elif state == "commented":
            site_status["blocked (assert commented out)"] += 1
        else:
            site_status["locally-proven"] += 1

    print(f"=== Branch panic survey: {len(sites)} raw bl sites ===\n")
    print("Partition:")
    print(f"  {distinct:>4}  distinct annotated sites (user source)")
    print(f"  {deduped:>4}  deduped dupes (same source line, inlined)")
    for k, v in top.items():
        print(f"  {v:>4}  {k}")
    print(f"  ----  {distinct + deduped + sum(top.values())} total")

    print(f"\nObligation status of the {distinct} distinct annotated sites:")
    for k, v in site_status.most_common():
        print(f"  {v:>4}  {k}")
    print(f"  ----  {sum(site_status.values())}")


if __name__ == "__main__":
    main()
