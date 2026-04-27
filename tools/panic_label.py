#!/usr/bin/env python3
"""
panic_label.py — assign Flux `blockers` labels to each panic site in
panic_survey.json.

The only two things that matter per site are:
  1. `sink` / `sink_flavor`  (already derived by panic_survey.py)
     — what panic function gets called.
  2. `blockers`  (assigned here, sorted+deduped list)
     — which Flux features are needed to eliminate this panic. A site can
       carry more than one blocker when several features are co-required.
        `blocked_cell`        Flux needs to prove a non-invariant precondition on a Cell<T>.
        `blocked_dyn`         Flux needs to prove a precondition that crosses a `dyn T` boundary.
        `blocked_reentrancy`  Flux needs to prove grant re-entrancy freedom.
        []                    (empty) no Flux feature needed; site can in principle be eliminated.

Everything else (intentional panics, upstream panics, library panics, etc.)
leaves `blockers` empty. Aggregate analysis happens downstream via jq/stats.

Modes:
  --apply-rules   Run the one auto-rule (grant re-entrancy → blocked_reentrancy).
                  Empirically sound; re-running is idempotent.
  --review        Interactive walker over sites with empty `blockers` and empty
                  `notes`. Accepts a compound c/d/r string per site (e.g. `cd`
                  for cell+dyn); resume-safe (flushes every answer).
  --stats         Print exclusive vs any-of blocker distributions.
  --report        Emit a markdown digest grouped by exclusive blocker set.
"""

import argparse
import json
import sys
from pathlib import Path

BLOCKERS = ["blocked_cell", "blocked_dyn", "blocked_reentrancy"]

REVIEW_KEYS = {
    "c": "blocked_cell",
    "d": "blocked_dyn",
    "r": "blocked_reentrancy",
}

CONTROL_KEYS = {"", "n", "b", "q", "?"}

# Rust v0 mangling prefixes for trait-impl-method functions.
# `_RNvX...` and `_RNvY...` indicate <Concrete as Trait>::method.
TRAIT_IMPL_PREFIXES = ("_RNvX", "_RNvY")


def is_trait_impl_label(label: str) -> bool:
    return label.startswith(TRAIT_IMPL_PREFIXES)


def normalize_blockers(items) -> list[str]:
    """Sorted, deduped, stripped of unknown values."""
    return sorted({b for b in items if b in BLOCKERS})


def site_blockers(site: dict) -> list[str]:
    """Return the site's blockers list, normalizing on the fly. Tolerates the
    legacy single-blocker schema if encountered (caller probably hasn't run
    the migration script yet)."""
    if "blockers" in site:
        return normalize_blockers(site["blockers"])
    legacy = site.get("blocker") or ""
    return [legacy] if legacy in BLOCKERS else []


# ---------------------------------------------------------------------------
# Auto-rule: grant re-entrancy
# ---------------------------------------------------------------------------

def is_grant_reentrancy(site: dict) -> bool:
    eff = site.get("effective_frame") or {}
    eff_file = eff.get("file") or ""
    if "kernel/src/grant.rs" not in eff_file:
        return False
    # Sink is either panic_fmt (panic!("Attempted to re-enter a grant region."))
    # or unwrap_failed (self.access_grant(fun, true).unwrap()). Both fire from
    # the re-entry guard at line 1421 / 1244.
    return site.get("sink_flavor") in ("explicit_panic", "unwrap_option")


def apply_rules(doc: dict) -> dict:
    """Additive-only: add `blocked_reentrancy` to grant-routed sites that
    don't already have it. Never removes anything — manual labels (including
    `blocked_reentrancy` on non-grant sites the user judged reentrancy-prone)
    are preserved. Idempotent."""
    grant_assigned = 0
    untouched = 0
    for site in doc["sites"]:
        # Normalize storage: always have a sorted-deduped list.
        site["blockers"] = site_blockers(site)
        site.pop("blocker", None)
        site.setdefault("notes", "")

        before = set(site["blockers"])
        if is_grant_reentrancy(site):
            after = before | {"blocked_reentrancy"}
            if after != before:
                grant_assigned += 1
            else:
                untouched += 1
        else:
            after = before
            untouched += 1
        site["blockers"] = normalize_blockers(after)
    return {
        "grant_assigned": grant_assigned,
        "untouched": untouched,
    }


