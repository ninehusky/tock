#!/usr/bin/env python3
"""
panic_stats_md.py — render summary graphs from `panic_sites.md`.

The .md file carries our annotations (status, blockers we've assigned,
notes describing path-forward) — that's the source of truth for "what
we proved" and "what's stuck on what." panic_survey.json is the raw
binary survey and doesn't reflect any of our hand-classification.

Outputs (in tools/):
  panic_stats_status.png      — overall blocker / status distribution
  panic_stats_files.png       — top files, colored by current status
  panic_stats_remaining.png   — of unblocked-not-started rows, what's stuck on what

Run with the venv that has matplotlib:
  tools/.venv/bin/python3 tools/panic_stats_md.py
"""

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


HERE = Path(__file__).resolve().parent
DEFAULT_MD = HERE / "panic_sites.md"
DEFAULT_OUT_DIR = HERE.parent / "docs" / "panic_stats"


# Color palette
COLORS = {
    "locally proven":      "#2ca02c",  # green
    "blocked_cell":        "#1f77b4",  # blue
    "blocked_reentrancy":  "#ff7f0e",  # orange
    "blocked_stdlib":      "#9467bd",  # purple
    "blocked_dyn":         "#d62728",  # red
    "blocked_chips":       "#8c564b",  # brown
    "blocked_arch":        "#e377c2",  # pink
    "blocked_ice":         "#7f7f7f",  # grey
    "actionable":          "#17becf",  # cyan — empty blocker, not started
    "other":               "#bcbd22",
}


def parse_md(path: Path):
    """Parse panic_sites.md table rows into dicts.

    Robust to pipes inside Notes (col 4): splits naively, then collapses
    the middle cells back into Notes so total cell count is exactly 8.
    Schema: addr | flavor | location | source | notes | blockers | status | assignee
    """
    rows = []
    section = None
    with path.open() as f:
        for line in f:
            m = re.match(r"^## (\S+)", line)
            if m:
                section = m.group(1)
                continue
            if not line.startswith("| `0x"):
                continue
            # Strip leading/trailing pipes, split.
            parts = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(parts) < 8:
                continue
            if len(parts) > 8:
                # Extras came from pipes inside Notes (col index 4). Glue them back.
                # Last 3 cols are blockers, status, assignee — keep those at the tail.
                head = parts[:4]                  # addr, flavor, location, source
                tail = parts[-3:]                 # blockers, status, assignee
                middle = parts[4:-3]              # notes possibly fractured
                notes_glued = " | ".join(middle)
                cols = head + [notes_glued] + tail
            else:
                cols = parts
            addr, flavor, location, source, notes, blockers, status, assignee = cols
            rows.append({
                "section": section,
                "addr": addr,
                "flavor": flavor,
                "location": location,
                "source": source,
                "notes": notes,
                "blockers": blockers,
                "status": status,
                "assignee": assignee,
            })
    return rows


def primary_blocker(b: str):
    """Return the single most-meaningful blocker tag from the Blockers column."""
    if not b:
        return ""
    # Strip backticks; take the first token if multiple
    cleaned = b.replace("`", "").strip()
    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    return parts[0] if parts else ""


def category(row):
    """Coarse bucket: locally proven / each blocker tag / actionable / other."""
    if row["status"] == "locally proven":
        return "locally proven"
    pb = primary_blocker(row["blockers"])
    if pb:
        return pb
    if row["status"] == "not started":
        return "actionable"
    return "other"


def remaining_bucket(row):
    """For an actionable row (no blocker, not started), classify by what
    its Notes say is needed. This is by-keyword heuristic."""
    n = row["notes"].lower()
    if "stream" in n or "sresult" in n or "stream::done" in n:
        return "Stream::Done refinement"
    if "valid_output" in n or "slice-output" in n:
        return "valid_output (slice extern spec)"
    if "mpu trait" in n or "mpu refinement" in n:
        return "MPU trait associated refinement"
    if "static mut" in n or "static-mut" in n:
        return "static-mut spec (flux limitation)"
    if "try_into" in n:
        return "try_into refinement chain"
    if "subslicemut" in n or "leasable" in n:
        return "SubSliceMut refinement"
    if "kernel-loop" in n or "kernel loop" in n:
        return "kernel-loop state refinement"
    if "from_raw_parts_mut" in n:
        return "from_raw_parts_mut spec"
    if "ice" in n or "fixpoint_encoding" in n or "infer.rs" in n:
        return "flux ICE"
    return "other / unclassified"


