#!/usr/bin/env python3
"""generate_missing_markers.py -- one-shot bulk marker generator.

NOT part of the pipeline. It reads ``invariant1_report.json`` (step 1's output)
and mechanically adds ``// FLUX-`` comments for the *straightforward*
``missing_comment`` panic instructions, leaving the gnarly cases as a printed
manual worklist. It emits comments only -- never ``flux_support::assert(...)``
(that is invariant 2's manual pass).

Workflow it sits inside::

    step 0 -> step 1 (N missing) -> THIS -> step 0 (re-anchor) -> step 1 (exit 0
    modulo manually-handled cases)

What it generates, per ``missing_comment`` entry:

  * Precise markers (source line survived LTO, ``effective_frame.line`` non-null):
    ``// FLUX-TODO addr=0x.. flavor=..`` one line above the panic, at the panic's
    indentation. Instructions sharing (file, line, flavor) collapse to one
    ``addrs=[..]`` marker; differing flavors at one line get one marker each.

  * FN-LEVEL markers (line lost, ``effective_frame.line`` null): grouped by the
    *resolved source fn-declaration line* (NOT by ``effective_frame.func``, which
    is the post-LTO asm owner and can differ per monomorph/impl). Addrs and
    flavors are unioned across the grouped funcs; one flavor -> that flavor,
    several -> ``flavor=mixed``. Placed immediately above the ``fn`` line, below
    any preceding attributes / doc comments.

Skip rules (a skipped site is a manual worklist line, never a broken edit):

  * shift-orphan: an insertion would shift an existing precise marker's panic past
    its ``[P, P+5]`` anchoring window. Computed precisely from the report's
    (annotation.line, source.line) pairs -- not a crude window heuristic. A
    full-line comment above any panic is otherwise syntactically valid (match
    arms, macro bodies, block openers all accept a leading comment), so the
    syntactic skip rules of earlier versions are gone.
  * FN-LEVEL: the fn name can't be located. When multiple ``fn <name>`` match,
    the func's ``<Type as Trait>::method`` shape disambiguates by trait; if that
    still doesn't resolve to one, skip.
  * FN-LEVEL: the fn already carries a FN-LEVEL marker (would double). Fixing
    that one requires editing the existing marker -- intentionally out of scope
    for an insertion-only generator.

Idempotency: existing ``// FLUX-`` comments are never edited or removed; any addr
already covered by a precise or FN-LEVEL marker is skipped. Safe to re-run.

Atomicity: all edits are computed against an in-memory snapshot and written only
if every file/line validates; a single failure writes nothing.

Exit codes:
  0  ran successfully (any number of skips is fine -- skips are for the human)
  1  an actual error (a report file/line that doesn't match the tree); nothing written
  2  usage / I/O / report-parse error; nothing written
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field

# Reuse step 0's parser, file discovery, fn-decl regex and kind constants so the
# idempotency check and fn-location heuristic match the rest of the pipeline.
from reannotate_flux import (  # noqa: E402
    KIND_FN_LEVEL,
    KIND_OPT,
    KIND_TODO,
    _FN_DECL,
    discover_scope,
    enclosing_fn_below,
    parse_markers,
)


# --------------------------------------------------------------------------- #
# fn-name extraction from a post-LTO asm-owner func path
# --------------------------------------------------------------------------- #


def method_name(func: str) -> str | None:
    """Last bare-identifier segment of an ``effective_frame.func`` path, skipping
    trailing ``{closure#N}`` and turbofish ``<...>`` blobs.

    ``<kernel::kernel::Kernel>::kernel_loop::<nrf52840dk::Platform, ...>``
        -> ``kernel_loop``
    ``<Console as TransmitClient>::transmitted_buffer`` -> ``transmitted_buffer``
    """
    depth = 0
    seg = ""
    segs: list[str] = []
    i = 0
    while i < len(func):
        c = func[i]
        if c == "<":
            depth += 1
            seg += c
        elif c == ">":
            depth -= 1
            seg += c
        elif c == ":" and depth == 0 and i + 1 < len(func) and func[i + 1] == ":":
            segs.append(seg)
            seg = ""
            i += 2
            continue
        else:
            seg += c
        i += 1
    segs.append(seg)
    for s in reversed(segs):
        if re.fullmatch(r"\w+", s):
            return s
    return None


# --------------------------------------------------------------------------- #
# Marker rendering
# --------------------------------------------------------------------------- #

def _sorted_addrs(addrs) -> list[str]:
    return sorted({a.lower() for a in addrs}, key=lambda a: int(a, 16))


def render_marker(indent: str, kind_kw: str, flavor: str, addrs: list[str]) -> list[str]:
    """Render a one-line marker. ``kind_kw`` is ``FLUX-TODO`` or ``FLUX-TODO-FN-LEVEL``.
    Single addr -> ``addr=`` form; otherwise ``addrs=[..]``.

    Always a single line, even for long lists. A precise marker is anchored by its
    kind line and its panic must fall in step 0's ``[P, P+5]`` window; a multi-line
    block taller than 5 lines would push the panic out and self-orphan (e.g. grant's
    50-monomorph site). One line keeps the panic at P+1 regardless of list length,
    and matches step 0's single-line-preserving rewrite, so it is stable across runs.
    """
    addrs = _sorted_addrs(addrs)
    if kind_kw == "FLUX-TODO" and len(addrs) == 1:
        return [f"{indent}// {kind_kw} addr={addrs[0]} flavor={flavor}"]
    return [f"{indent}// {kind_kw} addrs=[{', '.join(addrs)}] flavor={flavor}"]


# --------------------------------------------------------------------------- #
# Skip-rule predicates (precise markers)
# --------------------------------------------------------------------------- #


def _indent_of(line: str) -> str:
    return re.match(r"\s*", line).group(0)


_IMPL_LINE = re.compile(r"^\s*(?:unsafe\s+)?impl\b")


def _impl_block_uses_trait(lines: list[str], fn_line_1based: int, trait: str) -> bool:
    """Scan upward from a ``fn`` decl line to find its enclosing ``impl ... for ...``
    and check whether ``trait`` appears as a whole word in the trait position.
    Tolerant of multi-line impl headers; bails after 60 lines.
    """
    tok = re.compile(rf"\b{re.escape(trait)}\b")
    for k in range(fn_line_1based - 2, max(-1, fn_line_1based - 62), -1):
        if k < 0:
            return False
        L = lines[k]
        if _IMPL_LINE.match(L):
            header = L
            j = k
            while " for " not in header and j + 1 < len(lines) and "{" not in lines[j]:
                j += 1
                header += " " + lines[j]
            head = header.split(" for ", 1)[0] if " for " in header else header
            return tok.search(head) is not None
    return False


def trait_from_func(func: str) -> str | None:
    """For ``<Type as Trait>::method``-shaped func paths, return ``Trait``'s last
    identifier segment. ``None`` if the func doesn't carry a trait. Walks the path
    respecting ``<...>`` nesting so ``<Type<Generic::Path> as Trait>::m`` works.
    """
    depth = 0
    i = 0
    while i < len(func):
        c = func[i]
        if c == "<":
            depth += 1
        elif c == ">":
            depth -= 1
        elif depth >= 1 and func[i:i + 4] == " as ":
            j = i + 4
            d = depth
            start = j
            while j < len(func):
                if func[j] == "<":
                    d += 1
                elif func[j] == ">":
                    d -= 1
                    if d < depth:
                        path = func[start:j]
                        for s in reversed(path.split("::")):
                            s = re.sub(r"<.*$", "", s.strip())
                            if re.fullmatch(r"\w+", s):
                                return s
                        return None
                j += 1
            return None
        i += 1
    return None


# --------------------------------------------------------------------------- #
# Edits, skips, errors
# --------------------------------------------------------------------------- #


@dataclass
class Edit:
    file: str
    at: int                 # 1-based source line; insert block immediately before it
    lines: list[str]        # rendered marker lines


@dataclass
class Skip:
    file: str
    line: object            # int or None
    addrs: list[str]
    flavor: str
    reason: str


@dataclass
class Generator:
    repo_root: str
    report: dict
    edits: list[Edit] = field(default_factory=list)
    skips: list[Skip] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    n_precise: int = 0
    n_fnlevel: int = 0

    def __post_init__(self):
        self._lines: dict[str, list[str]] = {}
        # existing-marker state, parsed once from the whole tree (step 0's parser)
        self.covered: set[str] = set()
        self.fnlevel_fnlines: dict[str, set[int]] = defaultdict(set)
        _scope, annotated = discover_scope(self.repo_root, [])
        for path in sorted(annotated):
            text = self._read(path)
            if text is None:
                continue
            file_lines = text.split("\n")
            for m in parse_markers(path, text):
                if m.addr_singular:
                    self.covered.add(m.addr_singular)
                for a in m.list_addrs:
                    self.covered.add(a)
                if m.kind == KIND_FN_LEVEL:
                    _name, decl = enclosing_fn_below(file_lines, m.start)
                    if decl is not None:
                        self.fnlevel_fnlines[path].add(decl + 1)  # 1-based fn line

        # The shift-aware safety check needs every existing precise marker's anchor
        # (kind line m) and the panic line it covers (q). The report carries both
        # for each comment_precise obligation: annotation.line = m, source.line = q.
        # Dedup by (m, q): multi-instruction markers produce one obligation per addr
        # but share m and q. FN-LEVEL markers are anchored by fn name, not the line
        # window, so inserts never orphan them and they're excluded here.
        self.precise_anchors: dict[str, list[tuple[int, int]]] = defaultdict(list)
        seen: set[tuple[str, int, int]] = set()
        for o in self.report.get("obligations", []):
            if o.get("status") != "comment_precise":
                continue
            ann = o.get("annotation") or {}
            m_line = ann.get("line")
            q_line = o["source"].get("line")
            f = o["source"]["file"]
            if m_line is None or q_line is None:
                continue
            key = (f, m_line, q_line)
            if key in seen:
                continue
            seen.add(key)
            self.precise_anchors[f].append((m_line, q_line))

    # -- file access ----------------------------------------------------------

    def _read(self, path: str) -> str | None:
        if path in self._lines:
            return "\n".join(self._lines[path])
        full = os.path.join(self.repo_root, path)
        try:
            with open(full, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            return None
        self._lines[path] = text.split("\n")
        return text

    def _file_lines(self, path: str) -> list[str] | None:
        if self._read(path) is None:
            return None
        return self._lines[path]

    # -- precise --------------------------------------------------------------

    def plan_precise(self, entries: list[dict]):
        """Plan precise inserts, then check shift safety against every existing
        precise marker (m, q): an insertion of N lines at P with m < P <= q shifts
        the existing marker's panic by N, so we need ``(q - m) + sum_added <= 5``
        or step 0's anchoring window orphans it on the next run. Only insertions
        that violate this are skipped; the old syntactic skips (`{`, `=>`, blank,
        FLUX-above) are dropped because a full-line comment above any panic is
        syntactically valid and step 0 anchors it cleanly.
        """
        # group by (file, P); within a (file, P), by flavor
        by_line: dict[tuple[str, int], dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for o in entries:
            a = o["addr"].lower()
            if a in self.covered:
                continue
            by_line[(o["source"]["file"], o["source"]["line"])][o["sink_flavor"]].append(a)

        # Build candidate insertions; N = number of flavor-markers = lines added at P.
        @dataclass
        class _Cand:
            f: str
            P: int
            by_flavor: dict[str, list[str]]
            N: int

        candidates: list[_Cand] = []
        for (f, P), by_flavor in sorted(by_line.items()):
            by_flavor = {fl: addrs for fl, addrs in by_flavor.items() if addrs}
            if not by_flavor:
                continue
            lines = self._file_lines(f)
            if lines is None:
                self.errors.append(f"{f}: file referenced by report not found")
                continue
            if not (1 <= P <= len(lines)):
                self.errors.append(f"{f}:{P}: panic line out of range (file has {len(lines)} lines)")
                continue
            candidates.append(_Cand(f, P, by_flavor, len(by_flavor)))

        # Shift-safety: for each existing precise marker (m, q) in a file, find
        # candidates with m < P <= q (insertions between marker and its panic);
        # if the cumulative shift would push gap past 5, all those candidates are
        # unsafe. Single pass (no iteration to "skip some, keep others") because
        # any clustering in the current data is intentionally treated as a unit.
        unsafe: dict[int, str] = {}
        for f_anchor, anchors in self.precise_anchors.items():
            file_idx = [(i, c) for i, c in enumerate(candidates) if c.f == f_anchor]
            for (m, q) in anchors:
                in_range = [(i, c) for i, c in file_idx if m < c.P <= q]
                added = sum(c.N for _, c in in_range)
                if (q - m) + added > 5:
                    why = (f"would orphan existing marker@{m} (covers panic@{q}, "
                           f"gap {q - m}; +{added}-line insertion shifts it past P+5)")
                    for i, _ in in_range:
                        unsafe[i] = why

        for i, c in enumerate(candidates):
            if i in unsafe:
                for fl, addrs in sorted(c.by_flavor.items()):
                    self.skips.append(Skip(c.f, c.P, _sorted_addrs(addrs), fl, unsafe[i]))
                continue
            indent = _indent_of(self._file_lines(c.f)[c.P - 1])
            block: list[str] = []
            for fl, addrs in sorted(c.by_flavor.items()):
                block += render_marker(indent, "FLUX-TODO", fl, addrs)
                self.n_precise += 1
            self.edits.append(Edit(c.f, c.P, block))

    # -- fn-level -------------------------------------------------------------

    def plan_fn_level(self, entries: list[dict]):
        # resolve each entry to a source fn-declaration line, then group by that
        groups: dict[tuple[str, int], dict] = {}
        for o in entries:
            a = o["addr"].lower()
            if a in self.covered:
                continue
            f = o["source"]["file"]
            func = o["source"]["func"]
            lines = self._file_lines(f)
            if lines is None:
                self.errors.append(f"{f}: file referenced by report not found")
                continue
            name = method_name(func)
            decls = [i + 1 for i, L in enumerate(lines)
                     if (_FN_DECL.match(L) and _FN_DECL.match(L).group(1) == name)] if name else []
            if len(decls) > 1:
                # Multiple `fn <name>` in the file. The func string carries
                # `<Type as Trait>::method` for trait-impl methods; pick the decl
                # inside an `impl <Trait> for ...` block. Resolves the i2c
                # command_complete case (master vs slave client impls).
                trait = trait_from_func(func)
                if trait is not None:
                    filtered = [d for d in decls if _impl_block_uses_trait(lines, d, trait)]
                    if len(filtered) == 1:
                        decls = filtered
            if len(decls) != 1:
                why = (f"fn {name!r} not found in file" if not decls
                       else f"fn {name!r} ambiguous at lines {decls}")
                self.skips.append(Skip(f, None, [a], o["sink_flavor"], why))
                continue
            fn_line = decls[0]
            if fn_line in self.fnlevel_fnlines.get(f, set()):
                self.skips.append(Skip(f, fn_line, [a], o["sink_flavor"],
                                       f"fn {name!r} already has a FN-LEVEL marker"))
                continue
            g = groups.setdefault((f, fn_line), {"addrs": [], "flavors": set()})
            g["addrs"].append(a)
            g["flavors"].add(o["sink_flavor"])

        for (f, fn_line), g in sorted(groups.items()):
            lines = self._file_lines(f)
            flavor = next(iter(g["flavors"])) if len(g["flavors"]) == 1 else "mixed"
            indent = _indent_of(lines[fn_line - 1])
            block = render_marker(indent, "FLUX-TODO-FN-LEVEL", flavor, g["addrs"])
            self.edits.append(Edit(f, fn_line, block))
            self.n_fnlevel += 1

    # -- apply (atomic) -------------------------------------------------------

    def apply(self) -> int:
        """Write all edits, or none if any error was recorded. Returns count of
        files changed."""
        by_file: dict[str, list[Edit]] = defaultdict(list)
        for e in self.edits:
            by_file[e.file].append(e)
        new_content: dict[str, str] = {}
        for f, edits in by_file.items():
            lines = list(self._file_lines(f))
            # group edits sharing an insertion line, then apply bottom-up so
            # earlier 1-based indices stay valid
            at_blocks: dict[int, list[str]] = defaultdict(list)
            for e in edits:
                at_blocks[e.at] += e.lines
            for at in sorted(at_blocks, reverse=True):
                lines[at - 1:at - 1] = at_blocks[at]
            new_content[f] = "\n".join(lines)
        for f, text in new_content.items():
            with open(os.path.join(self.repo_root, f), "w", encoding="utf-8") as fh:
                fh.write(text)
        return len(new_content)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def report_skips(skips: list[Skip]):
    if not skips:
        return
    print(f"\n=== skipped ({len(skips)}) -- manual worklist ===")
    for s in sorted(skips, key=lambda s: (s.file, s.line if s.line is not None else -1)):
        loc = f"{s.file}:{s.line}" if s.line is not None else f"{s.file}:(fn-level)"
        addrs = ", ".join(s.addrs)
        print(f"  {loc}  [{addrs}] flavor={s.flavor}")
        print(f"      {s.reason}")


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


def main(argv=None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", default=os.path.join(here, "invariant1_report.json"),
                    help="step 1 report to read (default: tools/invariant1_report.json)")
    ap.add_argument("--repo-root", default=None,
                    help="repo root (default: parent of tools/)")
    ap.add_argument("--dry-run", action="store_true",
                    help="plan and report, but never write")
    args = ap.parse_args(argv)

    repo_root = os.path.abspath(args.repo_root or os.path.dirname(here))

    if not os.path.exists(args.report):
        sys.exit(f"error: --report {args.report} does not exist")
    try:
        with open(args.report) as fh:
            report = json.load(fh)
    except (OSError, ValueError) as e:
        sys.exit(f"error: could not parse report {args.report}: {e}")

    missing = [o for o in report.get("obligations", []) if o.get("status") == "missing_comment"]
    precise = [o for o in missing if o["source"]["line"] is not None]
    fnlevel = [o for o in missing if o["source"]["line"] is None]
    print(f"generating markers for {len(missing)} missing instructions: "
          f"{len(precise)} precise candidates, {len(fnlevel)} fn-level candidates")

    gen = Generator(repo_root, report)
    gen.plan_precise(precise)
    gen.plan_fn_level(fnlevel)

    if gen.errors:
        print(f"\n=== errors ({len(gen.errors)}) -- nothing written ===")
        for e in gen.errors:
            print(f"  {e}")
        return 1

    report_skips(gen.skips)

    if args.dry_run:
        print(f"\n[--dry-run] would add {gen.n_precise} precise + {gen.n_fnlevel} "
              f"fn-level marker(s) across {len({e.file for e in gen.edits})} file(s); "
              f"{len(gen.skips)} site(s) skipped. No writes.")
        return 0

    changed = gen.apply()
    print(f"\nadded {gen.n_precise} precise + {gen.n_fnlevel} fn-level marker(s) "
          f"across {changed} file(s); {len(gen.skips)} site(s) skipped.")
    print("re-run step 0 (reannotate_flux.py) to anchor, then step 1 to re-check.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
