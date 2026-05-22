#!/usr/bin/env python3
"""
apply_flux_annotations.py — insert FLUX-OPT / FLUX-TODO markers into every
depth=0 panic site listed in panic_survey.json.

Per (file, line) group:
  - If a flux_support::assert(...) is already directly above the panicking
    op (scanning past blank lines + comments), emit a FLUX-OPT marker above
    the assert.
  - Otherwise emit FLUX-TODO + a runtime-noop flux_support::assert(false)
    directly above the panicking op.

Multi-address groups (same file:line, multiple addresses) get a single
multi-line marker listing every address.

Stdlib sites (paths under /rustc/) are skipped.

Edits are applied bottom-up per file so line numbers don't shift mid-pass.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def is_stdlib(path: str) -> bool:
    return '/rustc/' in path or path.startswith('/library/')


def format_marker(kind: str, line: int, addrs: list[str], indent: str,
                  max_width: int = 100) -> list[str]:
    """Return the marker comment as a list of source-ready lines (with indent)."""
    if len(addrs) == 1:
        return [f"{indent}// {kind} addr={addrs[0]} line={line}"]
    # Multi-address: header, addrs wrapped to max_width, closer
    header = f"{indent}// {kind} line={line} addrs=["
    addr_indent = f"{indent}//     "
    lines = [header]
    cur = addr_indent
    for i, a in enumerate(addrs):
        sep = "," if i < len(addrs) - 1 else ","
        chunk = f"{a}{sep} "
        if len(cur) + len(chunk) > max_width and cur != addr_indent:
            lines.append(cur.rstrip())
            cur = addr_indent
        cur += chunk
    if cur.strip():
        lines.append(cur.rstrip())
    lines.append(f"{indent}// ]")
    return lines


def detect_assert_above(source: list[str], target_line_1b: int) -> bool:
    """Scan upward from line (target-1) past blank/comment lines.
    Return True iff the first non-blank non-comment line is a
    flux_support::assert(...)."""
    idx = target_line_1b - 2  # one above target (0-indexed)
    while idx >= 0:
        s = source[idx].strip()
        if not s or s.startswith('//'):
            idx -= 1
            continue
        return 'flux_support::assert(' in s
    return False


def already_annotated(source: list[str], target_line_1b: int, window: int = 4) -> bool:
    """Check target line and the `window` lines above for an existing
    FLUX-OPT or FLUX-TODO marker. Robust to small line-number drift between
    survey and source."""
    for offset in range(0, window + 1):
        idx = target_line_1b - 1 - offset
        if idx < 0:
            break
        s = source[idx]
        if 'FLUX-OPT' in s or 'FLUX-TODO' in s:
            return True
    return False


def get_indent(line: str) -> str:
    return line[:len(line) - len(line.lstrip())]


def resolve_source_path(survey_path: str, repo_root: Path) -> Path | None:
    """Resolve a survey file path (e.g. './arch/cortex-m/src/systick.rs')
    to a real path under repo_root."""
    p = survey_path.lstrip('./').lstrip('/')
    full = repo_root / p
    return full if full.exists() else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--survey', default='tools/panic_survey.json', type=Path)
    ap.add_argument('--root', default='.', type=Path)
    ap.add_argument('--file', default=None,
                    help='Only process source paths containing this substring.')
    ap.add_argument('--dry-run', action='store_true',
                    help='Show planned edits; do not write files.')
    args = ap.parse_args()

    survey = json.load(args.survey.open())

    # Group sites by (file, line)
    by_loc: dict[tuple[str, int], list[tuple[str, str, str]]] = defaultdict(list)
    skipped_stdlib = skipped_no_line = 0
    for s in survey['sites']:
        f = s['effective_frame']['file']
        if is_stdlib(f):
            skipped_stdlib += 1
            continue
        line = s['effective_frame']['line']
        if line is None:
            skipped_no_line += 1
            continue
        addr = s['address']
        flavor = s.get('sink_flavor', '')
        ef = s.get('effective_source', '')
        by_loc[(f, line)].append((addr, flavor, ef))

    # Group by file
    by_file: dict[str, list[tuple[int, list]]] = defaultdict(list)
    for (f, line), sites in by_loc.items():
        by_file[f].append((line, sites))

    files = sorted(by_file.keys())
    if args.file:
        files = [f for f in files if args.file in f]

    print(f"Total user-code locations: {len(by_loc)}; files: {len(files)}; "
          f"skipped stdlib: {skipped_stdlib}; skipped no-line: {skipped_no_line}",
          file=sys.stderr)

    total_edits = 0
    for f in files:
        full = resolve_source_path(f, args.root)
        if full is None:
            print(f"SKIP missing: {f}", file=sys.stderr)
            continue

        source = full.read_text().splitlines()
        # Bottom-up
        edits = sorted(by_file[f], key=lambda x: -x[0])

        planned: list[tuple[int, list[str], str]] = []  # (line, new_lines, summary)
        for line_num, sites in edits:
            if line_num < 1 or line_num > len(source):
                print(f"SKIP {f}:{line_num} out of range (file has {len(source)} lines)",
                      file=sys.stderr)
                continue
            target = source[line_num - 1]
            if already_annotated(source, line_num):
                continue  # Idempotent: skip already-annotated sites
            has_assert = detect_assert_above(source, line_num)
            kind = 'FLUX-OPT' if has_assert else 'FLUX-TODO'
            indent = get_indent(target)
            addrs = [a for a, _, _ in sites]
            marker = format_marker(kind, line_num, addrs, indent)
            inserted = list(marker)
            if not has_assert:
                inserted.append(f"{indent}flux_support::assert(false);")
            planned.append((line_num, inserted,
                            f"{kind} ({len(addrs)} addr{'s' if len(addrs)>1 else ''})"))

        if args.dry_run:
            print(f"\n=== {f} — {len(planned)} edits ===")
            for line_num, new_lines, summary in planned:
                print(f"  L{line_num}: {summary}")
                for nl in new_lines:
                    print(f"      {nl}")
        else:
            # Insert bottom-up
            new_source = list(source)
            for line_num, new_lines, _ in planned:
                new_source[line_num - 1:line_num - 1] = new_lines
            full.write_text('\n'.join(new_source) + '\n')

        total_edits += len(planned)

    print(f"\nDone. {'Would apply' if args.dry_run else 'Applied'} "
          f"{total_edits} edits across {len(files)} files.", file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
