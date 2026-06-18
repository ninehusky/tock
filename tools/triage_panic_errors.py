#!/usr/bin/env python3
"""Step 2.5: triage panic-root Flux errors by whether they codegen a `bl panic`.

This is the deliverable for issue #15. Steps 0/1/2 establish that every binary
panic is *covered* by a marker and that each covered obligation is genuinely
*checked* by Flux. This post-processor asks the inverse question about the Flux
errors that step 2 already recorded: when Flux flags a panic-root error
("assertion might fail", a refinement-type error at a panic guard, ...), does
that error correspond to a *real codegened panic* on the nrf52840 board?

An error that looks like a panicking root but does **not** map to a binary
`bl panic` is deprioritizable -- Flux is being conservative about something the
optimizer proved cannot panic (or that the build dropped). This script buckets
every panic-root error in `invariant2_report.json`'s `flux_errors[]` into:

  1. panicking     -- the error sits at an assert paired with a *precise* FLUX
                      marker whose addr resolves to a `bl panic` in the survey
                      (these are the `assert_obligation` errors). NOTE: this is a
                      raw error count -- a `flux_support::assert(X)` and the implicit
                      op it guards both join to the same addr, so they appear as two
                      records for one panic. `summary.panicking_distinct` folds those
                      (and monomorph duplicates) by addr-set; per-record `dedup_role`
                      marks each as "primary" (counted) or "folded" (a duplicate).
  2. lookup_failed -- the error is inside a function carrying a FLUX-TODO-FN-LEVEL
                      marker. Those markers exist precisely because addr2line
                      could not pin the panic to a specific source line, so we
                      know a panic codegens *in the function* but not on which
                      line.
  3. non_panicking -- a panic-SHAPED error (assertion might fail: OOB / div-by-zero
                      / may-panic) with neither of the above: no precise assert and
                      not inside a fn-level-marked function. By step 1's completeness
                      (every Tock-local binary panic is marked), such an error does
                      not map to a codegened panic -> the deprioritizable set.
  4. precondition_failed -- same "neither of the above" residual as (3), but the
                      error is a `refinement type error` (a precondition that did
                      not hold) rather than a panic-shaped assertion. These do not
                      codegen a `bl panic` either, but they are typically pushed up
                      from / downstream of a real obligation elsewhere, so they are
                      split out from (3): they are NOT safe to deprioritize on sight.

Errors that match an *exclude* pattern (e.g. precondition diagnostics) are bucket
`excluded` and reported separately so the gate stays legible.

Errors that are Flux *tooling failures* rather than verification obligations --
"parameter inference error at function call" (Flux could not infer the refinement
arguments for a call) and "internal flux error: ..." (a Flux crash) -- are bucket
`tooling_error`. These are not panic-root (Flux never finished analyzing the code,
so there is no obligation to map to a `bl panic`), but they are also not the same
as a deliberately-excluded precondition diagnostic, so they get their own bucket
to stay visible. They do not fail the gate.

An E0999 that matches NEITHER a panic-root, an exclude, nor a tooling-error pattern
is bucket `unclassified` -- the message patterns are inherently brittle, so rather
than silently dropping a reworded diagnostic into `excluded` and under-counting, the
tool screams and exits non-zero (override with --allow-unclassified). On the
current tree this is 0; a non-zero count means the patterns need updating.

It runs standalone over the committed reports + source -- it does NOT re-run the
~14-minute Flux probe -- and writes a `triage` block back into the report
idempotently (re-running overwrites the single `triage` key in place).

Two gates make it fail loudly (non-zero exit) so CI catches regressions:
  - conservation (exit 3, non-overridable): the buckets must be a complete
    bijective partition of flux_errors[] -- every E0999 represented exactly once
    and bucketed. A violation is a tool bug. (summary.conservation_ok records it.)
  - unclassified (exit 1, --allow-unclassified to override): every E0999 must
    match a panic-root or exclude pattern; a miss means the brittle patterns
    stopped covering a reworded/new diagnostic.

Usage:
    python3 tools/triage_panic_errors.py
    python3 tools/triage_panic_errors.py --out /tmp/triage.json   # safe dev run
    python3 tools/triage_panic_errors.py --panic-root-regex 'overflow'
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter

# Reuse step-0's enclosing-fn helpers rather than reimplementing brace counting.
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _TOOLS_DIR)
import reannotate_flux as RF  # noqa: E402

ROOT = os.path.dirname(_TOOLS_DIR)  # repo root (parent of tools/)

# --------------------------------------------------------------------------- #
# Panic-root filter
# --------------------------------------------------------------------------- #
# A "panic-root" error is one that looks like the source of a runtime panic, as
# opposed to a precondition error pushed up from a callee. `assert_obligation`
# errors are at *our* asserts -- which by construction guard a real `bl panic`
# -- so they are panic-root regardless of message. `other_obligation` errors are
# admitted by message pattern, with `precondition` future-proofed out (Flux does
# not currently emit that text in this tree, but the exclusion keeps the bucket
# honest if it starts to).
PANIC_ROOT_PATTERNS = [
    r"assertion might fail",
    r"refinement type error",
    r"may panic",
    r"use of (?:associated )?function",
]
EXCLUDE_PATTERNS = [
    r"precondition",
    # A struct refinement-invariant obligation checked at a fold point. This is a
    # pure verification obligation -- it does NOT codegen a `bl panic` -- so it is
    # not panic-root (same rationale as `precondition`).
    r"type invariant may not hold",
]
# Flux *tooling failures*: the analysis did not produce a verification obligation
# at all. "parameter inference error at function call" means Flux could not infer a
# call's refinement arguments (a limitation, needs manual annotation); "internal
# flux error: ..." is a Flux crash. Neither codegens a `bl panic`, so neither is
# panic-root -- but they are categorically different from a deliberately-excluded
# precondition diagnostic, so they get their own bucket instead of `excluded`.
TOOLING_ERROR_PATTERNS = [
    r"parameter inference error",
    r"internal flux error",
]


def _norm(path: str) -> str:
    """Repo-relative, no leading ./ -- mirrors RF.norm_file but tolerant of None."""
    return RF.norm_file(path) if path else path


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1] if path else path


# --------------------------------------------------------------------------- #
# Source-derived indexes
# --------------------------------------------------------------------------- #


def _read_lines(rel_path: str, cache: dict[str, list[str] | None]) -> list[str] | None:
    if rel_path in cache:
        return cache[rel_path]
    full = os.path.join(ROOT, rel_path)
    try:
        with open(full, encoding="utf-8") as fh:
            cache[rel_path] = fh.read().split("\n")
    except OSError:
        cache[rel_path] = None
    return cache[rel_path]


def build_fn_level_ranges(report: dict, cache: dict) -> list[tuple[str, int, int, str]]:
    """For every FLUX-TODO-FN-LEVEL marker referenced by a fn_level obligation,
    compute its function body's 1-based inclusive line range. Returns a list of
    (norm_file, start, end, func). Dedupes by (norm_file, marker_line)."""
    ranges: list[tuple[str, int, int, str]] = []
    seen: set[tuple[str, int]] = set()
    for ob in report.get("obligations", []):
        if ob.get("kind") != "fn_level":
            continue
        for mk in ob.get("covers", {}).get("markers", []):
            f = _norm(mk.get("file", ""))
            ln = mk.get("line")
            if not f or ln is None or (f, ln) in seen:
                continue
            seen.add((f, ln))
            lines = _read_lines(f, cache)
            if lines is None:
                continue
            fn_name, decl = RF.enclosing_fn_below(lines, ln - 1)  # marker_line is 1-based
            if decl is None:
                continue
            start, end = RF.fn_body_range(lines, decl)
            ranges.append((f, start, end, fn_name or ob["obligation"].get("func", "")))
    return ranges


def build_precise_index(report: dict) -> dict[tuple[str, int], dict]:
    """(basename, assert_line) -> precise obligation, for the bucket-1 addr join.
    Matches the basename + line tagging that check_invariant2.py used to label
    `assert_obligation` errors in the first place."""
    idx: dict[tuple[str, int], dict] = {}
    for ob in report.get("obligations", []):
        if ob.get("kind") != "precise":
            continue
        f = ob["obligation"].get("file")
        ln = ob["obligation"].get("assert_line")
        if f and ln is not None:
            idx[(_basename(f), ln)] = ob
    return idx


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


def _compile(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def classify(report: dict, survey_addrs: set[str], panic_root: list[re.Pattern],
             exclude: list[re.Pattern], tooling: list[re.Pattern], cache: dict) -> list[dict]:
    fn_ranges = build_fn_level_ranges(report, cache)
    precise_idx = build_precise_index(report)

    def filter_status(e: dict) -> str:
        """One of: 'assert' (panic-root by construction), 'panic_root' (message
        matched a panic-root pattern), 'excluded' (message matched an exclude
        pattern -- deliberately dropped), 'tooling_error' (a Flux inference/internal
        failure that produced no obligation), or 'unclassified' (matched NONE).
        'unclassified' is the loud case: an E0999 we don't recognize means a
        Flux diagnostic was reworded (or a new error kind appeared) and the
        brittle message patterns silently stopped covering it."""
        if e.get("category") == "assert_obligation":
            return "assert"
        msg = e.get("message", "")
        if any(p.search(msg) for p in tooling):
            return "tooling_error"
        if any(p.search(msg) for p in exclude):
            return "excluded"
        if any(p.search(msg) for p in panic_root):
            return "panic_root"
        return "unclassified"

    def precise_addrs(e: dict) -> list[str]:
        """All addrs of precise obligations this error joins to (basename + line +/-1)."""
        base = _basename(_norm(e.get("file", "")))
        ln = e.get("line")
        addrs: list[str] = []
        for (f, aln), ob in precise_idx.items():
            if f == base and ln is not None and abs(ln - aln) <= 1:
                addrs.extend(ob.get("covers", {}).get("addrs", []))
        return addrs

    def enclosing_fn_level(e: dict) -> str | None:
        f = _norm(e.get("file", ""))
        ln = e.get("line")
        if ln is None:
            return None
        for (rf, start, end, func) in fn_ranges:
            if rf == f and start <= ln <= end:
                return func
        return None

    out: list[dict] = []
    for e in report.get("flux_errors", []):
        rec = {
            "crate": e.get("crate"),
            "code": e.get("code"),
            "file": e.get("file"),
            "line": e.get("line"),
            "col": e.get("col"),
            "message": e.get("message"),
            "category": e.get("category"),
        }
        status = filter_status(e)
        if status == "unclassified":
            rec["bucket"] = "unclassified"
            rec["reason"] = ("E0999 matched NEITHER a panic-root nor an exclude pattern -- "
                             "the message patterns no longer cover this diagnostic (reworded "
                             "Flux output or a new error kind); triage cannot bucket it")
        elif status == "tooling_error":
            rec["bucket"] = "tooling_error"
            rec["reason"] = ("a Flux tooling failure (parameter inference error / internal "
                             "flux error) -- the analysis produced no verification obligation, "
                             "so it is not panic-root and does not codegen a bl panic")
        elif status == "excluded":
            rec["bucket"] = "excluded"
            rec["reason"] = "matched an exclude pattern (precondition / non-panic diagnostic)"
        elif e.get("category") == "assert_obligation":
            addrs = precise_addrs(e)
            rec["bucket"] = "panicking"
            rec["addrs"] = addrs
            missing = [a for a in addrs if a.lower() not in survey_addrs]
            if not addrs:
                rec["reason"] = ("at a precise-marker assert, but could not re-join to a "
                                 "precise obligation (basename/line drift) -- treated as panicking")
            elif missing:
                rec["reason"] = (f"at a precise-marker assert; addr(s) {addrs} but "
                                 f"{missing} are absent from the survey (stale?)")
            else:
                rec["reason"] = (f"at a precise-marker assert; addr(s) {addrs} resolve to a "
                                 f"bl panic in the survey")
        else:
            func = enclosing_fn_level(e)
            if func is not None:
                rec["bucket"] = "lookup_failed"
                rec["func"] = func
                rec["reason"] = (f"inside fn-level-marked function `{func}` -- addr2line could "
                                 f"not pin the panic to a precise line")
            elif re.search(r"refinement type error", e.get("message", "") or "", re.IGNORECASE):
                rec["bucket"] = "precondition_failed"
                rec["reason"] = ("a Flux refinement-type error (a precondition that did not "
                                 "hold) with no precise-marker assert and not inside a "
                                 "fn-level-marked function -- not itself a codegened bl panic, "
                                 "but typically a precondition pushed up from / downstream of a "
                                 "real obligation elsewhere, so NOT deprioritizable on its own")
            else:
                rec["bucket"] = "non_panicking"
                rec["reason"] = ("a panic-shaped assertion (out-of-bounds / divide-by-zero / "
                                 "may-panic) with no precise-marker assert and not inside a "
                                 "fn-level-marked function -- no codegened bl panic "
                                 "(optimizer/build proved or dropped it); deprioritizable")
        out.append(rec)
    return out


# --------------------------------------------------------------------------- #
# Conservation gate
# --------------------------------------------------------------------------- #

_BUCKETS = ("panicking", "lookup_failed", "non_panicking", "precondition_failed",
            "excluded", "tooling_error", "unclassified")


def mark_panicking_dedup(records: list[dict]) -> int:
    """Fold `panicking` records that guard an IDENTICAL set of codegened `bl panic`
    addrs into one obligation.

    A `flux_support::assert(X)` and the implicit operation it makes explicit (the
    `.unwrap()` / index it guards) sit on adjacent lines, so the +/-1 line window in
    `precise_addrs()` joins BOTH to the same precise obligation -> both carry the
    same addr(s). They are one panic, not two. Likewise N monomorphizations of one
    generic emit N records sharing the same addr-set. Counting raw records therefore
    over-counts; the distinct quantity is the number of distinct addr-sets.

    We do NOT drop anything -- every record stays in `errors[]` so the conservation
    gate's bijection over flux_errors[] still holds, and the explicit asserts remain
    visible/greppable. Instead each panicking record is tagged with:
      - addr_group: a stable comma-joined key of its sorted addr-set (None if it
                    carries no addrs -- those are never folded, each its own group).
      - dedup_role: "primary" for the first record of a group (the one counted by
                    panicking_distinct), "folded" for the rest (duplicates).
    Records are ordered (file, line, col) so "primary" is deterministic. Returns the
    distinct-obligation count = number of primaries.

    NB: folding by addr-SET (not individual addr) is deliberate -- a single source
    obligation that guards many monomorphized panics (e.g. one generic -> 50 addrs)
    is ONE obligation and stays one. To instead count distinct codegened bl-panic
    instructions, count distinct addrs across the addr_group keys."""
    groups: dict[tuple, list[dict]] = {}
    singletons = 0
    for r in records:
        if r.get("bucket") != "panicking":
            continue
        addrs = r.get("addrs") or []
        if not addrs:
            r["addr_group"] = None
            r["dedup_role"] = "primary"
            singletons += 1
            continue
        groups.setdefault(tuple(sorted(set(addrs))), []).append(r)
    for key, members in groups.items():
        gid = ",".join(key)
        members.sort(key=lambda r: (r.get("file") or "", r.get("line") or 0,
                                    r.get("col") or 0))
        for i, r in enumerate(members):
            r["addr_group"] = gid
            r["dedup_role"] = "primary" if i == 0 else "folded"
    return len(groups) + singletons


def verify_partition(flux_errors: list[dict], records: list[dict],
                     counts: dict[str, int]) -> list[str]:
    """The triage must be a complete, bijective partition of the input: every
    E0999 in flux_errors[] is represented exactly once in records[], landing in
    exactly one bucket -- nothing dropped, nothing double-counted. This is a
    tool-integrity invariant (the classify() loop should guarantee it), so a
    violation means a real bug, not a frontier finding. Returns a list of
    human-readable violations (empty == clean)."""
    def key(x: dict):
        return (x.get("file"), x.get("line"), x.get("col"), x.get("message"))

    v: list[str] = []
    if len(records) != len(flux_errors):
        v.append(f"record count {len(records)} != input flux_errors {len(flux_errors)} "
                 "(an obligation was dropped or duplicated)")
    bsum = sum(counts.get(b, 0) for b in _BUCKETS)
    if bsum != len(records):
        v.append(f"bucket-count sum {bsum} != record count {len(records)} "
                 "(a record is unbucketed or counted twice)")
    no_bucket = [r for r in records if r.get("bucket") not in _BUCKETS]
    if no_bucket:
        v.append(f"{len(no_bucket)} record(s) carry no known bucket")

    ki, ko = Counter(key(e) for e in flux_errors), Counter(key(r) for r in records)
    if ki != ko:
        missing = sorted(str(k) for k in ki if ki[k] != ko.get(k, 0))[:5]
        extra = sorted(str(k) for k in ko if ko[k] != ki.get(k, 0))[:5]
        v.append("input/output error sets differ (not a bijection); "
                 f"e.g. unmatched-in-input={missing} unmatched-in-output={extra}")
    return v


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", default=os.path.join(_TOOLS_DIR, "invariant2_report.json"),
                    help="invariant2_report.json to read and augment")
    ap.add_argument("--survey", default=os.path.join(_TOOLS_DIR, "panic_survey.json"),
                    help="panic_survey.json (ground truth for codegened panics)")
    ap.add_argument("--out", default=None,
                    help="where to write the augmented report (default: in place over --report)")
    ap.add_argument("--panic-root-regex", action="append", default=[],
                    help="extra panic-root message pattern (repeatable)")
    ap.add_argument("--exclude-regex", action="append", default=[],
                    help="extra exclude (non-panic-root) message pattern (repeatable)")
    ap.add_argument("--tooling-error-regex", action="append", default=[],
                    help="extra tooling-error message pattern -- a Flux inference/internal "
                         "failure that produced no obligation (repeatable)")
    ap.add_argument("--panic-root-only", action="store_true",
                    help="use only --panic-root-regex patterns, dropping the built-in defaults")
    ap.add_argument("--allow-unclassified", action="store_true",
                    help="do not fail when an E0999 matches neither panic-root nor exclude "
                         "patterns (default: scream and exit 1 so reworded Flux diagnostics "
                         "cannot silently slip through)")
    args = ap.parse_args()

    out_path = args.out or args.report

    try:
        with open(args.report, encoding="utf-8") as fh:
            report = json.load(fh)
    except OSError as ex:
        print(f"error: cannot read --report {args.report}: {ex}", file=sys.stderr)
        return 2
    try:
        with open(args.survey, encoding="utf-8") as fh:
            survey = json.load(fh)
    except OSError as ex:
        print(f"error: cannot read --survey {args.survey}: {ex}", file=sys.stderr)
        return 2

    survey_addrs = {str(s["address"]).lower() for s in survey.get("sites", [])}

    base_root = [] if args.panic_root_only else list(PANIC_ROOT_PATTERNS)
    panic_root = _compile(base_root + args.panic_root_regex)
    exclude = _compile(list(EXCLUDE_PATTERNS) + args.exclude_regex)
    tooling = _compile(list(TOOLING_ERROR_PATTERNS) + args.tooling_error_regex)

    cache: dict[str, list[str] | None] = {}
    errors = classify(report, survey_addrs, panic_root, exclude, tooling, cache)

    counts = {"panicking": 0, "lookup_failed": 0, "non_panicking": 0,
              "precondition_failed": 0, "excluded": 0, "tooling_error": 0,
              "unclassified": 0}
    for r in errors:
        counts[r["bucket"]] += 1
    total_panic_root = (counts["panicking"] + counts["lookup_failed"]
                        + counts["non_panicking"] + counts["precondition_failed"])

    # Fold the explicit-assert / monomorph duplicates within `panicking`: distinct
    # obligations, not raw error records. Does not touch `counts` (the conservation
    # gate partitions raw flux_errors[]); reported as an extra summary field.
    panicking_distinct = mark_panicking_dedup(errors)

    partition_violations = verify_partition(report.get("flux_errors", []), errors, counts)

    report["triage"] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inputs": {
            "invariant2_report": os.path.relpath(args.report, ROOT),
            "panic_survey_json": os.path.relpath(args.survey, ROOT),
        },
        "filter": {
            "panic_root_patterns": base_root + args.panic_root_regex,
            "exclude_patterns": list(EXCLUDE_PATTERNS) + args.exclude_regex,
            "tooling_error_patterns": list(TOOLING_ERROR_PATTERNS) + args.tooling_error_regex,
            "note": ("assert_obligation errors are panic-root by construction; "
                     "other_obligation errors are admitted by message pattern"),
        },
        "summary": {
            "total_errors": len(errors),
            "panicking": counts["panicking"],
            "panicking_distinct": panicking_distinct,
            "precondition_failed": counts["precondition_failed"],
            "non_panicking": counts["non_panicking"],
            "lookup_failed": counts["lookup_failed"],
            "excluded": counts["excluded"],
            "tooling_error": counts["tooling_error"],
            "unclassified": counts["unclassified"],
            "total_panic_root": total_panic_root,
            "conservation_ok": not partition_violations,
        },
        "errors": errors,
    }

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
        fh.write("\n")

    print(f"triage: panicking={counts['panicking']} "
          f"(distinct={panicking_distinct}) "
          f"precondition_failed={counts['precondition_failed']} "
          f"non_panicking={counts['non_panicking']} "
          f"lookup_failed={counts['lookup_failed']} "
          f"excluded={counts['excluded']} "
          f"tooling_error={counts['tooling_error']} "
          f"unclassified={counts['unclassified']} "
          f"(total panic-root={total_panic_root})")
    print(f"wrote {out_path}")

    if partition_violations:
        print("\n" + "!" * 72, file=sys.stderr)
        print("FATAL: triage is not a complete partition of invariant2_report's "
              "flux_errors[] -- an obligation was dropped, duplicated, or unbucketed. "
              "This is a tool bug, not a frontier finding.", file=sys.stderr)
        for msg in partition_violations:
            print(f"  - {msg}", file=sys.stderr)
        print("!" * 72, file=sys.stderr)
        return 3  # non-overridable: conservation must always hold

    if counts["unclassified"]:
        print("\n" + "!" * 72, file=sys.stderr)
        print(f"FATAL: {counts['unclassified']} E0999 error(s) matched NONE of the panic-root, "
              "exclude, or tooling-error patterns.", file=sys.stderr)
        print("The triage's message patterns no longer cover every Flux error -- a reworded "
              "diagnostic or new error kind is slipping through uncounted.", file=sys.stderr)
        print("Update PANIC_ROOT_PATTERNS / EXCLUDE_PATTERNS / TOOLING_ERROR_PATTERNS (or pass "
              "--panic-root-regex / --exclude-regex / --tooling-error-regex), or re-run with "
              "--allow-unclassified to override.", file=sys.stderr)
        for r in errors:
            if r["bucket"] == "unclassified":
                print(f"  - {r.get('file')}:{r.get('line')} [{r.get('category')}] "
                      f"{r.get('message')}", file=sys.stderr)
        print("!" * 72, file=sys.stderr)
        if not args.allow_unclassified:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
