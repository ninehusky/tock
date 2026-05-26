#!/usr/bin/env python3
"""
panic_stats.py — render summary graphs of panic_survey.json.

Produces three PNGs in tools/:
  panic_stats_files.png    — top files by panic count
  panic_stats_flavors.png  — distribution by sink_flavor
  panic_stats_blockers.png — distribution by exclusive blocker set

Also prints text summaries to stdout.

Run with the venv:
  tools/.venv/bin/python3 tools/panic_stats.py
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# Discrete color palette — tab10-ish, ordered for readability.
COLOR_NONE  = "#bdbdbd"   # grey: untouched / "(none)"
COLOR_CELL  = "#1f77b4"   # blue
COLOR_DYN   = "#d62728"   # red
COLOR_REENT = "#2ca02c"   # green
COLOR_MULTI = "#9467bd"   # purple
COLOR_OTHER = "#7f7f7f"   # dark grey

# Module-bucket palette (used for the file plot).
MODULE_COLORS = {
    "capsules/core":  "#1f77b4",
    "capsules/extra": "#ff7f0e",
    "kernel":         "#2ca02c",
    "chips":          "#d62728",
    "arch":           "#9467bd",
    "boards":         "#8c564b",
    "libraries":      "#e377c2",
    "stdlib":         "#7f7f7f",
    "other":          "#bcbd22",
    "unknown":        "#17becf",
}


def site_blockers(s):
    """Sorted-deduped list view of the site's blockers field. Tolerant of
    legacy single-blocker schema."""
    if "blockers" in s:
        return sorted(set(s["blockers"]))
    legacy = s.get("blocker") or ""
    return [legacy] if legacy else []


def shorten_path(p, max_segments=3):
    """Take last N path components (drop leading './' and 'src')."""
    if not p:
        return "<unknown>"
    parts = [x for x in p.split("/") if x and x != "." and x != "src"]
    if len(parts) > max_segments:
        parts = parts[-max_segments:]
    return "/".join(parts)


def color_for_blocker_set(tup):
    if not tup:
        return COLOR_NONE
    if len(tup) > 1:
        return COLOR_MULTI
    b = tup[0]
    return {
        "blocked_cell":        COLOR_CELL,
        "blocked_dyn":         COLOR_DYN,
        "blocked_reentrancy":  COLOR_REENT,
    }.get(b, COLOR_OTHER)


# ---------------------------------------------------------------------------
# Plot 1: top files by panic count
# ---------------------------------------------------------------------------

def plot_files(sites, out_path, top_n=25):
    by_file = Counter()
    by_file_module = {}
    for s in sites:
        eff = s.get("effective_frame") or {}
        f = eff.get("file") or "<unknown>"
        by_file[f] += 1
        by_file_module[f] = s.get("module_bucket") or "unknown"

    items = by_file.most_common(top_n)
    labels = [shorten_path(f) for f, _ in items]
    counts = [n for _, n in items]
    colors = [MODULE_COLORS.get(by_file_module[f], "#999999") for f, _ in items]

    fig_h = max(4.0, 0.32 * len(items) + 1.5)
    fig, ax = plt.subplots(figsize=(10, fig_h))
    bars = ax.barh(range(len(items)), counts, color=colors)
    ax.set_yticks(range(len(items)))
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("panic sites")
    ax.set_title(f"Top {len(items)} files by panic-site count "
                 f"({len(by_file)} files total, {sum(by_file.values())} sites)")
    for bar, n in zip(bars, counts):
        ax.text(bar.get_width() + max(counts) * 0.01,
                bar.get_y() + bar.get_height() / 2,
                str(n), va="center", fontsize=8)

    # Module legend
    used_modules = sorted({by_file_module[f] for f, _ in items})
    handles = [plt.Rectangle((0, 0), 1, 1, color=MODULE_COLORS.get(m, "#999999"))
               for m in used_modules]
    ax.legend(handles, used_modules, title="module", loc="lower right", fontsize=8)

    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    return items


# ---------------------------------------------------------------------------
# Plot 2: sink flavor distribution
# ---------------------------------------------------------------------------

def plot_flavors(sites, out_path):
    by_flavor = Counter()
    by_flavor_blocked = Counter()
    for s in sites:
        f = s.get("sink_flavor") or "<unknown>"
        by_flavor[f] += 1
        if site_blockers(s):
            by_flavor_blocked[f] += 1

    items = by_flavor.most_common()
    labels = [f for f, _ in items]
    totals = [n for _, n in items]
    blocked_counts = [by_flavor_blocked.get(f, 0) for f in labels]

    fig, ax = plt.subplots(figsize=(10, 6))
    y = range(len(items))
    ax.barh(y, totals, color="#cfd8dc", label="no blocker")
    ax.barh(y, blocked_counts, color="#1f77b4", label="≥1 blocker")
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("panic sites")
    ax.set_title(f"Sites by sink_flavor "
                 f"({sum(totals)} sites total)")
    for i, (n, bn) in enumerate(zip(totals, blocked_counts)):
        ax.text(n + max(totals) * 0.01, i, f"{n}  ({bn} blocked)",
                va="center", fontsize=8)
    ax.legend(loc="lower right", fontsize=8)

    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    return items, blocked_counts


# ---------------------------------------------------------------------------
# Plot 3: blocker set distribution (exclusive + any-of)
# ---------------------------------------------------------------------------

def plot_blockers(sites, out_path):
    exclusive = Counter()
    any_of = Counter()
    untouched = 0
    for s in sites:
        bs = tuple(site_blockers(s))
        exclusive[bs] += 1
        if not bs and not s.get("notes"):
            untouched += 1
        for b in bs:
            any_of[b] += 1

    # Render: stacked rows of exclusive (left chart) and any-of (right chart).
    fig, (ax_excl, ax_any) = plt.subplots(1, 2, figsize=(14, 5))

    # Exclusive
    excl_items = sorted(exclusive.items(),
                        key=lambda kv: (kv[0] == (), -kv[1]))
    excl_labels = ["+".join(t) if t else "(none)" for t, _ in excl_items]
    excl_counts = [n for _, n in excl_items]
    excl_colors = [color_for_blocker_set(t) for t, _ in excl_items]

    bars = ax_excl.barh(range(len(excl_items)), excl_counts, color=excl_colors)
    ax_excl.set_yticks(range(len(excl_items)))
    ax_excl.set_yticklabels(excl_labels, fontsize=9)
    ax_excl.invert_yaxis()
    ax_excl.set_xlabel("sites")
    ax_excl.set_title(f"Exclusive blocker set\n(untouched: {untouched}; "
                      f"sites with notes-only: "
                      f"{exclusive.get((), 0) - untouched})",
                      fontsize=10)
    for bar, n in zip(bars, excl_counts):
        ax_excl.text(bar.get_width() + max(excl_counts) * 0.01,
                     bar.get_y() + bar.get_height() / 2,
                     str(n), va="center", fontsize=8)

    # Any-of (only the three blocker keys)
    any_keys = ["blocked_cell", "blocked_dyn", "blocked_reentrancy"]
    any_counts = [any_of.get(k, 0) for k in any_keys]
    any_colors = [COLOR_CELL, COLOR_DYN, COLOR_REENT]
    bars = ax_any.barh(range(len(any_keys)), any_counts, color=any_colors)
    ax_any.set_yticks(range(len(any_keys)))
    ax_any.set_yticklabels(any_keys, fontsize=9)
    ax_any.invert_yaxis()
    ax_any.set_xlabel("sites")
    ax_any.set_title("Any-of (blocker appears in any combination)", fontsize=10)
    for bar, n in zip(bars, any_counts):
        ax_any.text(bar.get_width() + max(any_counts + [1]) * 0.01,
                    bar.get_y() + bar.get_height() / 2,
                    str(n), va="center", fontsize=8)

    fig.suptitle(f"Sites by blocker labeling ({len(sites)} total)", fontsize=12)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    return exclusive, any_of, untouched


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", default=here / "panic_survey.json", type=Path)
    ap.add_argument("--top-files", type=int, default=25,
                    help="how many top files to plot (default 25)")
    ap.add_argument("--out-dir", default=here, type=Path,
                    help="where to write PNGs (default tools/)")
    args = ap.parse_args()

    if not args.json.exists():
        print(f"ERROR: {args.json} not found.", file=sys.stderr)
        return 1
    doc = json.loads(args.json.read_text())
    sites = doc["sites"]
    print(f"Loaded {len(sites)} sites from {args.json}")
    print()

    files_path    = args.out_dir / "panic_stats_files.png"
    flavors_path  = args.out_dir / "panic_stats_flavors.png"
    blockers_path = args.out_dir / "panic_stats_blockers.png"

    print(f"=== Top {args.top_files} files by panic count ===")
    file_items = plot_files(sites, files_path, top_n=args.top_files)
    for f, n in file_items[:15]:
        print(f"  {n:4d}  {f}")
    if len(file_items) > 15:
        print(f"  ... +{len(file_items) - 15} more in the chart")
    print(f"  → {files_path}")
    print()

    print("=== Sites by sink_flavor ===")
    flavor_items, blocked_per = plot_flavors(sites, flavors_path)
    for (f, n), bn in zip(flavor_items, blocked_per):
        print(f"  {n:4d}  {f:<24}  ({bn} have ≥1 blocker)")
    print(f"  → {flavors_path}")
    print()

    print("=== Sites by blocker labeling ===")
    exclusive, any_of, untouched = plot_blockers(sites, blockers_path)
    print(f"  untouched (no blockers, no notes):  {untouched}")
    print()
    print("  Exclusive blocker set (site has exactly this set):")
    for tup, n in sorted(exclusive.items(), key=lambda kv: (kv[0] == (), -kv[1])):
        label = "+".join(tup) if tup else "(none)"
        print(f"    {n:4d}  {label}")
    print()
    print("  Any-of (this blocker appears, possibly with others):")
    for k in ("blocked_cell", "blocked_dyn", "blocked_reentrancy"):
        print(f"    {any_of.get(k, 0):4d}  {k}")
    print(f"  → {blockers_path}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
