#!/usr/bin/env python3
"""reannotate_flux.py -- Step 0 of the no-panic verification pipeline.

Audits and re-anchors the ``// FLUX-`` annotations in the Tock source against the
current release binary, so downstream steps can join on ``addr=`` / ``addrs=[...]``
reliably even though LTO drifts addresses on every rebuild.

This is a faithful implementation of ``tools/reannotate_flux_spec.md``; read that
first. The short version:

  * Markers are anchored by the comment's *physical* line number, not by any value
    they carry. ``line=`` is a stale cache and is stripped on rewrite.
  * Precise markers (FLUX-TODO / FLUX-OPT) join to the nearest panic_survey record
    at or below the comment (within 5 lines) with matching (file, flavor).
  * FN-LEVEL markers join on (file, func, flavor) against all records (both
    LTO-line-lost and known-line — the latter covers panics whose predicate is
    a function-level concern, e.g. inlined callees), with the function derived
    from the comment's enclosing source fn.
  * The run is atomic: collect every violation across the whole tree, then write all
    rewrites only if there were zero violations, else write nothing.

Exit codes:
  0  clean -- rewrites written (or, with --dry-run, would have been written)
  1  violations found -- nothing written
  2  usage / I/O / survey error

See the "Usage" section of the spec for operational guidance.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field

# The full flavor vocabulary panic_survey.py can emit. The authoritative source is
# its classify_sink (the _SINK_FLAVOR dict values plus these three specials); we read
# the dict out of the file with ast at runtime and fall back to this snapshot if that
# fails. Validity is keyed on the *vocabulary*, not on which flavors happen to appear
# in a given build -- a flavor with no panic this build is still valid (it orphans,
# it is not malformed).
_FLAVOR_SPECIALS = {"assert", "optional_cell_unwrap", "other"}
_FLAVOR_FALLBACK = {
    "bounds", "explicit_panic", "slice_start", "slice_end", "slice_order",
    "div_by_zero", "rem_by_zero", "unwrap_option", "unwrap_result", "unwind",
} | _FLAVOR_SPECIALS


def tool_flavor_vocabulary(tools_dir: str) -> set[str]:
    """Extract panic_survey.py's flavor vocabulary without executing it."""
    path = os.path.join(tools_dir, "panic_survey.py")
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_SINK_FLAVOR" for t in node.targets
            ) and isinstance(node.value, ast.Dict):
                vals = {ast.literal_eval(v) for v in node.value.values}
                return vals | _FLAVOR_SPECIALS
    except (OSError, SyntaxError, ValueError):
        pass
    return set(_FLAVOR_FALLBACK)

# --------------------------------------------------------------------------- #
# Survey loading and the in-scope record set
# --------------------------------------------------------------------------- #


def norm_file(path: str) -> str:
    """Normalise a survey ``effective_frame.file`` (``./kernel/...``) or a repo
    path to a repo-relative path with no leading ``./``."""
    if path.startswith("./"):
        return path[2:]
    return path


@dataclass
class Record:
    address: str          # e.g. "0xfa3c" (normalised lowercase)
    file: str             # repo-relative, no leading ./
    line: int | None      # effective_frame.line; None == LTO line loss
    func: str             # effective_frame.func (binary asm owner after inlining)
    flavor: str           # sink_flavor


def load_survey(path: str) -> tuple[list[Record], set[str]]:
    """Return (in-scope records, valid flavor vocabulary).

    In-scope == Tock-local == ``effective_frame.file`` starts with ``./`` (step A
    of the handoff: identical to ``origin_bucket != 'stdlib'``; keyed on the path so
    it is robust to bucket-taxonomy drift). The flavor vocabulary is every distinct
    ``sink_flavor`` the survey emits (plus the ``mixed`` wildcard, added later).
    """
    with open(path) as fh:
        data = json.load(fh)
    sites = data["sites"] if isinstance(data, dict) else data
    records: list[Record] = []
    flavors: set[str] = set()
    for s in sites:
        ef = s["effective_frame"]
        f = ef.get("file", "")
        flavors.add(s["sink_flavor"])
        if not f.startswith("./"):  # stdlib / external -> out of scope
            continue
        records.append(
            Record(
                address=str(s["address"]).lower(),
                file=norm_file(f),
                line=ef.get("line"),
                func=ef.get("func", "") or "",
                flavor=s["sink_flavor"],
            )
        )
    return records, flavors


# --------------------------------------------------------------------------- #
# Source-file discovery (the audit scope)
# --------------------------------------------------------------------------- #

_SKIP_DIRS = {".git", "target", "node_modules", ".venv"}


def iter_rs_files(repo_root: str):
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if name.endswith(".rs"):
                full = os.path.join(dirpath, name)
                yield os.path.relpath(full, repo_root)