# ---------------------------------------------------------------------------
# Interactive walker
# ---------------------------------------------------------------------------

KEY_HINT = "[c/d/r — combine: 'cd' = cell+dyn  |  n=next  b=back  ?=help  q=quit  (append ' : notes')]"


def print_review_help():
    print()
    print("Blocker keys (combine in any order; sorted+deduped on save):")
    for k, label in REVIEW_KEYS.items():
        print(f"  {k}     {label}")
    print("  e.g.  cd    → blocked_cell + blocked_dyn")
    print("        cdr   → all three")
    print()
    print("Control:")
    print("  n     next — leave blockers empty, move on")
    print("  b     back — re-show previous site")
    print("  ?     reprint this help")
    print("  q     save and quit")
    print()
    print("Append ` : note text` to attach notes, e.g. `c : self.buffer is a TakeCell`")
    print("Notes alone (e.g. `n : reconsider after Cell support lands`) mark the site as 'seen' so it won't reappear.")
    print()


def read_source_context(file_path: str, line, repo_root: Path, span: int = 5):
    if not file_path or line is None:
        return []
    candidates = []
    p = Path(file_path)
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.append(repo_root / file_path.lstrip('./'))
        candidates.append(Path(file_path))
    for cand in candidates:
        if cand.exists():
            try:
                lines = cand.read_text(errors='replace').splitlines()
            except OSError:
                return []
            start = max(1, line - span)
            end = min(len(lines), line + span)
            out = []
            for i in range(start, end + 1):
                marker = ">>" if i == line else "  "
                out.append(f"{marker} {i:5d}  {lines[i - 1]}")
            return out
    return []


def present_site(site: dict, repo_root: Path, idx: int, total: int):
    print("=" * 80)
    print(f"Site {idx + 1}/{total}   {site['address']}   "
          f"sink_flavor={site['sink_flavor']}   "
          f"module={site['module_bucket']}   origin={site['origin_bucket']}")
    eff = site.get("effective_frame", {}) or {}
    eff_file = eff.get("file") or "<unknown>"
    eff_line = eff.get("line")
    print(f"enclosing: {site.get('enclosing_asm_label','')}")
    print(f"effective: {eff_file}:{eff_line}")
    src = site.get("effective_source") or ""
    if src:
        print(f"source:    {src}")
    cur = site_blockers(site)
    if cur:
        print(f"current:   blockers={cur}")
    if site.get("notes"):
        print(f"notes:     {site['notes']}")
    print()
    ctx = read_source_context(eff_file, eff_line, repo_root)
    if ctx:
        for line in ctx:
            print(line)
    else:
        print("  (no source context available)")
    print()


def parse_command(raw: str):
    """Return (kind, payload, notes) where:
      kind == "control"  → payload is one of "" / "n" / "b" / "q" / "?"
      kind == "blockers" → payload is a sorted-deduped list of blocker strings
      kind == "error"    → payload is a human-readable error message
    `notes` is the optional free-form string after `:` (or None).
    """
    raw = raw.strip()
    notes = None
    if ":" in raw:
        keypart, notes = raw.split(":", 1)
        keypart = keypart.strip().lower()
        notes = notes.strip()
    else:
        keypart = raw.lower()

    if keypart in CONTROL_KEYS:
        return "control", keypart, notes

    if keypart and all(c in REVIEW_KEYS for c in keypart):
        chosen = sorted({REVIEW_KEYS[c] for c in set(keypart)})
        return "blockers", chosen, notes

    return "error", f"unrecognized input '{keypart}'", notes


