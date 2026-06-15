#!/usr/bin/env python3
"""
check_survey_fresh.py — guard against a stale committed panic_survey.json.

The downstream pipeline (reannotate_flux.py, check_invariant1/2.py) all read the
*committed* tools/panic_survey.json. If source changes (e.g. removing a
`#[flux_rs::trusted]` line, or a real bug-fix that deletes a panic site) without
regenerating the survey, those tools silently audit against a stale binary map.

This checker compares two surveys on an **address-independent projection** —
the multiset of (file, line, flavor, source) panic sites. Absolute `address`
values legitimately drift across hosts/toolchains (a benign rebuild relocates
the whole binary), so they are deliberately ignored; what must stay in sync is
the source-relative set of panic sites.

Usage (CI):
    python3 tools/panic_survey.py --out /tmp/fresh_survey.json \
        --objdump arm-none-eabi-objdump --addr2line arm-none-eabi-addr2line
    python3 tools/check_survey_fresh.py tools/panic_survey.json /tmp/fresh_survey.json

Exits 0 if the projections match, 1 (with a diff) if the committed survey is
stale and must be regenerated + committed.
"""

import json
import sys
from collections import Counter


def load_sites(path: str) -> list:
    raw = json.load(open(path))
    recs = raw if isinstance(raw, list) else (
        raw.get("records") or raw.get("sites")
        or next(v for v in raw.values() if isinstance(v, list))
    )
    return recs


def project(recs: list) -> Counter:
    """Address-independent fingerprint of the panic sites."""
    out = Counter()
    for r in recs:
        ef = r["effective_frame"]
        out[(ef["file"], ef["line"], r["sink_flavor"],
             (r.get("effective_source") or "").strip())] += 1
    return out


def fmt(key) -> str:
    f, line, flavor, src = key
    return f"  {f}:{line} [{flavor}] {src[:70]}"


def sort_key(key):
    """Stable ordering that tolerates None fields (e.g. line=None)."""
    f, line, flavor, src = key
    return (f or "", line if line is not None else -1, flavor or "", src or "")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: check_survey_fresh.py <committed.json> <fresh.json>",
              file=sys.stderr)
        return 2
    committed_path, fresh_path = sys.argv[1], sys.argv[2]
    committed = project(load_sites(committed_path))
    fresh = project(load_sites(fresh_path))

    if committed == fresh:
        print(f"OK: {committed_path} is fresh "
              f"({sum(committed.values())} panic sites match the current source).")
        return 0

    only_committed = committed - fresh   # in committed, not produced by current source
    only_fresh = fresh - committed       # produced by current source, missing from committed

    print(f"STALE: {committed_path} does not match the current source.\n")
    if only_fresh:
        print(f"--- {sum(only_fresh.values())} site(s) in the current source but "
              f"MISSING from the committed survey (uncovered panics) ---")
        for k in sorted(only_fresh.elements(), key=sort_key):
            print(fmt(k))
    if only_committed:
        print(f"\n--- {sum(only_committed.values())} site(s) in the committed survey "
              f"but GONE from the current source (removed/relocated panics) ---")
        for k in sorted(only_committed.elements(), key=sort_key):
            print(fmt(k))
    print("\nRegenerate and commit the survey:\n"
          "    python3 tools/panic_survey.py\n"
          "    git add tools/panic_survey.json")
    return 1


if __name__ == "__main__":
    sys.exit(main())