def discover_scope(repo_root: str, records: list[Record]) -> tuple[set[str], set[str]]:
    """Return (all in-scope files, files that actually contain a // FLUX- comment).

    Scope = union of (a) every in-scope record's file and (b) every repo file
    containing a ``// FLUX-`` comment. (b) is what catches orphaned annotations in
    files whose panics have since vanished.
    """
    from_records = {r.file for r in records}
    annotated: set[str] = set()
    for rel in iter_rs_files(repo_root):
        try:
            with open(os.path.join(repo_root, rel), encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        if "// FLUX-" in text or re.search(r"//+\s*FLUX-", text):
            annotated.add(rel)
    scope = {f for f in (from_records | annotated)
             if os.path.exists(os.path.join(repo_root, f))}
    return scope, annotated


def crate_of(path: str) -> str:
    """Derive the Flux crate name from a repo-relative file path."""
    parts = path.split("/")
    p0 = parts[0]
    if p0 == "kernel":
        return "kernel"
    if p0 in ("libraries", "arch", "chips") and len(parts) > 1:
        return parts[1]
    if p0 == "capsules" and len(parts) > 1:
        return "capsules-" + parts[1]
    if p0 == "boards" and len(parts) > 1:
        return "board-" + parts[1]
    return p0


# --------------------------------------------------------------------------- #
# Marker parsing
# --------------------------------------------------------------------------- #

_MARKER_START = re.compile(r"^\s*//+\s*FLUX-")
_FN_DECL = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:default\s+)?(?:const\s+)?(?:async\s+)?"
    r"(?:unsafe\s+)?(?:extern\s+\"[^\"]*\"\s+)?fn\s+(\w+)"
)
_HEX = re.compile(r"0x[0-9a-fA-F]+")

# Precise marker kinds and the FN-LEVEL kind, longest keyword first so the
# substring checks below don't mis-fire.
KIND_FN_LEVEL = "FN-LEVEL"
KIND_BLOCKED = "BLOCKED"
KIND_OPT = "OPT"
KIND_TODO = "TODO"
KIND_UNKNOWN = "UNKNOWN"


def classify_kind(text: str) -> str:
    if "FLUX-TODO-FN-LEVEL" in text:
        return KIND_FN_LEVEL
    if "FLUX-TODO-BLOCKED" in text:
        return KIND_BLOCKED
    if "FLUX-OPT" in text:
        return KIND_OPT
    if "FLUX-TODO" in text:
        return KIND_TODO
    return KIND_UNKNOWN


def field_value(text: str, name: str) -> str | None:
    """Read ``name=value`` (value stops at whitespace or ``]``); trailing punctuation
    such as a stray comma is stripped."""
    m = re.search(rf"\b{name}=([^\s\]]+)", text)
    if not m:
        return None
    return m.group(1).rstrip(",")


def has_singular_addr(text: str) -> bool:
    # \baddr= does not match addrs= (the 's' breaks the '=' adjacency).
    return re.search(r"\baddr=0x[0-9a-fA-F]+", text) is not None


@dataclass
class Marker:
    path: str            # repo-relative file
    start: int           # 0-based index of the kind line
    end: int             # 0-based index of the closing ] line (== start otherwise)
    lines: list[str]     # the marker's source lines (start..end inclusive)
    kind: str
    anchor: int          # 1-based physical line of the kind line

    # parsed fields
    flavor: str | None = None
    has_line: bool = False
    list_field: str | None = None     # 'addrs' | 'covers' | None
    addr_singular: str | None = None  # normalised lowercase, or None
    list_addrs: list[str] = field(default_factory=list)
    override_file: str | None = None  # FN-LEVEL: overrides the survey-join file

    @property
    def raw(self) -> str:
        return "\n".join(self.lines)

    @property
    def kind_line(self) -> str:
        return self.lines[0]


def parse_markers(path: str, text: str) -> list[Marker]:
    lines = text.split("\n")
    markers: list[Marker] = []
    i = 0
    n = len(lines)
    while i < n:
        if not _MARKER_START.match(lines[i]):
            i += 1
            continue
        start = i
        kind_line = lines[i]
        opener = re.search(r"\b(addrs|covers)=\[", kind_line)
        end = i
        if opener and "]" not in kind_line[opener.end() - 1:]:
            # multi-line list: advance to the first line carrying a ']'
            j = i + 1
            while j < n and "]" not in lines[j]:
                j += 1
            end = j if j < n else i
        block = lines[start:end + 1]
        joined = "\n".join(block)
        kind = classify_kind(kind_line)
        m = Marker(
            path=path, start=start, end=end, lines=block, kind=kind,
            anchor=start + 1,
        )
        m.flavor = field_value(kind_line, "flavor")
        m.has_line = re.search(r"\bline=\d+", kind_line) is not None
        m.override_file = field_value(kind_line, "file")
        m.addr_singular = (field_value(kind_line, "addr") or "").lower() or None \
            if has_singular_addr(kind_line) else None
        list_m = re.search(r"\b(addrs|covers)=\[", joined)
        if list_m:
            m.list_field = list_m.group(1)
            # collect addresses from the bracketed region only
            bracket = joined[list_m.end() - 1:]
            close = bracket.find("]")
            region = bracket[1:close] if close != -1 else bracket[1:]
            m.list_addrs = [a.lower() for a in _HEX.findall(region)]
        markers.append(m)
        i = end + 1
    return markers


