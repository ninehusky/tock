#!/usr/bin/env python3
"""
flux_health.py — run `cargo flux -p <crate>` per crate and decide, for each,
whether Flux ACTUALLY COMPLETED CLEANLY. The whole point: a run that ICEs
(e.g. the dyn-predicate crash in flux-infer) gets its errors swallowed by
`check_def_catching_bugs`, so a naive "no error at line X" reads as "proven"
when in fact the checker never finished. That false-clean is what produced the
bogus "only 17 proven" count on 2026-05-23.

So this tool treats run HEALTH as a first-class gate, separate from any
obligation count:

  CLEAN   — crate self-checked ("Checking <crate> v") AND no ICE/panic markers.
            Its results are trustworthy.
  TAINTED — an ICE / `thread 'rustc' panicked` / flux-internal assert fired.
            Any count from this run is UNTRUSTWORTHY. (This is the case the
            old pipeline silently mis-scored.)
  MASKED  — never saw "Checking <crate> v": a dependency failed to compile, so
            this crate was never checked at all (0 errors != clean).
  TIMEOUT — flux did not finish in the time budget.

Exit code is NONZERO if any requested crate is not CLEAN, so this can gate CI:
"every crate Flux is asked to verify must verify to completion, or the build
fails." Obligation counting (negation probe) is layered on top and only ever
runs against CLEAN crates.

Usage:
    tools/.venv/bin/python3 tools/flux_health.py                 # all crates, isolated
    tools/.venv/bin/python3 tools/flux_health.py --crate tickv
    tools/.venv/bin/python3 tools/flux_health.py --skip-build    # reuse logs
    tools/.venv/bin/python3 tools/flux_health.py --json out.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# crate fs path -> cargo package name (path used only for messages)
CRATES = {
    "kernel":               "kernel",
    "libraries/tock-cells": "tock-cells",
    "libraries/tickv":      "tickv",
    "arch/cortex-m":        "cortexm",
    "arch/cortex-v7m":      "cortexv7m",
    "capsules/core":        "capsules-core",
    "capsules/extra":       "capsules-extra",
    "chips/nrf52":          "nrf52",
    "chips/nrf52840":       "nrf52840",
    "chips/nrf5x":          "nrf5x",
}
DEFAULT_CRATES = list(CRATES.values())

# --- health signals -------------------------------------------------------
# An ICE / internal panic. These are what get swallowed and must taint a run.
ICE_MARKERS = [
    re.compile(r"internal compiler error"),
    re.compile(r"thread '.*' panicked at"),
    re.compile(r"Box<dyn Any>"),
    re.compile(r"flux[-_].*\.rs:\d+:\d+:\s"),       # a flux-internal source loc in a panic
    re.compile(r"tracked_span_(?:dbg_)?assert"),
]
# cargo prints "Checking <crate> v" only when it actually compiles (absent on a
# warm flux query cache), so it is NOT a reliable "was this checked" signal.
CHECKING_RE = re.compile(r"^\s*Checking\s+([\w-]+)\s+v")
# flux end-of-run summary IS the reliable "a flux check pass completed" signal.
SUMMARY_RE = re.compile(
    r"summary\.\s+(\d+)\s+functions processed:\s+(\d+)\s+checked;\s+"
    r"(\d+)\s+trusted;\s+(\d+)\s+ignored\.\s+(\d+)\s+constraints solved"
)
# refinement errors (real obligations that failed) — short or long format
ERROR_RE = re.compile(r"error\[(?:E0999|FLUX[^\]]*)\]")
# "could not compile `<crate>`": if it names a DEPENDENCY (not the target), the
# target was masked (never reached). If it names the target, the target was
# checked and flux just found errors — that is NOT masking.
COMPILE_FAIL_RE = re.compile(r"could not compile `([\w-]+)`")
EXIT_RE = re.compile(r"^--- exit: (-?\d+) ---")


def run_flux(pkg: str, out_dir: Path, isolated: bool, timeout: int) -> Path:
    log = out_dir / f"{pkg}.log"
    print(f"=== {pkg} ==={' (isolated)' if isolated else ''}", flush=True)
    env = os.environ.copy()
    if isolated:
        env["CARGO_TARGET_DIR"] = str((out_dir / "target" / pkg).resolve())
    try:
        r = subprocess.run(
            ["cargo", "flux", "-p", pkg, "--keep-going"],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        log.write_text(
            f"--- exit: {r.returncode} ---\n--- stdout ---\n{r.stdout}\n"
            f"--- stderr ---\n{r.stderr}\n"
        )
    except subprocess.TimeoutExpired:
        log.write_text(f"TIMEOUT after {timeout}s\n")
    return log


def classify(log: Path, pkg: str) -> dict:
    """Decide CLEAN / TAINTED / MASKED / TIMEOUT for one crate's log."""
    text = log.read_text() if log.exists() else ""
    if text.startswith("TIMEOUT"):
        return {"health": "TIMEOUT", "self_checked": False, "ice": [],
                "errors": 0, "summary": None}

    summary = None
    errors = 0
    ice_hits = []
    exit_code = None
    dep_compile_fail = False
    for line in text.splitlines():
        m = EXIT_RE.match(line)
        if m:
            exit_code = int(m.group(1))
        m = SUMMARY_RE.search(line)
        if m:
            summary = {
                "processed": int(m.group(1)), "checked": int(m.group(2)),
                "trusted": int(m.group(3)), "ignored": int(m.group(4)),
                "solved": int(m.group(5)),
            }
        if ERROR_RE.search(line):
            errors += 1
        m = COMPILE_FAIL_RE.search(line)
        if m and m.group(1) != pkg:
            dep_compile_fail = True   # a dependency failed to build -> target masked
        for pat in ICE_MARKERS:
            if pat.search(line):
                ice_hits.append(line.strip()[:160])
                break

    # A flux summary means a check pass completed. Multiple summaries appear
    # (one per flux-enabled crate); the target's is among them unless a dep
    # build failed first (dep_compile_fail) and stopped cargo before the target.
    checked = summary is not None
    if ice_hits:
        health = "TAINTED"
    elif dep_compile_fail and not checked:
        health = "MASKED"
    elif checked:
        health = "CLEAN"
    else:
        health = "MASKED"
    return {"health": health, "self_checked": checked, "exit": exit_code,
            "dep_compile_fail": dep_compile_fail,
            "ice": ice_hits[:5], "errors": errors, "summary": summary}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--crate", help="single cargo package (default: all)")
    ap.add_argument("--out", type=Path, default=Path("tools/flux_health_logs"))
    ap.add_argument("--skip-build", action="store_true", help="reuse existing logs")
    ap.add_argument("--no-isolated", action="store_true",
                    help="share one target dir (faster but a failing dep masks downstream)")
    ap.add_argument("--timeout", type=int, default=2400)
    ap.add_argument("--json", type=Path, help="write machine-readable results here")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    pkgs = [args.crate] if args.crate else DEFAULT_CRATES
    isolated = not args.no_isolated

    results = {}
    for pkg in pkgs:
        log = args.out / f"{pkg}.log"
        if not (args.skip_build and log.exists()):
            run_flux(pkg, args.out, isolated, args.timeout)
        results[pkg] = classify(log, pkg)

    icon = {"CLEAN": "ok ", "TAINTED": "ICE", "MASKED": "msk", "TIMEOUT": "t/o"}
    print()
    print(f"{'crate':<16} {'health':<8} {'self?':<6} {'errors':>6} {'fns_chk':>8}  notes")
    print("-" * 78)
    for pkg in pkgs:
        r = results[pkg]
        s = r["summary"] or {}
        note = ""
        if r["health"] == "TAINTED":
            note = r["ice"][0] if r["ice"] else "ICE"
        elif r["health"] == "MASKED":
            note = "dependency failed to flux-compile; crate never checked"
        print(f"{pkg:<16} {icon[r['health']]} {r['health']:<4} "
              f"{str(r['self_checked']):<6} {r['errors']:>6} "
              f"{s.get('checked','-'):>8}  {note[:60]}")

    clean = [p for p in pkgs if results[p]["health"] == "CLEAN"]
    bad = [p for p in pkgs if results[p]["health"] != "CLEAN"]
    print()
    print(f"CLEAN (trustworthy): {len(clean)}/{len(pkgs)} — {', '.join(clean) or 'none'}")
    if bad:
        print(f"NOT CLEAN (counts untrustworthy): {', '.join(bad)}")
        print("  → counts for these crates MUST NOT be reported as 'proven'.")

    if args.json:
        args.json.write_text(json.dumps(results, indent=2))
        print(f"\nwrote {args.json}")

    # CI gate: nonzero if any requested crate did not verify to completion.
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
