#!/usr/bin/env python3
"""
dead_proven_validate.py — validate the DEAD_PROVEN sentinels from
negation_probe.json with an active reachability control.

Problem: a `flux_support::assert(false)` sentinel that produces NO baseline error
is only a real dead-code proof if Flux actually CHECKS the enclosing body. In an
unchecked body (skipped closure, body Flux never enters), assert(false) is also
silent — vacuously, not because the line is proven unreachable.

Control: insert `flux_support::assert(false);` as the FIRST statement of the
enclosing fn (always reachable when the body is checked) and re-run flux.
  - error at the inserted entry line  => body IS checked => the original sentinel's
    silence is a GENUINE dead-code proof.            -> DEAD_VALIDATED
  - no error at the inserted entry line => body NOT checked (vacuous)
                                                     -> DEAD_VACUOUS

Health-gated and auto-restoring like negation_probe.py. Reads negation_probe.json,
writes tools/dead_proven_validate.json.

Usage:
    tools/.venv/bin/python3 tools/dead_proven_validate.py
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CRATES = {
    "kernel": "kernel", "tock-cells": "libraries/tock-cells", "tickv": "libraries/tickv",
    "cortexm": "arch/cortex-m", "cortexv7m": "arch/cortex-v7m",
    "capsules-core": "capsules/core", "capsules-extra": "capsules/extra",
    "nrf52": "chips/nrf52", "nrf52840": "chips/nrf52840", "nrf5x": "chips/nrf5x",
}
ICE = [re.compile(r"internal compiler error"), re.compile(r"thread '.*' panicked at"),
       re.compile(r"Box<dyn Any>"), re.compile(r"tracked_span_(?:dbg_)?assert")]
LOC_RE = re.compile(r"-->\s+(\S+\.rs):(\d+):(\d+)")
ERR_RE = re.compile(r"error\[(?:E0999|FLUX[^\]]*)\]")
FN_DECL_RE = re.compile(r"(?m)^([ \t]*)(?:pub(?:\([^)]*\))?\s+)?"
                        r"(?:const\s+|unsafe\s+|async\s+|extern\s+\"[^\"]*\"\s+)*fn\s+\w+")


def run_flux(pkg, target, timeout=1800):
    env = os.environ.copy(); env["CARGO_TARGET_DIR"] = str(target)
    try:
        r = subprocess.run(["cargo", "flux", "-p", pkg, "--keep-going"],
                           capture_output=True, text=True, timeout=timeout, env=env)
        return r.stdout + "\n" + r.stderr
    except subprocess.TimeoutExpired:
        return "TIMEOUT"


def has_ice(log): return any(p.search(log) for p in ICE)


def err_at_line(log, crate_dir, relfile, line):
    lines = log.splitlines()
    for n, ln in enumerate(lines):
        m = LOC_RE.search(ln)
        if not m:
            continue
        f, l = m.group(1), int(m.group(2))
        ctx = "\n".join(lines[max(0, n - 3):n + 3])
        if not ERR_RE.search(ctx):
            continue
        if f.lstrip("./").endswith(relfile) and l == line:
            return True
    return False


def fn_body_open(text, line):
    """Byte offset just after the `{` opening the body of the fn enclosing `line`."""
    off = sum(len(l) for l in text.splitlines(keepends=True)[:line - 1])
    last = None
    for m in FN_DECL_RE.finditer(text):
        if m.start() <= off:
            last = m
        else:
            break
    if not last:
        return None
    brace = text.find("{", last.end())
    return brace + 1 if brace != -1 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", type=Path, default=Path("tools/negation_probe.json"))
    ap.add_argument("--out", type=Path, default=Path("tools/dead_proven_validate.json"))
    ap.add_argument("--log-dir", type=Path, default=Path("tools/negation_probe_logs"))
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args()

    probe = json.loads(args.probe.read_text())
    results = {}
    for pkg, r in probe.items():
        deads = [s for s in r.get("sites", []) if s["category"] == "DEAD_PROVEN"]
        if not deads:
            continue
        crate_dir = CRATES[pkg]
        target = (args.log_dir / "target" / pkg).resolve()
        print(f"=== {pkg}: validating {len(deads)} DEAD_PROVEN ===", flush=True)
        out = []
        for s in deads:
            f = Path(crate_dir) / s["file"]
            orig = f.read_text()
            ob = fn_body_open(orig, s["line"])
            rec = {"file": s["file"], "line": s["line"], "inner_was": s.get("inner")}
            if ob is None:
                rec["verdict"] = "NOFN"
                out.append(rec); continue
            entry_line = orig.count("\n", 0, ob) + 1
            injected = orig[:ob] + "\n        flux_support::assert(false);" + orig[ob:]
            try:
                f.write_text(injected)
                log = run_flux(pkg, target, args.timeout)
            finally:
                f.write_text(orig)
            if log == "TIMEOUT" or has_ice(log):
                rec["verdict"] = "TAINTED"
            else:
                # the injected assert is on entry_line+1 (we inserted a leading \n)
                hit = err_at_line(log, crate_dir, s["file"], entry_line + 1)
                rec["verdict"] = "DEAD_VALIDATED" if hit else "DEAD_VACUOUS"
            print(f"  {s['file']}:{s['line']:<5} {rec['verdict']:<15} (fn entry ~L{entry_line})", flush=True)
            out.append(rec)
        results[pkg] = out
        args.out.write_text(json.dumps(results, indent=2))

    val = sum(1 for p in results.values() for x in p if x["verdict"] == "DEAD_VALIDATED")
    vac = sum(1 for p in results.values() for x in p if x["verdict"] == "DEAD_VACUOUS")
    other = sum(1 for p in results.values() for x in p if x["verdict"] in ("TAINTED", "NOFN"))
    print(f"\nDEAD_VALIDATED (genuine dead-code proof): {val}")
    print(f"DEAD_VACUOUS  (body not checked):         {vac}")
    print(f"other (tainted/no-fn):                    {other}")
    dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout
    src = [l for l in dirty.splitlines() if l.strip().endswith(".rs")]
    if src:
        print("!! restoring dirty source:", src)
        subprocess.run(["git", "checkout", "--"] + [l[3:] for l in src])
    else:
        print("✓ tree clean")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    sys.exit(main())