# --------------------------------------------------------------------------- #
# Enclosing-function helpers (FN-LEVEL join + ambiguity carve-out)
# --------------------------------------------------------------------------- #


def enclosing_fn_below(lines: list[str], marker_start: int) -> tuple[str | None, int | None]:
    """The convention places a FN-LEVEL comment immediately above its ``fn``; scan
    down (skipping comment lines) for the first real fn declaration."""
    for idx in range(marker_start, min(marker_start + 12, len(lines))):
        s = lines[idx]
        if s.lstrip().startswith("//"):
            continue
        fm = _FN_DECL.match(s)
        if fm:
            return fm.group(1), idx
    return None, None


def enclosing_impl_type(lines: list[str], marker_start: int) -> str | None:
    """Walk upward from the marker to find the enclosing ``impl`` block's target
    type name. Returns the type identifier (e.g. ``MuxAES128CCM``) or ``None`` if
    the marker is not inside an impl block. Used to disambiguate ``fn`` names that
    appear in multiple impls (``MuxAES128CCM::crypt_done`` vs
    ``VirtualAES128CCM::crypt_done``) -- both would otherwise segment-match the
    same fn token in ``effective_frame.func``.

    Multi-line impl signatures are supported: the impl declaration is read from
    its opening ``impl`` keyword down to its body's ``{``, so trailing
    ``for MuxAES128CCM<...>`` on a continuation line is found.

    Brace-aware: an ``impl`` line is only considered enclosing if its body's
    ``{`` is before ``marker_start`` AND its matching ``}`` is at or after
    ``marker_start``. Rust impls don't nest, so the first enclosing impl found
    walking up is the only one possible; if a closer impl block has already
    closed, the walker continues upward."""
    for idx in range(marker_start, -1, -1):
        if not re.match(r"^\s*impl\b", lines[idx]):
            continue
        # Find this impl's body-opening `{` (on this line or up to 10 below).
        body_open_idx = None
        body_open_col = None
        for j in range(idx, min(idx + 10, len(lines))):
            if "{" in lines[j]:
                body_open_idx = j
                body_open_col = lines[j].index("{")
                break
        if body_open_idx is None or body_open_idx >= marker_start:
            continue
        # Walk from the body-opening `{` forward; track brace depth. If depth
        # falls to 0 before reaching `marker_start`, the impl body closed
        # earlier and this impl does NOT enclose the marker.
        depth = 0
        encloses = True
        for k in range(body_open_idx, marker_start):
            text = lines[k]
            if k == body_open_idx:
                text = text[body_open_col:]  # only count braces from the body opener
            code = re.sub(r"//.*", "", text)
            depth += code.count("{") - code.count("}")
            if k > body_open_idx and depth <= 0:
                encloses = False
                break
        if not encloses or depth <= 0:
            continue
        # Marker is inside this impl. Extract the target type from the signature.
        sig = " ".join(lines[idx:body_open_idx + 1])
        for_m = re.search(r"\bfor\s+(?:\w+::)*([A-Z]\w*)", sig)
        if for_m:
            return for_m.group(1)
        inh_m = re.match(r"^\s*impl(?:<[^>]*>)?\s+(?:\w+::)*([A-Z]\w*)", sig)
        if inh_m:
            return inh_m.group(1)
        return None
    return None


def fn_body_range(lines: list[str], decl_idx: int) -> tuple[int, int]:
    """Brace-count from a fn declaration to the end of its body. Returns 1-based
    inclusive (start, end) source line numbers. Comments are stripped before
    counting; this is a pragmatic counter, adequate for the carve-out check."""
    depth = 0
    started = False
    end = len(lines)
    for idx in range(decl_idx, len(lines)):
        code = re.sub(r"//.*", "", lines[idx])
        depth += code.count("{") - code.count("}")
        if "{" in code:
            started = True
        if started and depth <= 0:
            end = idx
            break
    return decl_idx + 1, end + 1


def tokens(s: str) -> set[str]:
    return set(re.findall(r"\w+", s))


# --------------------------------------------------------------------------- #
# Violations
# --------------------------------------------------------------------------- #