def review(doc: dict, repo_root: Path, out_path: Path, only_module, filter_mode):
    sites = doc["sites"]
    for s in sites:
        # Migrate-on-load if the file still has the legacy single-blocker schema.
        s["blockers"] = site_blockers(s)
        s.pop("blocker", None)
        s.setdefault("notes", "")

    def residual_indices():
        out = []
        for i, s in enumerate(sites):
            label = s.get("enclosing_asm_label") or ""
            if filter_mode == "trait-impl":
                # Re-walk sites in trait-impl methods that haven't been
                # considered for dyn yet. INCLUDES sites with existing blockers
                # or notes — the point is re-examination. Excludes sites whose
                # blockers contain blocked_dyn (already considered) or
                # blocked_reentrancy-only (panic-at-grant-entry; dyn isn't
                # relevant for the reentry guard).
                if not is_trait_impl_label(label):
                    continue
                blockers = set(s["blockers"])
                if "blocked_dyn" in blockers:
                    continue
                if blockers == {"blocked_reentrancy"}:
                    continue
            else:
                # Default: skip if the site already has a blocker decision OR a
                # note — notes-bearing sites count as "seen" so they don't
                # reappear on resume.
                if s["blockers"] or s.get("notes"):
                    continue
            if only_module and s.get("module_bucket") != only_module:
                continue
            out.append(i)
        return out

    todo = residual_indices()
    if not todo:
        msg = ("No trait-impl sites left to consider for dyn."
               if filter_mode == "trait-impl"
               else "No untouched sites match. Nothing to do.")
        print(msg)
        return

    if filter_mode == "trait-impl":
        print(f"{len(todo)} trait-impl site(s) without blocked_dyn — "
              "re-examine for dyn co-blocker.")
        print("Type the *full* blocker set you want (e.g. `cd` to upgrade "
              "blocked_cell to cell+dyn). `n` keeps current.")
    else:
        print(f"{len(todo)} untouched site(s) (no blockers, no notes).")
    print_review_help()

    history: list[int] = []
    pos = 0

    def save():
        out_path.write_text(json.dumps(doc, indent=2) + "\n")

    while pos < len(todo):
        site_idx = todo[pos]
        site = sites[site_idx]
        # Clear screen once per new site; preserves scrollback on xterm-like terminals.
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()
        present_site(site, repo_root, pos, len(todo))

        # Inner loop: re-prompt on help / invalid input without re-clearing.
        advance = False
        while not advance:
            print(KEY_HINT)
            sys.stdout.flush()
            try:
                raw = input("blockers> ")
            except (EOFError, KeyboardInterrupt):
                print("\nSaving and exiting.")
                save()
                return

            kind, payload, notes = parse_command(raw)

            if kind == "error":
                print(f"  {payload}. type ? for help.")
                continue

            if kind == "control":
                if payload in ("", "?"):
                    print_review_help()
                    continue
                if payload == "q":
                    save()
                    remaining = sum(1 for s in sites
                                    if not s["blockers"] and not s.get("notes"))
                    print(f"\nSaved to {out_path}. "
                          f"{remaining} untouched site(s) remain.")
                    return
                if payload == "n":
                    if notes:
                        site["notes"] = notes
                        save()
                    history.append(site_idx)
                    pos += 1
                    advance = True
                    break
                if payload == "b":
                    if not history:
                        print("(no earlier site to go back to)")
                        continue
                    prev = history.pop()
                    todo.insert(pos, prev)
                    advance = True
                    break
                # Should be unreachable.
                print(f"  unhandled control '{payload}'. type ? for help.")
                continue

            # kind == "blockers"
            site["blockers"] = payload
            if notes:
                site["notes"] = notes
            save()
            history.append(site_idx)
            pos += 1
            advance = True

    save()
    remaining = sum(1 for s in sites if not s["blockers"] and not s.get("notes"))
    print(f"\nSaved to {out_path}. {remaining} untouched site(s) remain.")


# ---------------------------------------------------------------------------
# Stats / report
# ---------------------------------------------------------------------------

