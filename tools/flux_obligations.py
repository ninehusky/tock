#!/usr/bin/env python3
"""flux_obligations.py — total Flux-obligation census across a crate chain.

Goal: the GENUINE list of every Flux proof obligation each crate would emit if
checked whole-crate, even though crates mask each other (an upstream crate that
emits errors fails to compile, so its dependents are never checked).

Approach (like check_invariant2's dependency unmasking, but to COUNT rather than
gate): walk crates in dependency order. For each crate:

  1. MEASURE — enable whole-crate Flux (drop `[package.metadata.flux] include`)
     and run it against already-temp-disabled (clean) dependencies, so nothing
     upstream masks it. Dodge *body-checking* ICEs by temporarily marking the
     crashing fn `#[flux_rs::trusted]` (those fns are recorded as blind spots).
     Collect every Flux diagnostic in the crate's own files.
  2. DISABLE — temp-set `default_trusted = true` on the crate so it compiles
     clean as a dependency for everything downstream (its fn bodies are trusted,
     but its sigs/types still export, so dependents resolve against it).

All temp edits (include drops, injected trusts, default_trusted) are reverted at
the end via `git checkout` of the touched files — the tool never leaves the tree
mutated. Output: JSON, one record per crate, with every obligation classified by
kind, plus the trusted-fn blind spots and a health verdict.

A crate can hit a *sig-elaboration* ICE (e.g. flux-infer UnsolvedEvar on a
`&dyn Fn` trait method) that `#[flux_rs::trusted]` cannot dodge — it crashes
while building the signature, before/independent of body checking. Those are
detected (the dodge loop stalls) and recorded as `health: "elaboration_ice"`;
the tool falls back to the crate's curated scoped `include` (which avoids the
offending item and completes) so a partial obligation list is still produced.

Usage:
    tools/.venv/bin/python3 tools/flux_obligations.py            # whole chain
    tools/.venv/bin/python3 tools/flux_obligations.py --crates nrf5x capsules-core
    tools/.venv/bin/python3 tools/flux_obligations.py --out tools/flux_obligations.json
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# crate dir -> cargo package name, in DEPENDENCY (topological) order: a crate is
# measured only after all its flux-enabled deps have been temp-disabled (clean).
CHAIN = [
    ("libraries/tock-cells", "tock-cells"),
    ("libraries/tickv", "tickv"),
    ("flux_support", "flux_support"),
    ("kernel", "kernel"),
    ("arch/cortex-v7m", "cortexv7m"),
    ("arch/cortex-m", "cortexm"),
    ("chips/nrf5x", "nrf5x"),
    ("chips/nrf52", "nrf52"),
    ("chips/nrf52840", "nrf52840"),
    ("capsules/core", "capsules-core"),
    ("capsules/extra", "capsules-extra"),
    ("boards/nordic/nrf52840dk", "nrf52840dk"),
]

ERR_RE = re.compile(r"error\[(E0999|FLUX[^\]]*)\]:\s*(.*)")
LOC_RE = re.compile(r"-->\s*(\S+?):(\d+):(\d+)")
ICE_RE = re.compile(r"internal compiler error:\s*(.*)")
PANIC_RE = re.compile(r"thread '.*' .*panicked at\s*(.*)")
FN_DECL = re.compile(
    r"^([ \t]*)(?:pub(?:\([^)]*\))?\s+)?"
    r"(?:const\s+|unsafe\s+|async\s+|extern\s+\"[^\"]*\"\s+)*"
    r"fn\s+(\w+)", re.M)
TRUST = "#[flux_rs::trusted]"


def bucket(msg: str) -> str:
    m = msg.lower()
    if "out-of-bounds" in m or "out of bounds" in m:
        return "oob_index"
    if "overflow" in m:
        return "arith_overflow"
    if "division" in m or "divide" in m or "remainder" in m or "modulo" in m:
        return "div_by_zero"
    if "not included when checking external crate" in m or "external crate" in m:
        return "xcrate_resolution"
    if "refinement type error" in m:
        return "refinement_type"
    if "assertion might fail" in m:
        return "assert_might_fail"
    if "precondition" in m or "requires" in m:
        return "precondition"
    return "other"


# --- temp source/Cargo.toml edits (all reverted at end) -------------------
def save(path: Path, saved: dict):
    if str(path) not in saved:
        saved[str(path)] = path.read_text()


def drop_include(toml: Path, saved: dict):
    save(toml, saved)
    text = toml.read_text()
    m = re.search(r"^\[package\.metadata\.flux\][^\n]*\n", text, flags=re.M)
    bs = m.end()
    nxt = re.search(r"^\[", text[bs:], flags=re.M)
    be = bs + nxt.start() if nxt else len(text)
    body = text[bs:be]
    im = re.search(r"^[ \t]*include[ \t]*=[ \t]*\[", body, flags=re.M)
    if not im:
        return
    o = body.index("[", im.start())
    depth, j = 0, o
    while j < len(body):
        if body[j] == "[":
            depth += 1
        elif body[j] == "]":
            depth -= 1
            if depth == 0:
                break
        j += 1
    ls = body.rfind("\n", 0, im.start()) + 1
    le = j + 1
    if le < len(body) and body[le] == "\n":
        le += 1
    toml.write_text(text[:bs] + body[:ls] + body[le:] + text[be:])


def set_default(toml: Path, saved: dict, key: str):
    """Temp-disable a crate as a clean dependency. `default_trusted` keeps sigs
    checked-but-assumed (exports + no body obligations); `default_ignore` skips
    the crate entirely (needed when even sig-elaboration ICEs, but then it does
    not export refinements, so consumers may hit xcrate-resolution errors)."""
    save(toml, saved)
    text = toml.read_text()
    if re.search(rf"^[ \t]*{key}", text, flags=re.M):
        return
    m = re.search(r"^\[package\.metadata\.flux\][^\n]*\n", text, flags=re.M)
    toml.write_text(text[:m.end()] + f"{key} = true\n" + text[m.end():])


def enclosing_fn(text: str, line: int):
    best = None
    for m in FN_DECL.finditer(text):
        dl = text.count("\n", 0, m.start()) + 1
        if dl > line:
            break
        brace = text.find("{", m.end())
        if brace == -1:
            continue
        depth, j = 0, brace
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if dl <= line <= text.count("\n", 0, j) + 1:
            if best is None or dl > best[0]:
                best = (dl, m.group(1))
    return best


def inject_trust(path: Path, line: int, saved: dict) -> str:
    save(path, saved)
    text = path.read_text()
    res = enclosing_fn(text, line)
    if not res:
        return "NO_FN"
    dl, indent = res
    lines = text.split("\n")
    for k in range(max(0, dl - 4), dl - 1):
        if "flux_rs::trusted" in lines[k] and "_impl" not in lines[k]:
            return "ALREADY"
    lines.insert(dl - 1, f"{indent}{TRUST} // flux_obligations: dodge body-check ICE")
    path.write_text("\n".join(lines))
    return f"{path.relative_to(ROOT)}:{dl}"


# --- run + parse ----------------------------------------------------------
def run_flux(pkg: str, target: Path, timeout: int) -> str:
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(target.resolve())
    try:
        r = subprocess.run(["cargo", "flux", "-p", pkg, "--keep-going"],
                           capture_output=True, text=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "__TIMEOUT__"
    return r.stdout + "\n" + r.stderr


def own_errors(out: str, cratedir: str):
    lines, res = out.splitlines(), []
    for n, l in enumerate(lines):
        m = ERR_RE.search(l)
        if not m:
            continue
        for k in range(n, min(n + 6, len(lines))):
            lm = LOC_RE.search(lines[k])
            if lm:
                f = lm.group(1).replace(str(ROOT) + "/", "").lstrip("./")
                if f.startswith(cratedir + "/"):
                    msg = m.group(2).strip()
                    res.append({"code": m.group(1), "file": f, "line": int(lm.group(2)),
                                "col": int(lm.group(3)), "message": msg, "kind": bucket(msg)})
                break
    return res


def first_ice(out: str, cratedir: str):
    """Return (msg, (file,line)) of the first ICE/panic with an in-crate loc."""
    lines = out.splitlines()
    for n, l in enumerate(lines):
        im = ICE_RE.search(l) or PANIC_RE.search(l)
        if not im:
            continue
        kind = "ice" if ICE_RE.search(l) else "panic"
        # source loc: for ICE it's on a following line; for a panic, the LAST
        # in-crate `-->` BEFORE it (the def being checked when it crashed).
        rng = range(n, min(n + 8, len(lines))) if kind == "ice" else range(n, -1, -1)
        for k in rng:
            lm = LOC_RE.search(lines[k])
            if lm:
                f = lm.group(1).replace(str(ROOT) + "/", "").lstrip("./")
                if f.startswith(cratedir + "/"):
                    return (kind, im.group(1)[:140], (f, int(lm.group(2))))
        return (kind, im.group(1)[:140], None)
    return None


# --- per-crate measurement ------------------------------------------------
def scoped_fallback(cratedir, pkg, target, saved, timeout, msg, loc):
    """An elaboration ICE can't be dodged whole-crate; fall back to the crate's
    curated scoped `include` (which avoids the offending item and completes) for
    a partial-but-real obligation list."""
    toml = ROOT / cratedir / "Cargo.toml"
    toml.write_text(saved[str(toml)])  # restore original include (scoped)
    subprocess.run(["cargo", "flux", "clean"], capture_output=True,
                   env={**os.environ, "CARGO_TARGET_DIR": str(target.resolve())})
    out = run_flux(pkg, target, timeout)
    return {"health": "elaboration_ice", "ice": msg,
            "ice_loc": (f"{loc[0]}:{loc[1]}" if loc else None),
            "obligations_scope": "scoped_fallback",
            "obligations": own_errors(out, cratedir)}


def measure(cratedir: str, pkg: str, outdir: Path, saved: dict, timeout: int, log):
    toml = ROOT / cratedir / "Cargo.toml"
    target = outdir / "target" / pkg
    drop_include(toml, saved)
    subprocess.run(["cargo", "flux", "clean"], capture_output=True,
                   env={**os.environ, "CARGO_TARGET_DIR": str(target.resolve())})
    trusted, last_ice = [], None
    for it in range(40):
        out = run_flux(pkg, target, timeout)
        (outdir / f"{pkg}.iter{it}.log").write_text(out)
        if out == "__TIMEOUT__":
            return {"health": "timeout", "obligations": [], "ice_trusted_fns": trusted}
        ice = first_ice(out, cratedir)
        if not ice:
            obs = own_errors(out, cratedir)
            (outdir / f"{pkg}.log").write_text(out)
            return {"health": "clean", "obligations": obs, "ice_trusted_fns": trusted}
        kind, msg, loc = ice
        if not loc:
            # ICE whose source loc is outside this crate (e.g. a generic in a dep,
            # or associated-refinement elaboration) — can't trust a local fn to
            # dodge it. Fall back to the curated scoped include.
            r = scoped_fallback(cratedir, pkg, target, saved, timeout, msg, None)
            r["health"] = "ice_no_loc"
            r["ice_trusted_fns"] = trusted
            return r
        if loc == last_ice:  # same spot twice -> trusting didn't help: elaboration ICE
            r = scoped_fallback(cratedir, pkg, target, saved, timeout, msg, loc)
            r["ice_trusted_fns"] = trusted
            return r
        last_ice = loc
        res = inject_trust(ROOT / loc[0], loc[1], saved)
        if res in ("NO_FN", "ALREADY"):
            r = scoped_fallback(cratedir, pkg, target, saved, timeout, msg, loc)
            r["ice_trusted_fns"] = trusted
            return r
        trusted.append({"fn": res, "ice": msg})
        log(f"    dodged {kind} @ {loc[0]}:{loc[1]} -> trusted {res}")
        subprocess.run(["cargo", "flux", "clean"], capture_output=True,
                       env={**os.environ, "CARGO_TARGET_DIR": str(target.resolve())})
    return {"health": "ice_budget_exhausted", "obligations": [], "ice_trusted_fns": trusted}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--crates", nargs="*", help="subset of package names (default: whole chain)")
    ap.add_argument("--out", type=Path, default=ROOT / "tools" / "flux_obligations.json")
    ap.add_argument("--workdir", type=Path, default=ROOT / "tools" / "flux_obligations_logs")
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=True)
    chain = [(d, p) for d, p in CHAIN if not args.crates or p in args.crates]

    def log(m):
        print(m, flush=True)

    saved, report = {}, {}
    try:
        for cratedir, pkg in chain:
            log(f"=== measure {pkg} ({cratedir}) ===")
            r = measure(cratedir, pkg, args.workdir / pkg, saved, args.timeout, log)
            n = len(r["obligations"])
            log(f"    health={r['health']} obligations={n} "
                f"ice_trusted={len(r['ice_trusted_fns'])}")
            r["crate"] = pkg
            r["crate_dir"] = cratedir
            report[pkg] = r
            # temp-disable so dependents are not masked. trusted exports sigs;
            # but an elaboration-ICE crate ICEs even when trusted -> ignore it
            # (consumers may then see xcrate-resolution errors, recorded as such).
            disable_key = "default_ignore" if r["health"] == "elaboration_ice" else "default_trusted"
            set_default(ROOT / cratedir / "Cargo.toml", saved, disable_key)
    finally:
        # restore every touched file — never leave the tree mutated
        for path, text in saved.items():
            Path(path).write_text(text)
        log(f"restored {len(saved)} files")

    # aggregate
    from collections import Counter
    kinds = Counter()
    for pkg, r in report.items():
        for o in r["obligations"]:
            kinds[o["kind"]] += 1
    summary = {
        "total_obligations": sum(len(r["obligations"]) for r in report.values()),
        "by_kind": dict(kinds.most_common()),
        "ice_trusted_total": sum(len(r["ice_trusted_fns"]) for r in report.values()),
        "elaboration_ice_crates": [p for p, r in report.items()
                                   if r["health"] == "elaboration_ice"],
    }
    args.out.write_text(json.dumps({"summary": summary, "crates": report}, indent=2))
    log(f"\nwrote {args.out}")
    log(f"total obligations: {summary['total_obligations']}  by kind: {summary['by_kind']}")
    log(f"ICE-trusted blind-spot fns: {summary['ice_trusted_total']}")
    log(f"elaboration-ICE (partial) crates: {summary['elaboration_ice_crates']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