# ---------------------------------------------------------------------------
# Plot 1: overall status / blocker distribution
# ---------------------------------------------------------------------------
def plot_status(rows, out_path):
    cats = Counter(category(r) for r in rows)
    # Order: proven first, then blockers by count, then actionable/other
    order = ["locally proven"]
    blocker_keys = sorted(
        [k for k in cats if k.startswith("blocked_")],
        key=lambda k: -cats[k],
    )
    order.extend(blocker_keys)
    if "actionable" in cats: order.append("actionable")
    if "other" in cats: order.append("other")

    labels = order
    counts = [cats[k] for k in order]
    colors = [COLORS.get(k, "#999") for k in order]

    fig, ax = plt.subplots(figsize=(10, 5.5))
    bars = ax.barh(range(len(labels)), counts, color=colors)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("panic sites")
    ax.set_title(f"panic_sites.md status & blocker distribution "
                 f"({sum(counts)} sites total)")
    for bar, n in zip(bars, counts):
        ax.text(bar.get_width() + max(counts) * 0.005,
                bar.get_y() + bar.get_height() / 2,
                str(n), va="center", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    return cats


# ---------------------------------------------------------------------------
# Plot 2: top files, colored by majority status
# ---------------------------------------------------------------------------
def plot_files(rows, out_path, top_n=25):
    by_file = defaultdict(list)
    for r in rows:
        loc = r["location"]
        # Take whatever comes between [ and ] before the URL  (markdown link form),
        # or the location text directly otherwise.
        m = re.match(r"\[([^\]]+)\]", loc)
        loc_pretty = m.group(1) if m else loc
        # strip line numbers
        loc_pretty = re.sub(r":\d+$", "", loc_pretty)
        by_file[loc_pretty].append(r)
    items = sorted(by_file.items(), key=lambda kv: -len(kv[1]))[:top_n]

    labels, counts, colors = [], [], []
    for f, rs in items:
        labels.append(f if len(f) <= 60 else f"...{f[-57:]}")
        counts.append(len(rs))
        # Color by majority category
        cat_counts = Counter(category(r) for r in rs)
        top_cat = cat_counts.most_common(1)[0][0]
        colors.append(COLORS.get(top_cat, "#999"))

    fig_h = max(4.0, 0.32 * len(items) + 1.5)
    fig, ax = plt.subplots(figsize=(12, fig_h))
    bars = ax.barh(range(len(items)), counts, color=colors)
    ax.set_yticks(range(len(items)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("panic sites")
    ax.set_title(f"Top {len(items)} files by panic-site count, colored by majority status")
    for bar, n in zip(bars, counts):
        ax.text(bar.get_width() + max(counts) * 0.005,
                bar.get_y() + bar.get_height() / 2,
                str(n), va="center", fontsize=8)

    # Legend
    used_cats = sorted({category(r) for f, rs in items for r in rs}, key=lambda k: -sum(1 for r in rows if category(r) == k))
    handles = [plt.Rectangle((0, 0), 1, 1, color=COLORS.get(c, "#999")) for c in used_cats]
    ax.legend(handles, used_cats, title="majority status", loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Plot 3: of actionable rows, what we're stuck on
# ---------------------------------------------------------------------------
def plot_remaining(rows, out_path):
    """Filtered to unassigned (our work) — Cole's open rows excluded."""
    actionable = [r for r in rows
                  if category(r) == "actionable" and r["assignee"] == ""]
    cats = Counter(remaining_bucket(r) for r in actionable)
    items = sorted(cats.items(), key=lambda kv: -kv[1])
    labels = [k for k, _ in items]
    counts = [n for _, n in items]
    colors = ["#17becf"] * len(items)

    fig, ax = plt.subplots(figsize=(11, 0.5 * len(items) + 2.5))
    bars = ax.barh(range(len(items)), counts, color=colors)
    ax.set_yticks(range(len(items)))
    ax.set_yticklabels(labels, fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("rows")
    ax.set_title(f"Of {len(actionable)} actionable + unassigned rows, what's stuck on what")
    for bar, n in zip(bars, counts):
        ax.text(bar.get_width() + 0.05,
                bar.get_y() + bar.get_height() / 2,
                str(n), va="center", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    return cats


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--md", default=DEFAULT_MD, type=Path)
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR, type=Path)
    args = ap.parse_args()

    if not args.md.exists():
        print(f"ERROR: {args.md} not found.", file=sys.stderr)
        return 1

    rows = parse_md(args.md)
    print(f"Loaded {len(rows)} rows from {args.md}\n")

    status_path = args.out_dir / "panic_stats_status.png"
    files_path = args.out_dir / "panic_stats_files.png"
    remaining_path = args.out_dir / "panic_stats_remaining.png"

    cats = plot_status(rows, status_path)
    print("=== Overall status / blocker distribution ===")
    for k, v in sorted(cats.items(), key=lambda kv: -kv[1]):
        print(f"  {v:>4}  {k}")
    print(f"  → {status_path}\n")

    plot_files(rows, files_path)
    print(f"  → {files_path}\n")

    print("=== Of actionable rows, what's stuck on what ===")
    rcats = plot_remaining(rows, remaining_path)
    for k, v in sorted(rcats.items(), key=lambda kv: -kv[1]):
        print(f"  {v:>3}  {k}")
    print(f"  → {remaining_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