def print_histogram(doc: dict, field: str) -> None:
    """ASCII bar chart: count of sites grouped by `field` (e.g. sink_flavor).

    Special-cases `blocker`/`blockers`: groups by the exclusive blocker set,
    rendered as `+`-joined name (or `(none)` when empty)."""
    from collections import Counter
    sites = doc["sites"]
    total = len(sites)

    def value_for(site):
        if field in ("blocker", "blockers"):
            bs = site_blockers(site)
            return "+".join(bs) if bs else "(none)"
        return site.get(field) or "(none)"

    counts = Counter(value_for(s) for s in sites)
    if not counts:
        print(f"(no values for field '{field}')")
        return

    max_n = max(counts.values())
    bar_width = 50  # chars
    name_width = max(len(k) for k in counts)
    name_width = min(name_width, 40)

    label = "blockers (exclusive set)" if field in ("blocker", "blockers") else field
    print(f"Distribution of {total} panic sites by `{label}`:\n")
    for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        bar_len = max(1, round(n / max_n * bar_width)) if n else 0
        bar = "█" * bar_len
        pct = 100.0 * n / total
        print(f"  {name:<{name_width}}  {bar} {n}  ({pct:.1f}%)")


def print_stats(doc: dict) -> None:
    from collections import Counter

    sites = doc["sites"]
    total = len(sites)

    exclusive: Counter = Counter()       # exact blocker tuple → count
    any_of: dict[str, int] = {b: 0 for b in BLOCKERS}
    multi_blocker = 0
    untouched = 0
    by_flavor: Counter = Counter()
    by_flavor_blocked: Counter = Counter()

    for s in sites:
        bs = site_blockers(s)
        exclusive[tuple(bs)] += 1
        if not bs and not s.get("notes"):
            untouched += 1
        if len(bs) > 1:
            multi_blocker += 1
        for b in bs:
            any_of[b] += 1
        f = s.get("sink_flavor") or "<unknown>"
        by_flavor[f] += 1
        if bs:
            by_flavor_blocked[f] += 1

    print(f"Total sites: {total}")
    print(f"Untouched (no blockers, no notes): {untouched}")
    print(f"Multi-blocker sites: {multi_blocker}")
    print()
    print("By blocker (exclusive — site has exactly this blocker set):")
    for tup, n in sorted(exclusive.items(), key=lambda kv: (-kv[1], kv[0])):
        label = "+".join(tup) if tup else "(none)"
        print(f"  {n:4d}  {label}")
    print()
    print("By blocker (any-of — site has this blocker, possibly with others):")
    for b in BLOCKERS:
        print(f"  {any_of[b]:4d}  {b}")
    print()
    print("By sink_flavor:")
    for f, n in sorted(by_flavor.items(), key=lambda kv: -kv[1]):
        blk = by_flavor_blocked.get(f, 0)
        print(f"  {n:4d}  {f:<24}  ({blk} have ≥1 blocker)")


