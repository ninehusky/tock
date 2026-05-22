#!/usr/bin/env python3
"""
panic_survey.py — canonical end-to-end survey of every panic call site in the
nrf52840dk release binary.

Pipeline:
    1. (optional) `make release` in boards/nordic/nrf52840dk
    2. gobjdump -d the ELF into a .dis file
    3. scan the disassembly for every `bl` into a symbol in symbols.txt
    4. batch `addr2line -a -f -C -i` on every call-site address
    5. classify each site (sink flavor, module bucket, origin bucket, grant re-entrancy, flux-blocker hint)
    6. emit JSON

Defaults are relative to this file's directory (`tools/`).
"""

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

FUNC_LABEL_RE = re.compile(r'^([0-9a-f]+) <(.+)>:\s*$')
FILE_LINE_RE = re.compile(r'^(.*?):(\d+)(?:\s+\(discriminator \d+\))?$')


# ---------------------------------------------------------------------------
# Build + disassemble
# ---------------------------------------------------------------------------

def run_build(repo_root: Path) -> None:
    board_dir = repo_root / 'boards' / 'nordic' / 'nrf52840dk'
    print(f"Running `make release` in {board_dir} …", file=sys.stderr)
    subprocess.check_call(['make', 'release'], cwd=board_dir)


def regenerate_dis(objdump: str, elf: Path, dis: Path) -> None:
    print(f"Running {objdump} -d {elf} > {dis} …", file=sys.stderr)
    with dis.open('w') as fh:
        subprocess.check_call([objdump, '-d', str(elf)], stdout=fh)