@dataclass
class Violation:
    kind: str          # 'malformed' | 'orphan' | 'ambiguous' | 'double' | 'overspecified' | 'unpaired'
    marker: Marker
    detail: str
    extra: Marker | None = None  # second marker for double-marker
    hint: str | None = None      # self-diagnosis: what's actually near the anchor

    def location(self) -> str:
        return f"{self.marker.path}:{self.marker.anchor}"


# Substring match: pairs match-arm shapes like
#   _ => { flux_support::assert(false); panic!(...) },
# as well as plain `flux_support::assert(...)` on its own line.
_ASSERT_PAIR = re.compile(r"\bflux_support::assert\s*\(")
# Inside the flux_support crate itself, `flux_support::assert(` doesn't resolve
# (a crate can't refer to itself by name); the canonical reference is
# `crate::assert(`. Accept that form for markers in files under flux_support/.
_ASSERT_PAIR_FLUX_SUPPORT = re.compile(r"\b(?:flux_support|crate)::assert\s*\(")


# --------------------------------------------------------------------------- #
# Core audit
# --------------------------------------------------------------------------- #


@dataclass
class Resolution:
    """A successful join: what the marker should be rewritten to."""
    marker: Marker
    new_addr: str | None = None        # for addr= form
    new_addrs: list[str] | None = None  # for addrs=/covers= form & FN-LEVEL
    carved: bool = False                # ambiguous addr= covered by a FN-LEVEL


