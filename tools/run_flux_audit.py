#!/usr/bin/env python3
"""
run_flux_audit.py — run `cargo flux -p <crate>` for each panic-site-bearing
crate, collect output, and report which panic addresses correspond to
undischarged Flux obligations.

Per memory note `feedback_flux_scoped_runs`: Flux MUST be run scoped to a
crate (`-p <name>`), never from the workspace root, because workspace-root
runs mask spec-resolution issues and can yield false soundness diagnoses.

Outputs:
  - Per-crate logs to <out-dir>/<crate>.log
  - Per-crate summary line (fns checked/trusted/ignored, constraints solved,
    errors found, wall time)
  - Cross-reference: how many panic_ledger addresses have a Flux error
    within ±6 lines of their attributed file:line

Usage:
    tools/.venv/bin/python3 tools/run_flux_audit.py                # all crates
    tools/.venv/bin/python3 tools/run_flux_audit.py --crate kernel # just one
    tools/.venv/bin/python3 tools/run_flux_audit.py --skip-build   # reuse logs
"""

import argparse
import csv
import re
import subprocess
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

# Map crate filesystem path → cargo package name. Path is used to read
# Cargo.toml; package name is used for `cargo flux -p <name>`.
CRATES = {
    "kernel":               "kernel",
    "capsules/core":        "capsules-core",
    "capsules/extra":       "capsules-extra",
    "libraries/tock-cells": "tock-cells",
    "libraries/tickv":      "tickv",
    "arch/cortex-m":        "cortexm",
    "arch/cortex-v7m":      "cortexv7m",
    "chips/nrf52":          "nrf52",
    "chips/nrf52840":       "nrf52840",
    "chips/nrf5x":          "nrf5x",
}
DEFAULT_CRATES = list(CRATES.values())

SUMMARY_RE = re.compile(
    r"summary\.\s+(\d+)\s+functions processed:\s+(\d+)\s+checked;\s+(\d+)\s+trusted;\s+(\d+)\s+ignored\.\s+(\d+)\s+constraints solved\.\s+Finished in ([\d.]+)s"
)
# `--message-format short` puts file:line:col on the same line as the error:
#   kernel/src/grant.rs:1248:9: error[E0999]: refinement type error: a precondition cannot be proved
SHORT_ERROR_RE = re.compile(
    r"^([^\s:]+\.rs):(\d+):(\d+):\s+error\[(?:E0999|FLUX[^\]]*)\]:\s*(.*)"
)
CHECKING_RE = re.compile(r"^\s*Checking\s+([\w-]+)\s+v")


def run_flux(crate: str, out_dir: Path, isolated: bool = False) -> Path:
    """Invoke `cargo flux -p <crate>` and write captured output to <out_dir>/<crate>.log.

    When isolated=True, sets CARGO_TARGET_DIR to a per-crate path so an
    upstream crate's flux errors don't halt this crate's build. Slower
    (each crate rebuilds deps from scratch) but gives independent results.
    """
    log_path = out_dir / f"{crate}.log"
    print(f"=== {crate} ==={' (isolated)' if isolated else ''} (logging to {log_path})", flush=True)
    import os
    env = os.environ.copy()
    if isolated:
        target = (out_dir / "target" / crate).resolve()
        env["CARGO_TARGET_DIR"] = str(target)
    try:
        # `--keep-going` so an upstream crate's flux errors don't halt the
        # build before we get to the target crate.
        result = subprocess.run(
            ["cargo", "flux", "-p", crate, "--keep-going",
             "--message-format", "short"],
            capture_output=True, text=True, timeout=1800, env=env,
        )
    except subprocess.TimeoutExpired:
        log_path.write_text("TIMEOUT after 1800s\n")
        return log_path
    log_path.write_text(
        f"--- exit code: {result.returncode} ---\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}\n"
    )
    return log_path


def parse_log(log_path: Path, target_crate: str) -> dict:
    """Extract per-crate stats + per-(file,line) error list from a flux log.

    Strategy: if we see `Checking <target_crate> v...`, the summary that
    follows belongs to the target. If we never see that line (cargo got
    quieter on cached runs), fall back to assuming the LAST summary is
    the target's (deps run first in dependency order).

    For errors, we always collect every short-format error line. (Errors
    from a dep that failed to verify are not attributable per se, but in
    practice they're rare with --keep-going and target-crate errors
    dominate.)
    """
    text = log_path.read_text()
    target_summary = None     # captured when we're inside the target crate's section
    last_summary = None       # always overwritten with the latest summary seen
    in_target = False
    errors = []
    for line in text.splitlines():
        m_check = CHECKING_RE.match(line)
        if m_check:
            in_target = (m_check.group(1) == target_crate)
            continue
        m_sum = SUMMARY_RE.search(line)
        if m_sum:
            summary = {
                "processed": int(m_sum.group(1)),
                "checked":   int(m_sum.group(2)),
                "trusted":   int(m_sum.group(3)),
                "ignored":   int(m_sum.group(4)),
                "solved":    int(m_sum.group(5)),
                "wall_s":    float(m_sum.group(6)),
            }
            last_summary = summary
            if in_target:
                target_summary = summary
            continue
        m_err = SHORT_ERROR_RE.match(line)
        if m_err:
            errors.append({
                "file": m_err.group(1),
                "line": int(m_err.group(2)),
                "col":  int(m_err.group(3)),
                "msg":  m_err.group(4),
            })
    return {
        "summary": target_summary if target_summary else last_summary,
        "errors": errors,
    }


