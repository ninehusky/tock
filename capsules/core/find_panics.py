#!/usr/bin/env python3
"""
find_panics.py

Scans nrf52840dk.dis for every `bl` that targets a panic sink (as listed in
symbols.txt), then uses addr2line to map each call site back to source
file/line (including inlined frames).

Usage:
    python3 find_panics.py [--dis <path>] [--sinks <path>] [--elf <path>] [--addr2line <path>]

Defaults:
    --dis       nrf52840dk.dis
    --sinks     symbols.txt
    --elf       ../../target/thumbv7em-none-eabi/release/nrf52840dk
    --addr2line /opt/homebrew/Cellar/binutils/2.46.0/bin/addr2line
"""

import re
import argparse
import subprocess
from pathlib import Path
from collections import defaultdict

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

# Function label:  00001234 <some::symbol::possibly<nested>>:
FUNC_LABEL_RE = re.compile(r'^([0-9a-f]+) <(.+)>:\s*$')

# bl call:         1234: f0xx xxxx    \tbl\t0xabcd <target name> @ imm = #N
# Bytes section uses spaces; a tab separates bytes from mnemonic; another tab
# separates mnemonic from operands.  The target name may contain nested <>,
# so we greedily capture everything up to the last > before ' @' or EOL.
BL_RE = re.compile(
    r'^\s+([0-9a-f]+):\s+.*\tbl\t0x([0-9a-f]+) <(.+)>(?:\s+@|\s*$)'
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_sinks(path: Path) -> set[str]:
    """Return the set of panic-sink symbol names from the sinks file."""
    sinks = set()
    for line in path.read_text().splitlines():
        name = line.strip()
        if name:
            sinks.add(name)
    return sinks


def scan(dis_path: Path, sinks: set[str]):
    """
    Yield (call_addr, enclosing_func, target_name) for every bl in the
    disassembly whose target symbol name is a panic sink.
    """
    current_func = "<unknown>"
    with dis_path.open() as fh:
        for line in fh:
            m = FUNC_LABEL_RE.match(line)
            if m:
                current_func = m.group(2)
                continue

            m = BL_RE.match(line)
            if not m:
                continue
            call_addr, _target_addr, target_name = m.groups()

            if target_name in sinks:
                yield call_addr, current_func, target_name


def addr2line(binary: Path, tool: Path, addrs: list[str]) -> dict[str, list[str]]:
    """
    Run addr2line -f -C -i on all addresses at once.
    Returns {addr: [line, ...]} where each entry is the demangle+inline output.
    addr2line with -f prints alternating function/location lines;
    with -i it unwinds inlined frames (each frame = two lines: func then loc).
    We collect all lines for each address as a single block.
    """
    if not addrs:
        return {}

    # addr2line prints two lines per frame (func name, then file:line),
    # with a blank separator between addresses when using --addresses.
    # Simpler: run once with all addresses; it emits results in input order,
    # two lines per inlined frame, no separator.  We pass --addresses (-a)
    # so we can split on the echoed address lines.
    cmd = [str(tool), '-e', str(binary), '-f', '-C', '-i', '-a'] + [f'0x{a}' for a in addrs]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
    except subprocess.CalledProcessError:
        return {a: ['<addr2line error>'] for a in addrs}

    # Output format with -a:
    #   0xADDR
    #   func_name
    #   file:line
    #   func_name          <- inlined frame
    #   file:line
    #   0xNEXTADDR
    #   ...
    result: dict[str, list[str]] = {}
    current_addr = None
    lines_for_addr: list[str] = []
    raw_lines = out.splitlines()
    i = 0
    while i < len(raw_lines):
        l = raw_lines[i]
        if l.startswith('0x'):
            if current_addr is not None:
                result[current_addr] = lines_for_addr
            current_addr = l[2:].lstrip('0') or '0'  # normalise
            lines_for_addr = []
        else:
            lines_for_addr.append(l)
        i += 1
    if current_addr is not None:
        result[current_addr] = lines_for_addr

    return result


def format_frames(lines: list[str]) -> str:
    """
    addr2line -f -C -i produces pairs of (func_name, file:line).
    Format them as an indented inline chain, innermost first.
    """
    frames = []
    for j in range(0, len(lines) - 1, 2):
        func = lines[j].strip()
        loc  = lines[j + 1].strip()
        frames.append(f"{func}  @  {loc}")
    if not frames:
        return "        <no debug info>"
    # Indent each frame; innermost (index 0) is the actual panic call
    out = []
    for k, f in enumerate(frames):
        prefix = "        " + ("(inlined) " * k)
        out.append(f"{prefix}{f}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    here = Path(__file__).parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dis',      default=here / 'nrf52840dk.dis')
    ap.add_argument('--sinks',    default=here / 'symbols.txt')
    ap.add_argument('--elf',      default=here / '../../target/thumbv7em-none-eabi/release/nrf52840dk')
    ap.add_argument('--addr2line',default='/opt/homebrew/Cellar/binutils/2.46.0/bin/addr2line')
    args = ap.parse_args()

    dis_path = Path(args.dis)
    elf_path = Path(args.elf)
    a2l_tool = Path(args.addr2line)
    sinks    = load_sinks(Path(args.sinks))

    print(f"Loaded {len(sinks)} panic sinks.\n")

    results = list(scan(dis_path, sinks))
    print(f"Found {len(results)} panic call sites — resolving with addr2line...\n")

    # Resolve all addresses in one batch
    all_addrs = [addr for addr, _, _ in results]
    resolved  = addr2line(elf_path, a2l_tool, all_addrs)

    # Group by enclosing function
    by_func: dict[str, list] = defaultdict(list)
    for call_addr, func, target_name in results:
        norm = call_addr.lstrip('0') or '0'
        frames = resolved.get(norm, ['<not resolved>'])
        by_func[func].append((call_addr, target_name, frames))

    for func in sorted(by_func):
        print(f"[{func}]")
        for call_addr, target_name, frames in sorted(by_func[func]):
            print(f"    0x{call_addr}  ->  {target_name}")
            print(format_frames(frames))
        print()


if __name__ == '__main__':
    main()