class Auditor:
    def __init__(self, repo_root: str, records: list[Record], valid_flavors: set[str]):
        self.repo_root = repo_root
        self.records = records
        self.valid_flavors = valid_flavors
        # indices
        self.by_file_flavor: dict[tuple[str, str], list[Record]] = defaultdict(list)
        self.by_file: dict[str, list[Record]] = defaultdict(list)       # precise only
        self.nullline_by_file: dict[str, list[Record]] = defaultdict(list)
        # All records by file, regardless of line presence. FN-LEVEL markers
        # join against this set (both null-line LTO records and known-line
        # records where the predicate is structurally a function-level
        # concern — see spec step 6).
        self.all_by_file: dict[str, list[Record]] = defaultdict(list)
        for r in records:
            self.all_by_file[r.file].append(r)
            if r.line is not None:
                self.by_file_flavor[(r.file, r.flavor)].append(r)
                self.by_file[r.file].append(r)
            else:
                self.nullline_by_file[r.file].append(r)

        self.violations: list[Violation] = []
        self.resolutions: list[Resolution] = []
        # per-file cache of FN-LEVEL fn body ranges + claimed addrs, for the
        # carve-out check (spec exit condition 3). Each entry is
        # (start_line, end_line, frozenset_of_addrs) -- a precise marker whose
        # resolved address is in this addr set is "carved": left unchanged and
        # excluded from the double-marker check.
        self._fnlevel_claims: dict[str, list[tuple[int, int, frozenset[str]]]] = {}
        self._lines_cache: dict[str, list[str]] = {}

    # -- file pass ------------------------------------------------------------

    def audit_file(self, path: str):
        with open(os.path.join(self.repo_root, path), encoding="utf-8") as fh:
            text = fh.read()
        lines = text.split("\n")
        self._lines_cache[path] = lines
        markers = parse_markers(path, text)

        # Split into the three audit lanes. FN-LEVEL markers are audited first
        # so the per-fn-range claim set is available to the precise carve-out
        # check. Precise markers are processed in anchor-line order with a
        # per-flavor source-line claim set, so stacked markers in overlapping
        # windows pick distinct source lines (rebuild DWARF drift can
        # otherwise land two markers on the same nearest panic).
        precise: list[Marker] = []
        fnlevel: list[Marker] = []
        for m in markers:
            if m.kind == KIND_FN_LEVEL:
                fnlevel.append(m)
            elif m.kind in (KIND_TODO, KIND_OPT):
                precise.append(m)
            else:  # BLOCKED or UNKNOWN
                reason = ("deprecated FLUX-TODO-BLOCKED kind"
                          if m.kind == KIND_BLOCKED else "unrecognised // FLUX- kind")
                self.violations.append(Violation("malformed", m, reason))

        # Audit each FN-LEVEL marker, capturing its (fn-body range, claimed
        # addrs) so precise markers below can be carved out.
        claims: list[tuple[int, int, frozenset[str]]] = []
        for m in fnlevel:
            pre_count = len(self.resolutions)
            self._audit_fn_level(m, lines)
            _, decl = enclosing_fn_below(lines, m.start)
            if decl is None:
                continue
            rng = fn_body_range(lines, decl)
            if len(self.resolutions) > pre_count:
                res = self.resolutions[-1]
                addrs = frozenset(res.new_addrs or [])
            else:
                addrs = frozenset()
            claims.append((rng[0], rng[1], addrs))
        self._fnlevel_claims[path] = claims

        claimed: dict[str, set[int]] = defaultdict(set)
        for m in sorted(precise, key=lambda x: x.anchor):
            self._audit_precise(m, lines, claimed)
            self._audit_precise_pairing(m, lines)
        return markers

    # -- precise markers ------------------------------------------------------

    def _precise_malformed_reason(self, m: Marker) -> str | None:
        has_addr = m.addr_singular is not None
        has_list = m.list_field == "addrs"
        if m.list_field == "covers":
            return "covers= is FN-LEVEL-only; not valid on a precise marker"
        if m.override_file is not None:
            return "file= override is FN-LEVEL-only; not valid on a precise marker"
        if has_addr and has_list:
            return "carries both addr= and addrs=[...]"
        if not has_addr and not has_list:
            return "carries neither addr= nor addrs=[...] (free-text prose)"
        if m.flavor is None:
            return "missing flavor="
        if m.flavor == "mixed":
            return "flavor=mixed is valid only on FN-LEVEL markers"
        if m.flavor not in self.valid_flavors:
            return f"unknown flavor={m.flavor!r}"
        return None

    def _audit_precise(self, m: Marker, lines: list[str],
                       claimed: dict[str, set[int]]):
        reason = self._precise_malformed_reason(m)
        if reason is not None:
            self.violations.append(Violation("malformed", m, reason))
            return

        P = m.anchor
        # "Closest unclaimed nearest-panic line in window": filter out source
        # lines an earlier (lower-anchor) marker of this flavor already claimed
        # so stacked markers within five lines distribute to distinct panics.
        cands = [r for r in self.by_file_flavor[(m.path, m.flavor)]
                 if r.line is not None and P <= r.line <= P + 6
                 and r.line not in claimed[m.flavor]]
        if not cands:
            self.violations.append(Violation(
                "orphan", m,
                f"no record at anchor P={P} for (file={m.path}, flavor={m.flavor}) "
                f"within P..P+6",
                hint=self._orphan_hint_precise(m, claimed)))
            return

        matched_line = min(r.line for r in cands)
        claimed[m.flavor].add(matched_line)
        matched = [r for r in self.by_file_flavor[(m.path, m.flavor)]
                   if r.line == matched_line]
        addrs = sorted({r.address for r in matched})

        if self._carved_by_fn_level(m, addrs):
            # Spec exit condition 3: precise marker (either form) whose
            # resolved address(es) intersect a FN-LEVEL's claimed addrs in
            # the same enclosing fn is left unchanged and excluded from the
            # double-marker check.
            self.resolutions.append(Resolution(m, carved=True))
            return

        if m.addr_singular is not None:        # addr= form
            if len(addrs) == 1:
                self.resolutions.append(Resolution(m, new_addr=addrs[0]))
            else:
                self.violations.append(Violation(
                    "ambiguous", m,
                    f"line {matched_line} resolves to {len(addrs)} instructions: "
                    f"{', '.join(addrs)}"))
        else:                                   # addrs=[...] form
            if len(addrs) > 1:
                self.resolutions.append(Resolution(m, new_addrs=addrs))
            else:
                self.violations.append(Violation(
                    "overspecified", m,
                    f"line {matched_line} resolves to a single instruction {addrs[0]}; "
                    f"consider addr= form"))

    def _carved_by_fn_level(self, m: Marker, resolved_addrs: list[str]) -> bool:
        """Generalised exit-condition-3 carve-out: a precise marker (TODO or
        OPT, either `addr=` or `addrs=[...]` form) is carved out when it sits
        inside the body of a function that also carries a FN-LEVEL marker AND
        the precise marker's resolved address(es) intersect the FN-LEVEL
        marker's claimed addrs. Carved markers are left unchanged and
        excluded from the double-marker check."""
        resolved = set(resolved_addrs)
        for s, e, fn_addrs in self._fnlevel_claims.get(m.path, []):
            if s <= m.anchor <= e and resolved & fn_addrs:
                return True
        return False

    # -- precise-marker assert pairing (step 7) -------------------------------

    def _audit_precise_pairing(self, m: Marker, lines: list[str]):
        """Every precise marker (TODO/OPT) must be paired with a
        ``flux_support::assert(...)`` call below it. Scan downward from the
        line after the marker block, skipping blank lines and any lines whose
        stripped form starts with ``//`` (this naturally skips the marker's
        own continuation notes and further stacked precise markers). The
        first non-skipped source line must contain a ``flux_support::assert(``
        call; otherwise the marker is unpaired. Stacked precise markers above
        the same panic share one assert — each resolves to it independently
        via this same scan."""
        n = len(lines)
        idx = m.end + 1
        pair_re = (_ASSERT_PAIR_FLUX_SUPPORT if m.path.startswith("flux_support/")
                   else _ASSERT_PAIR)
        while idx < n:
            s = lines[idx]
            stripped = s.strip()
            if stripped == "" or stripped.startswith("//"):
                idx += 1
                continue
            if pair_re.search(s):
                return
            preview = stripped if len(stripped) <= 80 else stripped[:77] + "..."
            self.violations.append(Violation(
                "unpaired", m,
                f"first source line below marker has no flux_support::assert(...): "
                f"{preview}"))
            return
        self.violations.append(Violation(
            "unpaired", m, "no source line below marker (end of file)"))

    # -- orphan self-diagnosis ------------------------------------------------

    def _orphan_hint_precise(self, m: Marker,
                             claimed: dict[str, set[int]] | None = None) -> str:
        """Say what is actually near a precise orphan's anchor, so the fix is
        obvious (wrong flavor / comment drifted / line-lost / panic gone)."""
        P = m.anchor
        claimed_for_flavor = claimed[m.flavor] if claimed is not None else set()
        win_all = sorted(r.line for r in self.by_file_flavor.get((m.path, m.flavor), [])
                         if r.line is not None and P <= r.line <= P + 6)
        if win_all and all(L in claimed_for_flavor for L in win_all):
            return (f"window P..P+6 has {m.flavor} panic(s) at {win_all} but all are "
                    f"claimed by earlier markers in this file -> likely a duplicate "
                    f"stacked marker, delete or re-target")
        win = sorted((r.line, r.flavor) for r in self.by_file.get(m.path, [])
                     if P <= r.line <= P + 6)
        others = [f"{l}={fl}" for l, fl in win if fl != m.flavor]
        if others:
            return (f"window P..P+6 has a different-flavor panic ({', '.join(others)}); "
                    f"marker says {m.flavor} -> flavor mismatch, fix flavor=")
        same = sorted((r.line for r in self.by_file_flavor.get((m.path, m.flavor), [])),
                      key=lambda L: abs(L - P))
        if same:
            L = same[0]
            return (f"nearest {m.flavor} panic at line {L} (gap {L - P:+d}, outside "
                    f"P..P+6) -> move comment to within 6 lines above the panic")
        if self.nullline_by_file.get(m.path):
            return (f"no precise {m.flavor} panic in file, but it has line-lost records "
                    f"-> may belong on a FLUX-TODO-FN-LEVEL")
        return (f"no {m.flavor} panic anywhere in this file -> panic likely gone "
                f"(delete; invariant 1 backstops)")

    def _orphan_hint_fn_level(self, m: Marker, fn_name, decl) -> str:
        if decl is None:
            return "could not locate the enclosing fn below the comment"
        s, e = fn_body_range(self._lines_cache[m.path], decl)
        in_range = sorted({(r.line, r.flavor) for r in self.by_file.get(m.path, [])
                           if s <= r.line <= e})
        if in_range:
            shown = ", ".join(f"{l}={fl}" for l, fl in in_range[:6])
            more = " ..." if len(in_range) > 6 else ""
            return (f"fn {fn_name} now has PRECISE panic(s) at {shown}{more} "
                    f"-> convert FN-LEVEL to precise (debuginfo recovered the line)")
        return f"fn {fn_name} has no panic in the current binary -> safe to delete"

    # -- FN-LEVEL markers -----------------------------------------------------

    def _audit_fn_level(self, m: Marker, lines: list[str]):
        if m.list_field is None:
            self.violations.append(Violation(
                "malformed", m, "FN-LEVEL marker carries no addrs=[...] / covers=[...]"))
            return
        if m.flavor is None:
            self.violations.append(Violation("malformed", m, "missing flavor="))
            return
        if m.flavor != "mixed" and m.flavor not in self.valid_flavors:
            self.violations.append(Violation(
                "malformed", m, f"unknown flavor={m.flavor!r}"))
            return

        fn_name, decl = enclosing_fn_below(lines, m.start)
        impl_type = enclosing_impl_type(lines, m.start)
        wildcard = (m.flavor == "mixed")
        # Optional file= override: addr2line can attribute a line-lost panic to
        # an inlined callee's file (binary symbol owns it but origin frame is
        # elsewhere); pointing the FN-LEVEL marker at the origin file via
        # `file=` lets it claim those addresses.
        join_file = norm_file(m.override_file) if m.override_file else m.path
        # Spec step 6: FN-LEVEL joins against all records in the file matching
        # (func, flavor), regardless of whether the record carries a known line
        # or is LTO-line-lost. The known-line case covers panics attributed to
        # a specific source line whose predicate is still a function-level
        # concern (e.g. a callee inlined into this function). When the marker
        # sits inside an `impl Foo` block, the join also requires the impl-type
        # name to appear in the record's func -- a bare `fn_name in tokens(...)`
        # match cross-pulls panics from same-named methods on different types
        # (e.g. `MuxAES128CCM::crypt_done` vs `VirtualAES128CCM::crypt_done`).
        cands = [
            r for r in self.all_by_file.get(join_file, [])
            if (wildcard or r.flavor == m.flavor)
            and fn_name is not None and fn_name in tokens(r.func)
            and (impl_type is None or impl_type in tokens(r.func))
        ]
        if not cands:
            self.violations.append(Violation(
                "orphan", m,
                f"(file={join_file}, func~={fn_name}, flavor={m.flavor}) matched no "
                f"record",
                hint=self._orphan_hint_fn_level(m, fn_name, decl)))
            return
        addrs = sorted({r.address for r in cands})
        self.resolutions.append(Resolution(m, new_addrs=addrs))

    # -- cross-marker checks --------------------------------------------------

    def check_double_markers(self):
        claims: dict[str, list[Marker]] = defaultdict(list)
        for res in self.resolutions:
            if res.carved:
                continue  # excluded from the double-marker check (exit 3 rule)
            addrs = [res.new_addr] if res.new_addr else (res.new_addrs or [])
            for a in addrs:
                if res.marker not in claims[a]:
                    claims[a].append(res.marker)
        seen: set[tuple[int, int]] = set()
        for a, mks in claims.items():
            if len(mks) > 1:
                # report each distinct pair once
                for x in range(len(mks)):
                    for y in range(x + 1, len(mks)):
                        key = (id(mks[x]), id(mks[y]))
                        if key in seen:
                            continue
                        seen.add(key)
                        self.violations.append(Violation(
                            "double", mks[x],
                            f"shares address {a} with {mks[y].path}:{mks[y].anchor}",
                            extra=mks[y]))


