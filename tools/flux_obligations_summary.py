#!/usr/bin/env python3
"""flux_obligations_summary.py — quick "what's left to prove" summary.

Reads tools/flux_obligations.json (produced by flux_obligations.py) and prints
the remaining Flux obligations bucketed by kind, plus what we can trust and what
is not being checked. Stdlib-only, no Flux run — just aggregates the committed
census, so it is instant.

Usage:
    python3 tools/flux_obligations_summary.py
    python3 tools/flux_obligations_summary.py --json other_census.json
    python3 tools/flux_obligations_summary.py --md   # GitHub-flavored markdown
"""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Re-bucket from the raw Flux message (independent of the stored `kind`, so this
# stays correct even if flux_obligations.py's bucketer changes).
def kind_of(msg: str) -> str:
    m = msg.lower()
    if "not included when checking external crate" in m or "external crate" in m:
        return "xcrate"            # measurement artifact, not a real obligation
    if "out-of-bounds" in m or "out of bounds" in m:
        return "assert_oob"
    if "division by zero" in m or "remainder with a divisor of zero" in m or "modulo" in m:
        return "assert_divzero"
    if "assertion might fail" in m:
        return "assert_other"
    if "overflow" in m:
        return "overflow"
    if "type invariant" in m:
        return "type_invariant"
    if "refinement type error" in m:
        return "refinement"        # sig / pre- & post-condition mismatches
    return "other"

LABEL = {
    "refinement":     "refinement-type errors (sig / pre- & post-conditions)",
    "assert_oob":     "asserts: possible out-of-bounds index",
    "assert_divzero": "asserts: possible division / remainder by zero",
    "assert_other":   "asserts: other `assertion might fail`",
    "overflow":       "arithmetic overflow",
    "type_invariant": "type-invariant may not hold",
    "other":          "other",
    "xcrate":         "cross-crate resolution (measurement artifact, not real)",
}
REAL_KINDS = [k for k in LABEL if k != "xcrate"]


def load(p: Path):
    d = json.loads(p.read_text())
    return d["crates"], d.get("summary", {})


def summarize(crates):
    kinds = Counter()
    per_crate = {}
    for pkg, r in crates.items():
        ck = Counter(kind_of(o["message"]) for o in r["obligations"])
        per_crate[pkg] = (r["health"], ck, len(r.get("ice_trusted_fns", [])))
        kinds.update(ck)
    return kinds, per_crate


def render(crates, md=False):
    kinds, per_crate = summarize(crates)
    real = sum(kinds[k] for k in REAL_KINDS)
    art = kinds.get("xcrate", 0)
    ice_fns = sum(t[2] for t in per_crate.values())
    scoped = [p for p, (h, *_ ) in per_crate.items() if h in ("elaboration_ice", "ice_no_loc")]
    masked = [p for p, (h, ck, _) in per_crate.items()
              if h == "clean" and sum(ck.values()) == 0 and p in ("nrf52840dk", "nrf52840")]
    L = []
    h2 = (lambda s: L.append(f"\n## {s}\n")) if md else (lambda s: L.append(f"\n{s}\n" + "-" * len(s)))
    L.append(("# " if md else "") + "Flux obligation census — what's left to prove")
    L.append(f"\n**{real} real obligations** to discharge "
             f"(+{art} cross-crate artifacts, excluded).")

    h2("Remaining, by kind")
    if md:
        L.append("| kind | count |\n|---|---:|")
        for k in REAL_KINDS:
            if kinds[k]:
                L.append(f"| {LABEL[k]} | {kinds[k]} |")
    else:
        for k in REAL_KINDS:
            if kinds[k]:
                L.append(f"  {kinds[k]:4}  {LABEL[k]}")

    h2("What we can trust vs what's not checked")
    L.append(f"- can trust (Flux completes & checks): crates with health=clean")
    L.append(f"- blind spots — ICE-trusted fns (their obligations hidden): {ice_fns}")
    L.append(f"- whole-crate blocked, measured from scoped floor: {scoped or 'none'}")
    L.append(f"- masked downstream (cortex-m rty:813 ICE): {masked or 'none'}")

    h2("Per crate")
    if md:
        L.append("| crate | health | obligations |\n|---|---|---:|")
    for pkg, (health, ck, _) in per_crate.items():
        realn = sum(ck[k] for k in REAL_KINDS)
        bits = ", ".join(f"{ck[k]} {k.split('_')[-1]}" for k in REAL_KINDS if ck[k]) or "—"
        if md:
            L.append(f"| {pkg} | {health} | {realn} ({bits}) |")
        else:
            L.append(f"  {pkg:<16} {health:<16} {realn:4}  ({bits})")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", type=Path, default=ROOT / "tools" / "flux_obligations.json")
    ap.add_argument("--md", action="store_true", help="emit GitHub-flavored markdown")
    args = ap.parse_args()
    crates, _ = load(args.json)
    print(render(crates, md=args.md))


if __name__ == "__main__":
    main()
