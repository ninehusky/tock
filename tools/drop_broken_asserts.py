#!/usr/bin/env python3
"""
drop_broken_asserts.py — for each `flux_support::assert(false);` line that
sits in a mid-expression context (broken), remove just that line. Keep the
preceding FLUX-OPT/FLUX-TODO comment in place as a TODO marker.

Detection: assert(false) is "broken" if either
  - the line directly above (skipping FLUX-* comment lines) ends with a
    non-statement-end character: `(` `,` `|` `.` `=` `:` `=>` `[`
  - or the line directly below starts with `.` `,` `)` `]` `=>`

Idempotent: leaves all non-broken inserts in place.
"""
import re
import subprocess
import sys
from pathlib import Path

FLUX_COMMENT_RX = re.compile(r'^[ \t]*// FLUX-(?:OPT|TODO)\b')
FLUX_TODO_BLOCK_OPEN = re.compile(r'^[ \t]*// (?:FLUX-(?:OPT|TODO))\s+line=\d+.*addrs=\[$')
FLUX_TODO_BLOCK_CLOSE = re.compile(r'^[ \t]*// \]$')
ASSERT_RX = re.compile(r'^[ \t]*flux_support::assert\(false\);[ \t]*$')

BROKEN_PREV_END_RX = re.compile(r'[(,|=:\[]\s*$|=>\s*$|\.\w[\w]*\(?\)?\s*$|[}\]]\s*$|^\s*///|^\s*#\[')
BROKEN_NEXT_START_RX = re.compile(
    r'^[ \t]*[.,)\]}]'
    r'|^[ \t]*=>'
    r'|^[ \t]*#\['
    r'|^[ \t]*pub\s+(fn|const|use|mod|struct|enum|trait|impl|static)\b'
    r'|^[ \t]*(fn|const|use|mod|struct|enum|trait|impl|static|type|unsafe\s+impl)\b'
    r'|^[ \t]*///'
)


def scan_prev_non_flux(lines, idx):
    """Walk upward from idx (the assert line) past FLUX comment block lines.
    Return the index of the nearest non-FLUX-comment line."""
    j = idx - 1
    while j >= 0:
        if FLUX_COMMENT_RX.match(lines[j]) or FLUX_TODO_BLOCK_CLOSE.match(lines[j]):
            j -= 1
            continue
        # Inside a multi-addr block: keep going up past the addr lines
        if re.match(r'^[ \t]*//\s+0x', lines[j]):
            j -= 1
            continue
        return j
    return -1


def process(path: Path, dry_run: bool = False) -> int:
    lines = path.read_text().splitlines()
    out = []
    i = 0
    dropped = 0
    while i < len(lines):
        if ASSERT_RX.match(lines[i]):
            prev_j = scan_prev_non_flux(lines, i)
            next_i = i + 1
            broken = False
            if prev_j >= 0:
                prev_line = lines[prev_j].rstrip()
                if prev_line and BROKEN_PREV_END_RX.search(prev_line):
                    broken = True
            if not broken and next_i < len(lines):
                nxt = lines[next_i]
                if BROKEN_NEXT_START_RX.match(nxt):
                    broken = True
            if broken:
                dropped += 1
                if dry_run:
                    print(f"  drop {path}:{i+1}: prev='{lines[prev_j].strip()[:60] if prev_j>=0 else ''}'  next='{lines[next_i].strip()[:60] if next_i<len(lines) else ''}'")
                i += 1
                continue
        out.append(lines[i])
        i += 1

    if not dry_run and dropped:
        path.write_text('\n'.join(out) + '\n')
    return dropped


def main() -> int:
    dry_run = '--dry-run' in sys.argv
    files = subprocess.check_output(
        ['git', 'diff', '--name-only', '--diff-filter=M', '--', '*.rs'],
        text=True,
    ).split()
    total = 0
    for f in files:
        p = Path(f)
        if not p.exists():
            continue
        n = process(p, dry_run=dry_run)
        if n:
            print(f"{f}: dropped {n}")
            total += n
    print(f"\nTotal {'would drop' if dry_run else 'dropped'}: {total}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