# --------------------------------------------------------------------------- #
# Rewriting
# --------------------------------------------------------------------------- #


def _strip_line_field(text: str) -> str:
    """Remove a ``line=NNN`` token and one adjacent space, leaving single spacing."""
    return re.sub(r"\s*\bline=\d+", "", text, count=1)


def _comment_indent(line: str) -> str:
    m = re.match(r"(\s*)//", line)
    return m.group(1) if m else ""


def _render_list_block(marker: Marker, new_addrs: list[str]) -> list[str]:
    """Produce the rewritten source lines for a list-bearing marker (precise
    addrs=/covers= or FN-LEVEL), normalising the field name to ``addrs`` and the
    ``line=`` strip. Preserves single-line vs multi-line shape."""
    kind_line = marker.lines[0]
    opener = re.search(r"\b(addrs|covers)=\[", kind_line)
    head = _strip_line_field(kind_line[:opener.start()]).rstrip() + " "
    addr_text = ", ".join(new_addrs)

    if marker.start == marker.end:
        # single line: ... addrs=[a, b] <tail>
        close = kind_line.index("]", opener.end() - 1)
        tail = kind_line[close + 1:]
        return [f"{head}addrs=[{addr_text}]{tail}"]

    # multi-line: head + wrapped continuation lines + closing ] line
    indent = _comment_indent(kind_line)
    cont_prefix = f"{indent}//     "
    end_line = marker.lines[-1]
    close = end_line.index("]")
    tail = end_line[close + 1:]
    out = [f"{head}addrs=["]
    per_line = 6
    for i in range(0, len(new_addrs), per_line):
        chunk = new_addrs[i:i + per_line]
        out.append(cont_prefix + ", ".join(chunk) + ",")
    out.append(f"{indent}// ]{tail}")
    return out