def write_report(doc: dict, out_path: Path) -> None:
    sites = doc["sites"]
    total = len(sites)
    with_any = sum(1 for s in sites if site_blockers(s))

    # Group by exclusive blocker tuple. "(none)" is reserved for empty.
    by_set: dict[tuple, list] = {}
    for s in sites:
        bs = tuple(site_blockers(s))
        by_set.setdefault(bs, []).append(s)

    def label_for(tup: tuple) -> str:
        return "+".join(tup) if tup else "(none)"

    ordered_keys = sorted(
        by_set.keys(),
        key=lambda t: (t == (), -len(by_set[t]), label_for(t)),
    )

    lines = []
    lines.append("# Panic Blocker Digest")
    lines.append("")
    lines.append(f"Source: `{doc['meta']['binary']}`")
    lines.append(f"Total {total} sites; {with_any} have ≥1 blocker; "
                 f"{total - with_any} have none.")
    lines.append("")
    lines.append("Counts by exclusive blocker set:")
    for k in ordered_keys:
        lines.append(f"- **{label_for(k)}**: {len(by_set[k])}")
    lines.append("")

    # Any-of summary (one blocker may appear in multiple sets).
    any_counts = {b: 0 for b in BLOCKERS}
    for s in sites:
        for b in site_blockers(s):
            any_counts[b] += 1
    lines.append("Counts by blocker (any-of, sites where the blocker appears in any combination):")
    for b in BLOCKERS:
        lines.append(f"- **{b}** (any-of): {any_counts[b]}")
    lines.append("")

    for k in ordered_keys:
        entries = by_set[k]
        lines.append(f"## {label_for(k)} ({len(entries)})")
        lines.append("")
        by_outer: dict[str, list] = {}
        for s in entries:
            f = (s.get("outermost_frame") or {}).get("file") or "<unknown>"
            by_outer.setdefault(f, []).append(s)
        for outer_file in sorted(by_outer.keys()):
            group = by_outer[outer_file]
            lines.append(f"### `{outer_file}` ({len(group)})")
            lines.append("")
            group.sort(key=lambda s: (
                (s.get("effective_frame") or {}).get("line") or 0,
                int(s["address"], 16),
            ))
            for s in group:
                eff = s.get("effective_frame") or {}
                eff_loc = f"{eff.get('file')}:{eff.get('line')}"
                src = (s.get("effective_source") or "").strip()
                if len(src) > 120:
                    src = src[:117] + "..."
                notes = s.get("notes") or ""
                notes_suffix = f" — {notes}" if notes else ""
                lines.append(
                    f"- {s['address']}  `{eff_loc}`  _{s['sink_flavor']}_"
                    f"{notes_suffix}"
                )
                if src:
                    lines.append(f"  - `{src}`")
            lines.append("")
    out_path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    here = Path(__file__).resolve().parent
    repo_root = here.parent

    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply-rules", action="store_true",
                      help="Auto-assign blocked_reentrancy via the grant rule.")
    mode.add_argument("--review",      action="store_true",
                      help="Interactive walker over sites with empty blocker.")
    mode.add_argument("--stats",       action="store_true",
                      help="Print blocker + sink_flavor distribution.")
    mode.add_argument("--report",      action="store_true",
                      help="Emit markdown digest grouped by blocker.")
    mode.add_argument("--histogram",   action="store_true",
                      help="ASCII bar chart of site counts by --by (default: sink_flavor).")
    ap.add_argument("--json", default=here / "panic_survey.json", type=Path)
    ap.add_argument("--out",  default=here / "panic_labels.md", type=Path,
                    help="(report only) markdown output path.")
    ap.add_argument("--by",   default="sink_flavor",
                    help="(histogram only) field to group by (default: sink_flavor). "
                         "Useful options: sink_flavor, module_bucket, origin_bucket, blocker, sink.")
    ap.add_argument("--module", default=None,
                    help="(review only) restrict to a module_bucket, e.g. capsules/core")
    ap.add_argument("--filter", default=None, choices=["trait-impl"],
                    help="(review only) preset filter. 'trait-impl' re-walks "
                         "sites in trait-impl methods that don't yet have "
                         "blocked_dyn — useful for the dyn revisit pass.")
    args = ap.parse_args()

    if not args.json.exists():
        print(f"ERROR: {args.json} not found. Run panic_survey.py first.", file=sys.stderr)
        return 1
    doc = json.loads(args.json.read_text())

    if args.apply_rules:
        r = apply_rules(doc)
        args.json.write_text(json.dumps(doc, indent=2) + "\n")
        print(f"Applied grant rule to {args.json}:")
        print(f"  blocked_reentrancy (added):       {r['grant_assigned']}")
        print(f"  unchanged sites:                  {r['untouched']}")
    elif args.review:
        review(doc, repo_root, args.json,
               only_module=args.module, filter_mode=args.filter)
    elif args.stats:
        print_stats(doc)
    elif args.report:
        write_report(doc, args.out)
        print(f"Wrote {args.out}")
    elif args.histogram:
        print_histogram(doc, args.by)
    return 0


if __name__ == "__main__":
    sys.exit(main())