def audit_includes(ledger_path: Path) -> dict:
    """For each crate, determine which ledger sites are inside files covered
    by the crate's flux include filter vs files that aren't.

    Returns:
        {crate_path: {'whole': n, 'included_file': n, 'def_only': n, 'excluded': n,
                      'excluded_files': [files...]}}
    """
    crate_includes = {}
    for crate_path in CRATES:
        try:
            data = tomllib.load(open(Path(crate_path) / "Cargo.toml", "rb"))
            flux = data.get("package", {}).get("metadata", {}).get("flux", {})
            crate_includes[crate_path] = flux.get("include", None)  # None = whole-crate
        except (OSError, KeyError):
            crate_includes[crate_path] = None

    rows = list(csv.DictReader(open(ledger_path)))
    audit = {p: {"whole": 0, "included_file": 0, "def_only": 0,
                 "excluded": 0, "excluded_files": set()} for p in CRATES}

    for r in rows:
        if r.get("status") in ("singleton-helper", "singleton-monomorph-helper",
                                "removed-on-branch"):
            continue
        f = (r.get("final_file") or r.get("ef_file") or "").lstrip("./")
        if not f:
            continue
        matched_crate = None
        for crate_path in CRATES:
            if f.startswith(crate_path + "/"):
                matched_crate = crate_path
                break
        if not matched_crate:
            continue
        includes = crate_includes[matched_crate]
        if includes is None:
            audit[matched_crate]["whole"] += 1
            continue
        rel_path = f[len(matched_crate) + 1:]
        if rel_path in includes:
            audit[matched_crate]["included_file"] += 1
        elif any(inc.startswith("def:") for inc in includes):
            audit[matched_crate]["def_only"] += 1
            audit[matched_crate]["excluded_files"].add(f)
        else:
            audit[matched_crate]["excluded"] += 1
            audit[matched_crate]["excluded_files"].add(f)
    return audit


def cross_reference(per_crate: dict, ledger_path: Path) -> dict:
    """For each ledger row, mark whether a Flux error covers it (±6 lines)."""
    rows = list(csv.DictReader(open(ledger_path)))
    by_file_line = defaultdict(list)
    for r in rows:
        f = (r.get("final_file") or r.get("ef_file") or "").lstrip("./")
        l_str = r.get("final_line") or r.get("ef_line") or ""
        m = re.search(r"\d+", str(l_str))
        if not f or not m:
            continue
        by_file_line[f].append((int(m.group()), r["address"]))

    all_errors = []
    for crate, data in per_crate.items():
        for e in data["errors"]:
            all_errors.append((crate, e["file"].lstrip("./"), e["line"]))

    matched_addrs = set()
    unmatched_errors = []
    for crate, ef, el in all_errors:
        sites = by_file_line.get(ef, [])
        hit = False
        for site_line, addr in sites:
            if abs(el - site_line) <= 6:
                matched_addrs.add(addr)
                hit = True
        if not hit:
            unmatched_errors.append((crate, ef, el))

    return {
        "total_errors": len(all_errors),
        "matched_addrs": matched_addrs,
        "unmatched_errors": unmatched_errors,
        "ledger_count": len(rows),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--crate", default=None, help="Run a single crate (default: all)")
    ap.add_argument("--out", default=Path("tools/flux_audit_logs"), type=Path)
    ap.add_argument("--skip-build", action="store_true",
                    help="Reuse existing logs instead of re-running flux")
    ap.add_argument("--isolated", action="store_true",
                    help="Use a per-crate CARGO_TARGET_DIR so upstream crate "
                         "flux errors don't halt downstream builds. Slower but "
                         "produces independent per-crate results.")
    ap.add_argument("--ledger", default=Path("tools/panic_ledger.csv"), type=Path)
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    crates = [args.crate] if args.crate else DEFAULT_CRATES

    # Pre-flight: include-list audit
    print("=== Include-list audit (panic-bearing files vs flux include filters) ===")
    print(f"{'crate':<22} {'whole':>6} {'incl-file':>10} {'def-only':>9} {'excluded':>9}")
    inc_audit = audit_includes(args.ledger)
    for crate_path, st in inc_audit.items():
        print(f"  {crate_path:<22} {st['whole']:>6} {st['included_file']:>10} "
              f"{st['def_only']:>9} {st['excluded']:>9}")
        if st["excluded_files"]:
            for fp in sorted(st["excluded_files"])[:5]:
                print(f"      ! {fp}")
    print()

    per_crate = {}
    for c in crates:
        log = args.out / f"{c}.log"
        if not (args.skip_build and log.exists()):
            run_flux(c, args.out, isolated=args.isolated)
        per_crate[c] = parse_log(log, c)

    print()
    print(f"{'crate':<18} {'procd':>6} {'chk':>5} {'trst':>5} {'ign':>4} {'solved':>7} {'errors':>7} {'wall_s':>8}")
    print("-" * 70)
    for c in crates:
        d = per_crate[c]
        s = d["summary"] or {}
        print(f"{c:<18} {s.get('processed', '?'):>6} {s.get('checked', '?'):>5} "
              f"{s.get('trusted', '?'):>5} {s.get('ignored', '?'):>4} "
              f"{s.get('solved', '?'):>7} {len(d['errors']):>7} "
              f"{s.get('wall_s', '?'):>8}")

    xref = cross_reference(per_crate, args.ledger)
    print()
    print(f"Total Flux errors across all crates: {xref['total_errors']}")
    print(f"Of those, errors that pin to a panic_ledger address (±6 lines): "
          f"{len(xref['matched_addrs'])} unique panic sites")
    print(f"  → {len(xref['matched_addrs'])} of {xref['ledger_count']} ledger sites have an unresolved Flux obligation.")
    print(f"Errors that didn't pin to any ledger site: {len(xref['unmatched_errors'])}")
    if xref["unmatched_errors"]:
        print("  First 10 unmatched errors:")
        for crate, f, l in xref["unmatched_errors"][:10]:
            print(f"    [{crate}] {f}:{l}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