def render_marker(marker: Marker, res: Resolution | None) -> list[str]:
    """Return the rewritten source lines for a marker. ``res`` is None when the
    marker had no successful resolution (malformed/orphan/etc.) -- in that case it is
    returned unchanged (the run will abort before any write anyway)."""
    if res is None or res.carved:
        return list(marker.lines)  # leave unchanged (carve-out / unresolved)

    if res.new_addrs is not None or marker.list_field is not None:
        return _render_list_block(marker, res.new_addrs or marker.list_addrs)

    # single addr= form
    line = _strip_line_field(marker.lines[0])
    line = re.sub(r"\baddr=0x[0-9a-fA-F]+", f"addr={res.new_addr}", line)
    return [line]


def rewrite_file(repo_root: str, path: str, markers: list[Marker],
                 res_by_marker: dict[int, Resolution]) -> bool:
    """Apply rewrites to one file. Returns True if the file content changed."""
    full = os.path.join(repo_root, path)
    with open(full, encoding="utf-8") as fh:
        text = fh.read()
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    marker_by_start = {m.start: m for m in markers}
    while i < len(lines):
        m = marker_by_start.get(i)
        if m is not None:
            out.extend(render_marker(m, res_by_marker.get(id(m))))
            i = m.end + 1
        else:
            out.append(lines[i])
            i += 1
    new_text = "\n".join(out)
    if new_text != text:
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        return True
    return False


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def print_scope_summary(markers: list[Marker]):
    files = sorted({m.path for m in markers})
    crates = sorted({crate_of(f) for f in files})
    print(f"auditing {len(markers)} annotations across {len(files)} files in "
          f"{len(crates)} crates: [{', '.join(crates)}]")