def ensure_dis(objdump: str, elf: Path, dis: Path, force: bool) -> None:
    if force or not dis.exists() or dis.stat().st_mtime < elf.stat().st_mtime:
        regenerate_dis(objdump, elf, dis)
    else:
        print(f"Disassembly {dis} is up-to-date, reusing.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Scan + demangle (helpers adapted from find_panics_flux.py)
# ---------------------------------------------------------------------------

def load_sinks(path: Path) -> set[str]:
    return {l.strip() for l in path.read_text().splitlines() if l.strip()}


def _iter_logical_lines(dis_path: Path):
    """Yield logical lines, joining gobjdump's `\\\\\\n` continuations."""
    with dis_path.open() as fh:
        pending = ""
        for raw in fh:
            if pending:
                raw = pending + raw.lstrip()
                pending = ""
            if raw.endswith("\\\n"):
                pending = raw[:-2]
                continue
            yield raw


def build_demangle_map(dis_path: Path) -> dict[str, str]:
    mangled = set()
    for raw in _iter_logical_lines(dis_path):
        parts = raw.strip().split('\t')
        if len(parts) < 3 or parts[2].strip() != 'bl':
            continue
        operand = parts[3] if len(parts) > 3 else ""
        lt, gt = operand.find('<'), operand.rfind('>')
        if lt != -1 and gt != -1:
            mangled.add(operand[lt + 1:gt])
    if not mangled:
        return {}
    ordered = list(mangled)
    try:
        out = subprocess.check_output(
            ['rustfilt'], input='\n'.join(ordered), text=True, stderr=subprocess.DEVNULL
        )
        return dict(zip(ordered, out.splitlines()))
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {m: m for m in ordered}


def scan(dis_path: Path, sinks: set[str], demangle: dict[str, str]):
    """Yield (call_addr, enclosing_asm_label, sink_name_demangled)."""
    current_func = "<unknown>"
    for raw in _iter_logical_lines(dis_path):
        m = FUNC_LABEL_RE.match(raw)
        if m:
            current_func = m.group(2)
            continue
        parts = raw.strip().split('\t')
        if len(parts) < 3 or parts[2].strip() != 'bl':
            continue
        operand = parts[3] if len(parts) > 3 else ""
        lt, gt = operand.find('<'), operand.rfind('>')
        if lt == -1 or gt == -1:
            continue
        target = operand[lt + 1:gt]
        demangled = demangle.get(target, target)
        if demangled in sinks:
            call_addr = parts[0].rstrip(':').strip()
            yield call_addr, current_func, demangled


def addr2line_batch(tool: Path, elf: Path, addrs: list[str]) -> dict[str, list[str]]:
    if not addrs:
        return {}
    cmd = [str(tool), '-e', str(elf), '-f', '-C', '-i', '-a'] + [f'0x{a}' for a in addrs]
    out = subprocess.check_output(cmd, text=True)

    result: dict[str, list[str]] = {}
    cur_addr = None
    cur_lines: list[str] = []
    for line in out.splitlines():
        if line.startswith('0x'):
            if cur_addr is not None:
                result[cur_addr] = cur_lines
            cur_addr = line[2:].lstrip('0') or '0'
            cur_lines = []
        else:
            cur_lines.append(line)
    if cur_addr is not None:
        result[cur_addr] = cur_lines
    return result


def parse_frames(lines: list[str]) -> list[dict]:
    """Return [{func, file, line}, ...] innermost-first from addr2line output.

    addr2line -f -C -i emits alternating (func, file:line) pairs.
    `line` is an int when parseable, else None (e.g. '?:?').
    """
    frames = []
    for i in range(0, len(lines) - 1, 2):
        func = lines[i].strip()
        loc = lines[i + 1].strip()
        m = FILE_LINE_RE.match(loc)
        if m:
            file_ = m.group(1)
            try:
                lineno = int(m.group(2))
            except ValueError:
                lineno = None
        else:
            # Typically '??:?' — unresolved
            file_ = loc.split(':', 1)[0] if ':' in loc else loc
            lineno = None
        frames.append({"func": func, "file": file_, "line": lineno})
    return frames


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

_SINK_FLAVOR = {
    "core::panicking::panic_bounds_check": "bounds",
    "core::panicking::panic_fmt": "explicit_panic",
    "core::panicking::panic": "explicit_panic",
    "core::slice::index::slice_start_index_len_fail": "slice_start",
    "core::slice::index::slice_start_index_len_fail::do_panic::runtime": "slice_start",
    "core::slice::index::slice_end_index_len_fail": "slice_end",
    "core::slice::index::slice_end_index_len_fail::do_panic::runtime": "slice_end",
    "core::slice::index::slice_index_order_fail": "slice_order",
    "core::slice::index::slice_index_order_fail::do_panic::runtime": "slice_order",
    "core::panicking::panic_const::panic_const_div_by_zero": "div_by_zero",
    "core::panicking::panic_const::panic_const_rem_by_zero": "rem_by_zero",
    "core::option::unwrap_failed": "unwrap_option",
    "core::result::unwrap_failed": "unwrap_result",
    "rust_begin_unwind": "unwind",
}


def classify_sink(sink: str) -> str:
    if sink in _SINK_FLAVOR:
        return _SINK_FLAVOR[sink]
    if sink.startswith("core::panicking::assert_failed"):
        return "assert"
    if "::unwrap_or_panic" in sink:
        return "optional_cell_unwrap"
    return "other"


def classify_module(path: str) -> str:
    """Path-prefix classifier for the innermost-frame file."""
    if not path or path in ("??", "?"):
        return "unknown"
    # Stdlib / toolchain paths come out either absolute (/rustc/...) or with
    # embedded /library/core/src/... segments.
    if path.startswith("/rustc/") or "/library/core/" in path or "/library/alloc/" in path \
            or "/compiler-builtins" in path or "/library/std/" in path:
        return "stdlib"
    # Tock paths from the workspace root. addr2line emits paths either as
    # absolute (containing /tock/...) or as repo-relative. Match on substring.
    markers = [
        ("capsules/core/",  "capsules/core"),
        ("capsules/extra/", "capsules/extra"),
        ("kernel/src/",     "kernel"),
        ("kernel/",         "kernel"),
        ("chips/",          "chips"),
        ("arch/",           "arch"),
        ("libraries/",      "libraries"),
        ("boards/",         "boards"),
    ]
    for needle, bucket in markers:
        if needle in path:
            return bucket
    return "other"


def effective_frame(frames: list[dict]) -> dict:
    """Deepest non-stdlib frame in the inline chain.

    addr2line -i reports stdlib helpers (e.g. /rustc/.../slice/index.rs) as the
    deepest frame when the panic is an inlined slice-index check. The useful
    "where does the panic live" frame for categorization is the first non-stdlib
    frame from the bottom. We prefer frames that have a source line attached;
    falls back to a non-stdlib frame without a line, then to the deepest frame.
    """
    if not frames:
        return {"func": "", "file": "", "line": None}
    # Pass 1: deepest non-stdlib frame WITH a source line
    for f in frames:
        if classify_module(f.get("file") or "") != "stdlib" and f.get("line") is not None:
            return f
    # Pass 2: deepest non-stdlib frame (even without a line)
    for f in frames:
        if classify_module(f.get("file") or "") != "stdlib":
            return f
    return frames[0]


def classify_origin(effective: dict, outermost: dict) -> str:
    eff_file = effective.get("file") or ""
    outer_file = outermost.get("file") or ""
    eff_bucket = classify_module(eff_file)
    outer_bucket = classify_module(outer_file)

    if eff_bucket == "stdlib":
        return "stdlib"
    if eff_file == outer_file and eff_file:
        return "local"
    # Grant re-entrancy: the `all_enterable` / `run` panic in kernel/src/grant.rs
    if "kernel/src/grant.rs" in eff_file:
        return "via_grant"
    if eff_bucket == "kernel" and outer_bucket in ("capsules/core", "capsules/extra"):
        return "via_kernel"
    if eff_bucket == "chips" and outer_bucket in ("capsules/core", "capsules/extra"):
        return "via_hal"
    if eff_bucket in ("capsules/core", "capsules/extra") \
            and outer_bucket in ("capsules/core", "capsules/extra") \
            and eff_file != outer_file:
        return "via_other_capsule"
    return "other"


def fetch_source_line(file_path: str, line: int | None, repo_root: Path,
                      cache: dict[str, list[str]]) -> str | None:
    """Return the raw source line at file:line. Handles absolute, repo-relative,
    and './'-prefixed paths from addr2line. Returns None if unresolvable."""
    if not file_path or line is None:
        return None
    candidates = []
    p = Path(file_path)
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.append(repo_root / file_path.lstrip('./'))
        candidates.append(Path(file_path))
    for cand in candidates:
        key = str(cand)
        if key not in cache:
            if cand.exists():
                try:
                    cache[key] = cand.read_text(errors='replace').splitlines()
                except OSError:
                    cache[key] = []
            else:
                cache[key] = []
        lines = cache[key]
        if 1 <= line <= len(lines):
            return lines[line - 1].strip()
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    here = Path(__file__).resolve().parent
    repo_root = here.parent
    default_elf = repo_root / 'target' / 'thumbv7em-none-eabi' / 'release' / 'nrf52840dk'

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--skip-build', action='store_true', help='Do not run `make release`; assume ELF exists.')
    ap.add_argument('--elf',      default=default_elf, type=Path)
    ap.add_argument('--dis',      default=here / 'nrf52840dk.dis', type=Path)
    ap.add_argument('--sinks',    default=here / 'symbols.txt', type=Path)
    ap.add_argument('--objdump',  default='gobjdump')
    ap.add_argument('--addr2line',default='/opt/homebrew/Cellar/binutils/2.46.0/bin/addr2line', type=Path)
    ap.add_argument('--out',      default=here / 'panic_survey.json', type=Path)
    ap.add_argument('--force-dis', action='store_true', help='Regenerate .dis even if newer than ELF.')
    args = ap.parse_args()

    if not args.skip_build:
        run_build(repo_root)

    if not args.elf.exists():
        print(f"ERROR: ELF not found at {args.elf}. Run without --skip-build, or pass --elf.", file=sys.stderr)
        return 1

    ensure_dis(args.objdump, args.elf, args.dis, force=args.force_dis or not args.skip_build)

    sinks = load_sinks(args.sinks)
    print(f"Loaded {len(sinks)} panic sinks.", file=sys.stderr)

    demangle = build_demangle_map(args.dis)
    raw_sites = list(scan(args.dis, sinks, demangle))
    print(f"Scan found {len(raw_sites)} panic-sink bl sites.", file=sys.stderr)

    addrs = [a for a, _, _ in raw_sites]
    resolved = addr2line_batch(args.addr2line, args.elf, addrs)

    # Load existing labels (if any) so we don't clobber work from panic_label.py.
    # Key on a build-stable tuple: (effective file:line, outermost file:line, sink).
    # Binary addresses shift across rebuilds; source locations don't (until edits).
    prior_labels: dict[tuple, dict] = {}
    if args.out.exists():
        try:
            old = json.loads(args.out.read_text())
            for s in old.get("sites", []):
                # Graceful migration: accept old single-blocker schema if encountered.
                blockers = s.get("blockers")
                if blockers is None:
                    legacy = s.get("blocker") or ""
                    blockers = [legacy] if legacy else []
                if not blockers and not s.get("notes"):
                    continue
                eff = s.get("effective_frame") or {}
                outer = s.get("outermost_frame") or {}
                key = (eff.get("file"), eff.get("line"),
                       outer.get("file"), outer.get("line"),
                       s.get("sink"))
                prior_labels[key] = {
                    "blockers": sorted(set(blockers)),
                    "notes":    s.get("notes", ""),
                }
        except (OSError, json.JSONDecodeError):
            pass

    sites = []
    preserved = 0
    source_cache: dict[str, list[str]] = {}
    for addr, enclosing_asm_label, sink in raw_sites:
        norm = addr.lstrip('0') or '0'
        frames = parse_frames(resolved.get(norm, []))
        if not frames:
            innermost = {"func": "", "file": "", "line": None}
            outermost = innermost
        else:
            innermost = frames[0]
            outermost = frames[-1]

        eff = effective_frame(frames)
        source_line = fetch_source_line(eff.get("file") or "",
                                        eff.get("line"), repo_root, source_cache)

        sink_flavor = classify_sink(sink)
        module_bucket = classify_module(eff.get("file") or "")
        origin_bucket = classify_origin(eff, outermost)

        key = (eff.get("file"), eff.get("line"),
               outermost.get("file"), outermost.get("line"), sink)
        prior = prior_labels.get(key, {})
        if prior:
            preserved += 1

        sites.append({
            "address":             f"0x{addr}",
            "sink":                sink,
            "sink_flavor":         sink_flavor,
            "enclosing_asm_label": enclosing_asm_label,
            "innermost_frame":     innermost,
            "effective_frame":     eff,
            "effective_source":    source_line,
            "outermost_frame":     outermost,
            "inline_chain":        frames,
            "module_bucket":       module_bucket,
            "origin_bucket":       origin_bucket,
            "blockers":            prior.get("blockers", []),
            "notes":               prior.get("notes", ""),
        })

    sites.sort(key=lambda s: (
        s["module_bucket"],
        s["outermost_frame"].get("file") or "",
        s["outermost_frame"].get("line") or 0,
        int(s["address"], 16),
    ))

    elf_mtime = datetime.fromtimestamp(args.elf.stat().st_mtime, tz=timezone.utc).isoformat()
    generated_at = datetime.now(tz=timezone.utc).isoformat()

    doc = {
        "meta": {
            "binary": str(args.elf),
            "elf_mtime": elf_mtime,
            "generated_at": generated_at,
            "total_sites": len(sites),
        },
        "sites": sites,
    }

    args.out.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"Wrote {len(sites)} sites to {args.out}", file=sys.stderr)
    if prior_labels:
        print(f"  preserved labels on {preserved} site(s) "
              f"(from {len(prior_labels)} unique source locations)", file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
