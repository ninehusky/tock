#!/usr/bin/env python3
"""
relocate_flux_annotations.py — find FLUX-OPT/FLUX-TODO insertions that landed
in mid-expression positions (between fn-call args, inside method chains,
inside tuples/patterns, inside closure bodies as a stray statement), and
lift them to before the nearest enclosing statement.

Detection (a marker is in a "broken" position if):
  - the line BEFORE the `// FLUX-...` comment ends with one of:
      `(` `,` `|` `=` `:` `=>` `[`
    OR ends with `.foo` style method-chain continuation
  - OR the line AFTER `flux_support::assert(...);` starts with one of:
      `.` `,` `)` `]` `=>`

Relocation:
  Walk upward from the comment until we find a line whose indent is
  <= comment's_indent - 4 AND whose stripped form starts with a
  statement-starter (`let`, `if`, `match`, `for`, `while`, `loop`,
  `return`, `break`, `continue`, `unsafe`, `fn`, or just looks like an
  expression statement). Insert the marker just before that line at the
  matching indent. If the relocation changes scope (closure -> outer,
  arm body -> outside match), tag with `// FLUX-RELOC` so the user can
  audit later.

Idempotent: skips markers already in a non-broken position.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

FLUX_COMMENT = re.compile(r'^(?P<indent>[ \t]*)// (?P<kind>FLUX-(?:OPT|TODO))\b[^\n]*$')
FLUX_TODO_MULTI_OPEN = re.compile(r'^[ \t]*// (?:FLUX-(?:OPT|TODO))\s+line=\d+.*addrs=\[$')
FLUX_TODO_MULTI_CLOSE = re.compile(r'^[ \t]*// \]$')
ASSERT_LINE = re.compile(r'^[ \t]*flux_support::assert\(false\);[ \t]*$')

BROKEN_AFTER_RX = re.compile(r'^[ \t]*[.,)\]]|^[ \t]*=>')
BROKEN_BEFORE_END_RX = re.compile(r'[(,|=:\[]\s*$|\b=>\s*$|^\s*\.\w')

STATEMENT_STARTERS = (
    'let ', 'if ', 'match ', 'for ', 'while ', 'loop ', 'return ',
    'break', 'continue', 'unsafe ', 'unsafe{',
    '#[', '//',  # attribute or comment (skip past)
)
SCOPE_CHANGE_PARENT_TOKENS = ('|',)  # closure marker


def get_indent(line: str) -> str:
    return line[:len(line) - len(line.lstrip())]


def find_marker_blocks(lines: list[str]) -> list[tuple[int, int]]:
    """Return list of (start_idx, end_idx_inclusive) for FLUX marker blocks.
    A block is: one or more FLUX comment lines, followed by an assert(false);
    line. end_idx points to the assert line."""
    blocks = []
    i = 0
    while i < len(lines):
        m = FLUX_COMMENT.match(lines[i])
        if m:
            start = i
            # If multi-line FLUX-TODO addrs=[ block, consume until // ]
            if FLUX_TODO_MULTI_OPEN.match(lines[i]):
                j = i + 1
                while j < len(lines) and not FLUX_TODO_MULTI_CLOSE.match(lines[j]):
                    j += 1
                i = j  # i is now at the // ] line
            # Check if next line is assert(false);
            if i + 1 < len(lines) and ASSERT_LINE.match(lines[i + 1]):
                blocks.append((start, i + 1))
                i += 2
                continue
            # Maybe just a FLUX-OPT comment with no following assert (existing-assert case)
            # Skip it; not a "broken" candidate
        i += 1
    return blocks


def is_broken(lines: list[str], start: int, end: int) -> bool:
    """Heuristic: marker is broken if surrounding context isn't statement-level."""
    # Check line before the comment
    if start > 0:
        before = lines[start - 1].rstrip()
        if before and BROKEN_BEFORE_END_RX.search(before):
            return True
    # Check line after the assert
    if end + 1 < len(lines):
        after = lines[end + 1].rstrip()
        if BROKEN_AFTER_RX.match(after):
            return True
    return False


def find_relocation_target(lines: list[str], start: int) -> tuple[int, str, bool]:
    """Walk upward from `start` to find the line where the enclosing statement
    begins. Return (target_idx, target_indent, scope_changed)."""
    comment_indent = len(get_indent(lines[start]))
    scope_changed = False
    i = start - 1
    while i >= 0:
        line = lines[i]
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        # Detect closure scope by looking for `|x|` or `| ... |` on this line
        if '|' in line and indent <= comment_indent:
            # might be a closure boundary
            pass  # don't auto-tag for now; rely on token detection later
        # Stop at first line whose indent is strictly LESS than the marker's indent
        # AND looks like a statement-starter or block opener.
        if indent < comment_indent:
            s = stripped
            if any(s.startswith(p) for p in STATEMENT_STARTERS):
                return (i, ' ' * indent, scope_changed)
            # block opener: line ends with `{`
            if s.rstrip().endswith('{'):
                # Insert just after this line at indent+4
                return (i + 1, ' ' * (indent + 4), scope_changed)
            # something like `} else {` or `} else if x {`
            if s.startswith('}') and s.rstrip().endswith('{'):
                return (i + 1, ' ' * (indent + 4), scope_changed)
        # Closure boundary detection: line contains `|args|` and we're below it
        if re.search(r'\|[^|]*\|\s*[{(]?\s*$', line) and indent < comment_indent:
            scope_changed = True
            return (i + 1, ' ' * (indent + 4), True)
        i -= 1
    return (0, '', False)


def relocate(path: Path, dry_run: bool = False) -> int:
    text = path.read_text()
    lines = text.splitlines()
    blocks = find_marker_blocks(lines)
    # Filter to broken blocks
    broken = [(s, e) for s, e in blocks if is_broken(lines, s, e)]
    if not broken:
        return 0

    # Process bottom-up so indices don't shift
    fixes = 0
    for start, end in sorted(broken, key=lambda x: -x[0]):
        target_idx, target_indent, scope_changed = find_relocation_target(lines, start)
        if target_idx == 0 and target_indent == '':
            # Couldn't find target — leave as-is
            continue
        # Extract the marker block lines, re-indent
        marker_block = lines[start:end + 1]
        # Strip old indent and apply new indent
        old_indent = get_indent(marker_block[0])
        new_block = []
        for ml in marker_block:
            if ml.startswith(old_indent):
                new_block.append(target_indent + ml[len(old_indent):])
            else:
                new_block.append(ml)
        # If scope changed, add FLUX-RELOC marker on first line
        if scope_changed:
            first = new_block[0]
            if 'FLUX-RELOC' not in first:
                new_block[0] = first + '  // FLUX-RELOC: lifted out of closure'
        # Remove the old block
        del lines[start:end + 1]
        # Insert at the new position (may have shifted due to deletion above)
        # Since target_idx < start (we walked up), it's unchanged by deletion
        for k, l in enumerate(new_block):
            lines.insert(target_idx + k, l)
        fixes += 1

    if not dry_run and fixes:
        path.write_text('\n'.join(lines) + '\n')
    return fixes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('files', nargs='*')
    args = ap.parse_args()

    files = args.files
    if not files:
        files = subprocess.check_output(
            ['git', 'diff', '--name-only', '--diff-filter=M', '--', '*.rs'],
            text=True,
        ).split()
    total = 0
    for f in files:
        p = Path(f)
        if not p.exists():
            continue
        n = relocate(p, dry_run=args.dry_run)
        if n:
            print(f"{f}: relocated {n}")
            total += n
    print(f"\nTotal {'would relocate' if args.dry_run else 'relocated'}: {total}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