def report_violations(violations: list[Violation]) -> None:
    order = ["malformed", "orphan", "ambiguous", "overspecified", "unpaired", "double"]
    label = {
        "malformed": "MALFORMED",
        "orphan": "ORPHANED",
        "ambiguous": "AMBIGUOUS",
        "overspecified": "OVER-SPECIFIED",
        "unpaired": "UNPAIRED-ASSERT",
        "double": "DOUBLE-MARKER",
    }
    by_kind: dict[str, list[Violation]] = defaultdict(list)
    for v in violations:
        by_kind[v.kind].append(v)
    print("\n=== violations ===")
    for k in order:
        vs = by_kind.get(k, [])
        print(f"  {label[k]}: {len(vs)}")
    print(f"  TOTAL: {len(violations)}")
    for k in order:
        vs = by_kind.get(k, [])
        if not vs:
            continue
        print(f"\n--- {label[k]} ({len(vs)}) ---")
        for v in vs:
            print(f"  {v.location()}  {v.detail}")
            print(f"      {v.marker.kind_line.strip()}")
            if v.hint:
                print(f"      hint: {v.hint}")


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


def ensure_survey(args) -> str:
    if args.survey:
        if not os.path.exists(args.survey):
            sys.exit(f"error: --survey {args.survey} does not exist")
        return args.survey
    # invoke panic_survey.py (per spec step 1)
    here = os.path.dirname(os.path.abspath(__file__))
    cmd = [sys.executable, os.path.join(here, "panic_survey.py")]
    if args.skip_build:
        cmd.append("--skip-build")
    if args.elf:
        cmd += ["--elf", args.elf]
    print(f"running: {' '.join(cmd)}", file=sys.stderr)
    subprocess.run(cmd, check=True)
    out = os.path.join(here, "panic_survey.json")
    if not os.path.exists(out):
        sys.exit("error: panic_survey.py did not produce panic_survey.json")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--survey", help="use an existing panic_survey.json instead of "
                                     "invoking panic_survey.py")
    ap.add_argument("--skip-build", action="store_true",
                    help="passthrough to panic_survey.py")
    ap.add_argument("--elf", help="passthrough to panic_survey.py (--skip-build path)")
    ap.add_argument("--repo-root", default=None,
                    help="repo root (default: parent of tools/)")
    ap.add_argument("--dry-run", action="store_true",
                    help="audit and report, but never write (even on a clean tree)")
    args = ap.parse_args(argv)

    repo_root = args.repo_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo_root = os.path.abspath(repo_root)

    survey_path = ensure_survey(args)
    records, present_flavors = load_survey(survey_path)
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    valid_flavors = tool_flavor_vocabulary(tools_dir) | present_flavors

    auditor = Auditor(repo_root, records, valid_flavors)
    scope, _annotated = discover_scope(repo_root, records)

    all_markers: list[Marker] = []
    markers_by_file: dict[str, list[Marker]] = {}
    for path in sorted(scope):
        markers = auditor.audit_file(path)
        if markers:
            markers_by_file[path] = markers
            all_markers.extend(markers)

    auditor.check_double_markers()

    # scope summary FIRST (audit trail, visible even on a non-zero exit)
    print_scope_summary(all_markers)

    if auditor.violations:
        report_violations(auditor.violations)
        print(f"\n{len(auditor.violations)} violation(s); no changes written.")
        return 1

    # zero violations -> write all rewrites (unless --dry-run)
    res_by_marker = {id(r.marker): r for r in auditor.resolutions}
    changed = []
    if args.dry_run:
        # count what *would* change without touching disk
        for path, markers in markers_by_file.items():
            with open(os.path.join(repo_root, path), encoding="utf-8") as fh:
                text = fh.read()
            lines = text.split("\n")
            out: list[str] = []
            i = 0
            marker_by_start = {m.start: m for m in markers}
            while i < len(lines):
                m = marker_by_start.get(i)
                if m is not None:
                    out.extend(render_marker(m, res_by_marker.get(id(m))))
                    i = m.end + 1
                else:
                    out.append(lines[i])
                    i += 1
            if "\n".join(out) != text:
                changed.append(path)
        print(f"\nclean: would rewrite {len(changed)} file(s) "
              f"({len(auditor.resolutions)} markers resolved). [--dry-run: no writes]")
        return 0

    for path, markers in markers_by_file.items():
        if rewrite_file(repo_root, path, markers, res_by_marker):
            changed.append(path)
    print(f"\nclean: rewrote {len(changed)} file(s) "
          f"({len(auditor.resolutions)} markers resolved).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
