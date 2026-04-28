#!/usr/bin/env python3
"""
migrate_blocker_to_blockers.py — one-shot conversion of panic_survey.json from
single-blocker (`blocker: str`) to multi-blocker (`blockers: list[str]`).

Idempotent: sites already carrying `blockers` are left alone.
"""

import argparse
import json
import sys
from pathlib import Path


def migrate_site(site: dict) -> str:
    """Mutate site in place. Return one of 'already', 'converted_empty',
    'converted_one'."""
    if "blockers" in site:
        # Already migrated. Drop any stale `blocker` field, keep going.
        site.pop("blocker", None)
        return "already"

    raw = site.pop("blocker", "")
    if raw:
        site["blockers"] = [raw]
        return "converted_one"
    site["blockers"] = []
    return "converted_empty"


def main() -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", default=here / "panic_survey.json", type=Path)
    args = ap.parse_args()

    if not args.json.exists():
        print(f"ERROR: {args.json} not found.", file=sys.stderr)
        return 1

    doc = json.loads(args.json.read_text())
    counts = {"already": 0, "converted_empty": 0, "converted_one": 0}
    for s in doc["sites"]:
        counts[migrate_site(s)] += 1

    args.json.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"Migrated {args.json}:")
    print(f"  already on new schema: {counts['already']}")
    print(f"  converted (had blocker): {counts['converted_one']}")
    print(f"  converted (no blocker):  {counts['converted_empty']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
