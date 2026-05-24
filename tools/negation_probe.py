#!/usr/bin/env python3
"""
negation_probe.py — measure, reproducibly, how many flux_support::assert
obligations Flux GENUINELY checks (and of those, how many pass).

Why this exists: "the enclosing fn doesn't error" does NOT mean an assert is
proven. Flux may skip a body, or — the trap that produced a bogus count on
2026-05-23 — an ICE elsewhere in the crate gets swallowed and suppresses
error emission, so a flipped assert(false) wrongly reads as "silent / proven".

So this probe is HEALTH-GATED: before trusting any result for a crate, the
crate's flux run must COMPLETE WITHOUT AN ICE. If a flip introduces an ICE,
that single result is marked TAINTED rather than counted.

Taxonomy per `flux_support::assert(cond)` site (cond != false):
  PROVEN  — passes in baseline AND flipping cond->false makes Flux error here.
            (a live obligation Flux discharges)
  FAILING — Flux already errors here in baseline. (a live obligation Flux
            evaluates but cannot currently prove)
  SILENT  — passes in baseline AND still silent when flipped to false.
            => Flux is NOT checking this site (skipped body / vacuous), OR the
            line is already proven dead (flip is vacuously fine). These two are
            indistinguishable here; flagged for manual audit, NOT counted proven.

For existing `assert(false)` sentinels (prove-this-is-dead-code obligations):
  DEAD_PROVEN  — no error in baseline (Flux accepts the line as unreachable).
  DEAD_FAILING — errors in baseline (Flux cannot prove the line dead).

"Live obligations Flux checks" = PROVEN + FAILING + DEAD_PROVEN + DEAD_FAILING.
"Obligations that PASS" = PROVEN + DEAD_PROVEN.

Reproducible: pure function of the source tree + installed flux. Writes
tools/negation_probe.json. Restores every file it touches and asserts a clean
git tree at the end (fails loudly otherwise).

Usage:
    tools/.venv/bin/python3 tools/negation_probe.py                # all CLEAN crates from flux_health.json
    tools/.venv/bin/python3 tools/negation_probe.py --crate tickv  # one
    tools/.venv/bin/python3 tools/negation_probe.py --health tools/flux_health.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CRATES = {                       # cargo pkg -> filesystem crate dir
    "kernel": "kernel",
    "tock-cells": "libraries/tock-cells",
    "tickv": "libraries/tickv",
    "cortexm": "arch/cortex-m",
    "cortexv7m": "arch/cortex-v7m",
    "capsules-core": "capsules/core",
    "capsules-extra": "capsules/extra",
    "nrf52": "chips/nrf52",
    "nrf52840": "chips/nrf52840",
    "nrf5x": "chips/nrf5x",
}

ASSERT_CALL = "flux_support::assert("
ICE_MARKERS = [
    re.compile(r"internal compiler error"),
    re.compile(r"thread '.*' panicked at"),
    re.compile(r"Box<dyn Any>"),
    re.compile(r"tracked_span_(?:dbg_)?assert"),
]
# long-format flux error header: "error[E0999]: ..." preceded by a "--> file:line:col"
LOC_RE = re.compile(r"-->\s+(\S+\.rs):(\d+):(\d+)")
ERR_HEADER_RE = re.compile(r"error\[(?:E0999|FLUX[^\]]*)\]")


# --------------------------------------------------------------------------
# source scanning
# --------------------------------------------------------------------------
def find_assert_sites(text: str):
    """Yield dicts for each flux_support::assert( call: byte offsets of the
    inner argument, the call's start line (1-based), and #newlines in the arg."""
    sites = []
    idx = 0
    while True:
        i = text.find(ASSERT_CALL, idx)
        if i == -1:
            break
        arg_start = i + len(ASSERT_CALL)
        # balance parens from arg_start, skipping string/char literals + // comments
        depth = 1
        j = arg_start
        in_str = None  # '"' or "'" when inside a literal
        while j < len(text) and depth > 0:
            c = text[j]
            if in_str:
                if c == "\\":
                    j += 2
                    continue
                if c == in_str:
                    in_str = None
            else:
                if c in ('"', "'"):
                    in_str = c
                elif c == "/" and j + 1 < len(text) and text[j + 1] == "/":
                    nl = text.find("\n", j)
                    j = len(text) if nl == -1 else nl
                    continue
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        arg_end = j  # index of the matching ')'
        inner = text[arg_start:arg_end]
        start_line = text.count("\n", 0, i) + 1
        sites.append({
            "call_off": i,
            "arg_start": arg_start,
            "arg_end": arg_end,
            "start_line": start_line,
            "n_nl": inner.count("\n"),
            "inner": inner.strip(),
        })
        idx = arg_end + 1
    return sites


def flip_text(text: str, site: dict) -> str:
    """Replace a site's argument with `false`, preserving line numbers by
    keeping the same count of newlines."""
    new_inner = "false" + "\n" * site["n_nl"]
    return text[:site["arg_start"]] + new_inner + text[site["arg_end"]:]


FN_DECL_RE = re.compile(r"(?m)^([ \t]*)(?:pub(?:\([^)]*\))?\s+)?"
                        r"(?:const\s+|unsafe\s+|async\s+|extern\s+\"[^\"]*\"\s+)*fn\s+\w+")


def enclosing_fn_trusted(text: str, call_off: int) -> bool:
    """True if the fn enclosing `call_off` (or an impl/mod above it) carries a
    #[flux_rs::trusted] attribute — so an assert inside is blocked/skipped, not
    a genuine 'not checked' miss."""
    last = None
    for m in FN_DECL_RE.finditer(text):
        if m.start() > call_off:
            break
        last = m
    if not last:
        return False
    # scan the attribute/doc lines immediately above the fn decl
    head = text.rfind("\n", 0, last.start())
    probe = text[max(0, head - 400):last.start()]
    return "flux_rs::trusted" in probe


# --------------------------------------------------------------------------
# flux invocation + error parsing
# --------------------------------------------------------------------------
def run_flux(pkg: str, target: Path, timeout: int = 1800) -> str:
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(target)
    try:
        r = subprocess.run(
            ["cargo", "flux", "-p", pkg, "--keep-going"],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        return r.stdout + "\n" + r.stderr
    except subprocess.TimeoutExpired:
        return "TIMEOUT"


def has_ice(log: str) -> bool:
    return any(p.search(log) for p in ICE_MARKERS)


def error_lines(log: str, crate_dir: str) -> set:
    """Set of (relpath_within_crate, line) for every flux error location that
    lies in this crate's src. Long format: a '--> file:line:col' line that is
    part of an error[E0999] block."""
    out = set()
    lines = log.splitlines()
    for n, line in enumerate(lines):
        m = LOC_RE.search(line)
        if not m:
            continue
        f, ln = m.group(1), int(m.group(2))
        # is this location attached to an error (header within a few lines above)?
        ctx = "\n".join(lines[max(0, n - 3):n + 1])
        if not ERR_HEADER_RE.search(ctx):
            # also accept the inverse layout (--> after header on same block)
            ctx2 = "\n".join(lines[n:n + 3])
            if not ERR_HEADER_RE.search(ctx2):
                continue
        f = f.lstrip("./")
        if f.startswith(crate_dir + "/"):
            out.add((f[len(crate_dir) + 1:], ln))
        else:
            out.add((f, ln))
    return out


def err_at(errs: set, relfile: str, site: dict) -> bool:
    lo, hi = site["start_line"], site["start_line"] + site["n_nl"]
    return any(rf.endswith(relfile) and lo <= ln <= hi for (rf, ln) in errs)


# --------------------------------------------------------------------------
# per-crate probe
# --------------------------------------------------------------------------
def included_files(crate_dir: str):
    """Return list of src .rs files that contain assert calls. (We probe every
    assert; out-of-include ones simply come back SILENT, which is correct.)"""
    root = Path(crate_dir) / "src"
    return [p for p in root.rglob("*.rs")
            if ASSERT_CALL in p.read_text(errors="ignore")]


def probe_crate(pkg: str, log_dir: Path, timeout: int) -> dict:
    crate_dir = CRATES[pkg]
    target = (log_dir / "target" / pkg).resolve()
    target.mkdir(parents=True, exist_ok=True)

    files = included_files(crate_dir)
    # baseline
    print(f"  [{pkg}] baseline run ({len(files)} files w/ asserts)...", flush=True)
    base_log = run_flux(pkg, target, timeout)
    (log_dir / f"{pkg}.baseline.log").write_text(base_log)
    if base_log == "TIMEOUT":
        return {"pkg": pkg, "status": "TIMEOUT", "sites": []}
    if has_ice(base_log):
        return {"pkg": pkg, "status": "TAINTED_BASELINE", "sites": []}
    base_errs = error_lines(base_log, crate_dir)

    results = []
    for f in files:
        relfile = str(f.relative_to(crate_dir))
        orig = f.read_text()
        sites = find_assert_sites(orig)
        for si, site in enumerate(sites):
            rec = {"file": relfile, "line": site["start_line"],
                   "inner": site["inner"][:80]}
            is_sentinel = site["inner"] == "false"
            base_hit = err_at(base_errs, relfile, site)
            trusted = enclosing_fn_trusted(orig, site["call_off"])
            rec["trusted_fn"] = trusted
            if is_sentinel:
                rec["category"] = "DEAD_FAILING" if base_hit else "DEAD_PROVEN"
                results.append(rec)
                continue
            if base_hit:
                rec["category"] = "FAILING"
                results.append(rec)
                continue
            if trusted:
                # enclosing fn is trusted -> body skipped; flip can't fire.
                rec["category"] = "TRUSTED_BLOCKED"
                results.append(rec)
                print(f"    {relfile}:{site['start_line']:<5} TRUSTED_BLOCKED  {site['inner'][:50]}", flush=True)
                continue
            # passes baseline -> flip to false and re-check
            try:
                f.write_text(flip_text(orig, site))
                flog = run_flux(pkg, target, timeout)
            finally:
                f.write_text(orig)  # always restore
            if flog == "TIMEOUT":
                rec["category"] = "TIMEOUT_FLIP"
            elif has_ice(flog):
                rec["category"] = "TAINTED_FLIP"
            else:
                ferrs = error_lines(flog, crate_dir)
                rec["category"] = "PROVEN" if err_at(ferrs, relfile, site) else "SILENT"
            results.append(rec)
            print(f"    {relfile}:{site['start_line']:<5} {rec['category']:<13} {site['inner'][:50]}", flush=True)
    return {"pkg": pkg, "status": "OK", "sites": results}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--crate", help="single cargo pkg (default: all CLEAN per --health)")
    ap.add_argument("--health", type=Path, default=Path("tools/flux_health.json"))
    ap.add_argument("--out", type=Path, default=Path("tools/negation_probe.json"))
    ap.add_argument("--log-dir", type=Path, default=Path("tools/negation_probe_logs"))
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args()
    args.log_dir.mkdir(parents=True, exist_ok=True)

    if args.crate:
        pkgs = [args.crate]
    else:
        health = json.loads(args.health.read_text())
        pkgs = [p for p, r in health.items() if r.get("health") == "CLEAN"]
        print(f"CLEAN crates from {args.health}: {pkgs}")

    all_results = {}
    for pkg in pkgs:
        print(f"=== probing {pkg} ===", flush=True)
        all_results[pkg] = probe_crate(pkg, args.log_dir, args.timeout)
        args.out.write_text(json.dumps(all_results, indent=2))  # checkpoint after each crate

    # tally
    cats = ["PROVEN", "FAILING", "SILENT", "TRUSTED_BLOCKED",
            "DEAD_PROVEN", "DEAD_FAILING", "TAINTED_FLIP", "TIMEOUT_FLIP"]
    print("\n" + "=" * 64)
    print(f"{'crate':<16} " + " ".join(f"{c[:7]:>7}" for c in cats))
    tot = {c: 0 for c in cats}
    for pkg, r in all_results.items():
        per = {c: 0 for c in cats}
        for s in r["sites"]:
            per[s["category"]] = per.get(s["category"], 0) + 1
        for c in cats:
            tot[c] += per[c]
        status = "" if r["status"] == "OK" else f"  !! {r['status']}"
        print(f"{pkg:<16} " + " ".join(f"{per[c]:>7}" for c in cats) + status)
    print("-" * 64)
    print(f"{'TOTAL':<16} " + " ".join(f"{tot[c]:>7}" for c in cats))
    print()
    live = tot["PROVEN"] + tot["FAILING"] + tot["DEAD_PROVEN"] + tot["DEAD_FAILING"]
    passing = tot["PROVEN"] + tot["DEAD_PROVEN"]
    print(f"LIVE obligations Flux checks: {live}")
    print(f"  of which PASS (proven):     {passing}")
    print(f"  of which FAIL (unproven):   {tot['FAILING'] + tot['DEAD_FAILING']}")
    print(f"SILENT (not checked / dead — manual audit): {tot['SILENT']}")
    if tot["TAINTED_FLIP"] or tot["TIMEOUT_FLIP"]:
        print(f"!! untrustworthy flips: TAINTED={tot['TAINTED_FLIP']} TIMEOUT={tot['TIMEOUT_FLIP']}")

    # safety: tree must be clean (we restored everything)
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "*.rs"],
                           capture_output=True, text=True).stdout.strip()
    src_dirty = [l for l in dirty.splitlines() if l.strip().endswith(".rs")]
    if src_dirty:
        print("\n!!! WARNING: source tree not clean after probe — restoring:")
        for l in src_dirty:
            print("   ", l)
        subprocess.run(["git", "checkout", "--"] + [l[3:] for l in src_dirty])
    else:
        print("\n✓ source tree clean (all flips restored)")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
