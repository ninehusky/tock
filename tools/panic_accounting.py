#!/usr/bin/env python3
"""
panic_accounting.py — census of panic-site dispositions as they actually exist
in the branch source, plus an integrity audit against the 343-site ledger.

Two views:

  (1) SOURCE CENSUS  — a direct count of the FLUX-* disposition markers present
      in the source tree, grouped by disposition. This is 1-to-1 with the code
      by construction: it counts what is literally written in the .rs files.
        annotated   : FLUX-TODO with a live/explicit assert (documented, open)
        blocked     : FLUX-TODO-BLOCKED, `// Notes: blocked-*`, or a commented-out assert
        discharged  : FLUX-OPT (Flux proves it)
        actionable  : `// Notes: actionable` (known fix, deferred)

  (2) LEDGER AUDIT   — attempts to reconcile those markers with the 343-site
      ledger (tools/panic_ledger.csv) by address and by (file,line,flavor).
      Both joins currently FAIL: the ledger was built against master
      @104a47788, while the source markers carry addresses/lines from an older
      survey snapshot (and the branch refactored some sites away). This view
      exists to *quantify* that gap, not to hide it. A true 343-aligned
      accounting requires re-running tools/panic_survey.py against the current
      branch binary so addresses/lines are re-derived from the actual code.

Run:  tools/.venv/bin/python3 tools/panic_accounting.py [--png]
"""

import argparse
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

LEDGER = Path("tools/panic_ledger.csv")
CRATE_ROOTS = ["kernel", "libraries/tickv", "capsules", "chips", "arch"]

MARKER_RE = re.compile(r"//\s*(FLUX-OPT|FLUX-TODO-FN-LEVEL|FLUX-TODO-BLOCKED|FLUX-TODO)\b")
NOTES_RE  = re.compile(r"Notes:\s*([a-z][a-z0-9-]*)")
ADDR_RE   = re.compile(r"0x[0-9a-fA-F]+")
LINE_RE   = re.compile(r"line=(\d+)")
FLAVOR_RE = re.compile(r"flavor=([a-z_]+)")

NON_SOURCE_STATUSES = {
    "singleton-helper", "singleton-monomorph-helper", "removed-on-branch",
}


def iter_marker_blocks():
    """Yield (file, line_no, kind, addrs, lines=, flavors, notes, assert_state)
    for every FLUX-* marker block in the source."""
    for root in CRATE_ROOTS:
        for p in Path(root).rglob("*.rs"):
            lines = p.read_text(errors="replace").splitlines()
            i = 0
            while i < len(lines):
                m = MARKER_RE.search(lines[i])
                if not m:
                    i += 1
                    continue
                kind = m.group(1)
                addrs, line_tags, flavors = set(), set(), set()
                notes = None
                j = i
                while j < len(lines) and lines[j].lstrip().startswith("//"):
                    addrs.update(a.lower() for a in ADDR_RE.findall(lines[j]))
                    line_tags.update(LINE_RE.findall(lines[j]))
                    flavors.update(FLAVOR_RE.findall(lines[j]))
                    nm = NOTES_RE.search(lines[j])
                    if nm and notes is None:
                        notes = nm.group(1)
                    j += 1
                assert_state = None
                for k in range(j, min(j + 4, len(lines))):
                    s = lines[k].strip()
                    if "flux_support::assert" in s:
                        assert_state = "commented" if s.startswith("//") else "live"
                        break
                yield (str(p), i + 1, kind, addrs, line_tags, flavors, notes, assert_state)
                i = j


def disposition(kind, notes, assert_state):
    if kind == "FLUX-OPT":
        return "discharged"
    if kind == "FLUX-TODO-BLOCKED":
        return "blocked"
    if notes and notes.startswith("blocked"):
        return "blocked"
    if notes == "actionable":
        return "actionable"
    if assert_state == "commented":
        return "blocked"
    return "annotated"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--png", action="store_true")
    args = ap.parse_args()

    blocks = list(iter_marker_blocks())

    # ---- (1) SOURCE CENSUS ----
    census = Counter(disposition(b[2], b[6], b[7]) for b in blocks)
    print("=== (1) SOURCE CENSUS — disposition markers in the branch source ===")
    print("    (1-to-1 with the code: a direct count of what's in the .rs files)\n")
    order = ["annotated", "blocked", "discharged", "actionable"]
    for k in order:
        print(f"  {census.get(k, 0):>4}  {k}")
    print(f"  ----")
    print(f"  {sum(census.values()):>4}  total marker sites")

    # ---- (2) LEDGER AUDIT ----
    rows = list(csv.DictReader(open(LEDGER)))
    n_nonsrc = sum(1 for r in rows if r["status"] in NON_SOURCE_STATUSES)
    led_addr = {r["address"].lower() for r in rows}
    led_fl = defaultdict(list)
    for r in rows:
        f = (r["final_file"] or r["ef_file"] or "").lstrip("./")
        led_fl[(Path(f).name if f else "", r["final_line"], r["flavor"])].append(r)

    src_addr = set().union(*(b[3] for b in blocks)) if blocks else set()
    addr_overlap = len(led_addr & src_addr)

    fl_keys = []
    for f, ln, kind, addrs, line_tags, flavors, notes, _ in (
        (b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7]) for b in blocks
    ):
        base = Path(f).name
        for lt in line_tags:
            for fl in flavors:
                fl_keys.append((base, lt, fl))
    fl_hit = sum(1 for k in set(fl_keys) if k in led_fl)

    print("\n=== (2) LEDGER AUDIT — reconciliation with the 343-site ledger ===")
    print(f"  ledger sites: {len(rows)}  ({n_nonsrc} non-source, {len(rows)-n_nonsrc} should have a marker)")
    print(f"  by ADDRESS join:        {addr_overlap}/{len(led_addr)} ledger addrs found in source markers")
    print(f"  by (file,line,flavor):  {fl_hit}/{len(set(fl_keys))} marker keys hit a ledger key")
    print("  → NOT 1-to-1. Ledger addresses come from the master @104a47788 build;")
    print("    source-marker addresses come from an older survey snapshot.")
    print("    A 343-aligned accounting needs a fresh tools/panic_survey.py run")
    print("    against the current branch binary (re-derives addrs+lines from code).")

    if args.png:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        labels = [k for k in order if census.get(k)]
        vals = [census[k] for k in labels]
        colors = {"annotated": "#bdbdbd", "blocked": "#d62728",
                  "discharged": "#2ca02c", "actionable": "#ff7f0e"}
        fig, ax = plt.subplots(figsize=(9, 4.5))
        bars = ax.barh(range(len(labels)), vals, color=[colors[k] for k in labels])
        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(labels)
        ax.invert_yaxis()
        ax.set_title(f"Panic-site disposition — branch source census "
                     f"({sum(vals)} marker sites)")
        for b, n in zip(bars, vals):
            ax.text(b.get_width() + 1, b.get_y() + b.get_height() / 2, str(n), va="center")
        fig.tight_layout()
        out = Path("tools/panic_accounting.png")
        fig.savefig(out, dpi=120)
        print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
