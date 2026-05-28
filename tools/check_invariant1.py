#!/usr/bin/env python3
"""check_invariant1.py -- Step 1 of the no-panic verification pipeline.

Invariant 1: every Tock-local panic instruction in the release binary is covered
by a ``// FLUX-`` annotation in source. This is the inverse of step 0
(``reannotate_flux.py``), which re-anchors the annotations against the binary; step
1 asks, for each panic instruction in ``panic_survey.json``, whether *some*
annotation claims its address.

This is a faithful implementation of ``tools/invariant_one.md``; read that first.
The short version:

  * One entry per panic instruction (per binary ``bl``), never collapsed per source
    site. 50 monomorphized ``bl``s at one line are 50 obligations sharing one
    annotation block -- so "49 of 50 covered" surfaces as exactly that.
  * Each instruction gets one of four statuses: ``stdlib`` (not Tock-local, no
    annotation expected), ``comment_precise`` (a FLUX-TODO/FLUX-OPT marker references
    its addr), ``comment_fn_level`` (a FLUX-TODO-FN-LEVEL marker references its addr),
    or ``missing_comment`` (Tock-local, unreferenced -- the fail-fast condition).
  * Precise and FN-LEVEL markers must own disjoint address sets. An addr claimed by
    both a precise marker AND a FN-LEVEL marker is a *precise/FN-LEVEL overlap* --
    reported and fatal (the methodology: precise markers own line-survived
    addresses, FN-LEVEL owns line-lost addresses; the two sets are disjoint).
  * The marker grammar and the Tock-local filter are imported from step 0 so the two
    steps can never drift apart.

This script assumes step 0 has run successfully. It does NOT re-validate annotation
well-formedness, double-markers, orphans, or ambiguity -- step 0 guarantees these.
It checks comment *existence* only; whether a ``flux_support::assert`` follows and
whether Flux discharges it are invariant 2's concerns.

``invariant1_report.json`` is written on every run -- the exit code is the gate, the
JSON is the diagnostic.

Exit codes:
  0  every Tock-local panic instruction is covered; no overlaps
  1  one or more missing_comment instructions and/or precise/FN-LEVEL overlaps
  2  usage / I/O / survey error

See the "Usage" section of the spec for operational guidance.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

# Single-source the marker grammar and the Tock-local path convention from step 0,
# so step 1 parses `// FLUX-` comments exactly as step 0 does and can never drift.
from reannotate_flux import (  # noqa: E402
    KIND_FN_LEVEL,
    KIND_OPT,
    KIND_TODO,
    discover_scope,
    norm_file,
    parse_markers,
)

# Map step 0's terse kind constants to the spec's verbatim annotation keywords.
_KIND_LABEL = {
    KIND_TODO: "FLUX-TODO",
    KIND_OPT: "FLUX-OPT",
    KIND_FN_LEVEL: "FLUX-TODO-FN-LEVEL",
}


# --------------------------------------------------------------------------- #
# Survey loading -- keep ALL records (step 1 emits stdlib entries too)
# --------------------------------------------------------------------------- #


@dataclass
class SurveyRecord:
    addr: str             # e.g. "0xf978" (normalised lowercase)
    panic_fn: str         # survey `sink`, the panic sink symbol
    sink_flavor: str
    file: str             # effective_frame.file verbatim (may carry leading ./)
    line: int | None      # effective_frame.line; None == LTO line loss
    func: str             # effective_frame.func (post-LTO asm owner)
    tock_local: bool      # the step-0 filter: effective_frame.file starts with "./"


def load_survey(path: str) -> list[SurveyRecord]:
    """Load every record in ``panic_survey.json`` (no filtering -- step 1 needs the
    stdlib records too, to emit their ``stdlib``-status entries).

    Tock-local is the step-0 filter verbatim: ``effective_frame.file`` starts with
    ``./`` (keyed on the path so it is robust to bucket-taxonomy drift; equivalent to
    ``origin_bucket != 'stdlib'``). Step 0's ``load_survey`` *drops* the stdlib
    records; step 1 keeps and tags them.
    """
    with open(path) as fh:
        data = json.load(fh)
    sites = data["sites"] if isinstance(data, dict) else data
    records: list[SurveyRecord] = []
    for s in sites:
        ef = s["effective_frame"]
        f = ef.get("file", "")
        records.append(
            SurveyRecord(
                addr=str(s["address"]).lower(),
                panic_fn=s.get("sink", "") or "",
                sink_flavor=s["sink_flavor"],
                file=f,
                line=ef.get("line"),
                func=ef.get("func", "") or "",
                tock_local=f.startswith("./"),  # stdlib / external -> out of scope
            )
        )
    return records


# --------------------------------------------------------------------------- #
# Marker collection and the address -> marker indices
# --------------------------------------------------------------------------- #


def collect_markers(repo_root: str) -> list:
    """Parse every ``// FLUX-`` marker in the tree, using step 0's discovery and
    parser verbatim. ``discover_scope`` (called with no records) returns the set of
    files that contain a ``// FLUX-`` comment -- exactly the universe of annotations.
    """
    _scope, annotated = discover_scope(repo_root, [])
    markers: list = []
    for path in sorted(annotated):
        try:
            with open(os.path.join(repo_root, path), encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        markers.extend(parse_markers(path, text))
    return markers


def _precise_addrs(m) -> list[str]:
    """Addresses a precise (TODO/OPT) marker claims. Step 0 forbids carrying both
    forms, so a clean tree gives exactly one of addr_singular / list_addrs."""
    if m.addr_singular:
        return [m.addr_singular]
    return list(m.list_addrs)


def _precise_form(m) -> str:
    return "single" if m.addr_singular else "list"


def build_indices(markers: list) -> tuple[dict[str, list], dict[str, list]]:
    """Return (precise_by_addr, fnlevel_by_addr). BLOCKED/UNKNOWN markers contribute
    nothing -- step 0 guarantees a clean tree has none, and they are not step 1's
    concern."""
    precise_by_addr: dict[str, list] = defaultdict(list)
    fnlevel_by_addr: dict[str, list] = defaultdict(list)
    for m in markers:
        if m.kind in (KIND_TODO, KIND_OPT):
            for a in _precise_addrs(m):
                precise_by_addr[a].append(m)
        elif m.kind == KIND_FN_LEVEL:
            for a in m.list_addrs:
                fnlevel_by_addr[a].append(m)
    return precise_by_addr, fnlevel_by_addr


def _pick(markers: list):
    """Deterministic choice when (against step-0 guarantees) several markers claim
    one addr: lowest (path, anchor). Step 0's double-marker check owns the conflict;
    step 1 just needs a stable annotation to report."""
    return sorted(markers, key=lambda m: (m.path, m.anchor))[0]


def annotation_block(m, addr_form: str) -> dict:
    return {
        "kind": _KIND_LABEL[m.kind],
        "addr_form": addr_form,
        "file": norm_file(m.path),
        "line": m.anchor,
        "raw": m.raw,
    }


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


def classify(records: list[SurveyRecord],
             precise_by_addr: dict[str, list],
             fnlevel_by_addr: dict[str, list]) -> tuple[list[dict], dict[str, int]]:
    """One obligation entry per instruction, in survey order. Status precedence is
    stdlib (by file) -> comment_precise -> comment_fn_level -> missing_comment. The
    precise-before-fn-level order is lookup order, not a conflict-resolution ruling:
    an addr in both indices is reported as an overlap (see find_overlaps) and is
    fatal regardless of which status its obligation carries here."""
    counts = {"stdlib": 0, "comment_precise": 0,
              "comment_fn_level": 0, "missing_comment": 0}
    obligations: list[dict] = []
    for r in records:
        if not r.tock_local:
            status, annotation = "stdlib", None
        elif r.addr in precise_by_addr:
            m = _pick(precise_by_addr[r.addr])
            status, annotation = "comment_precise", annotation_block(m, _precise_form(m))
        elif r.addr in fnlevel_by_addr:
            m = _pick(fnlevel_by_addr[r.addr])
            status, annotation = "comment_fn_level", annotation_block(m, "list")
        else:
            status, annotation = "missing_comment", None
        counts[status] += 1
        obligations.append({
            "addr": r.addr,
            "panic_fn": r.panic_fn,
            "sink_flavor": r.sink_flavor,
            "status": status,
            "source": {"file": norm_file(r.file), "line": r.line, "func": r.func},
            "annotation": annotation,
        })
    return obligations, counts


def find_overlaps(records: list[SurveyRecord],
                  precise_by_addr: dict[str, list],
                  fnlevel_by_addr: dict[str, list]) -> list[dict]:
    """Panic-instruction addresses (keyed on the survey, per the spec) claimed by
    both a precise marker AND a FN-LEVEL marker. Sorted by numeric address."""
    overlaps: list[dict] = []
    seen: set[str] = set()
    for r in records:
        a = r.addr
        if a in seen or a not in precise_by_addr or a not in fnlevel_by_addr:
            continue
        seen.add(a)
        pm = _pick(precise_by_addr[a])
        fm = _pick(fnlevel_by_addr[a])
        overlaps.append({
            "addr": a,
            "precise": annotation_block(pm, _precise_form(pm)),
            "fn_level": annotation_block(fm, "list"),
        })
    overlaps.sort(key=lambda o: int(o["addr"], 16))
    return overlaps


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def report_violations(obligations: list[dict], overlaps: list[dict]) -> None:
    """Step-0-style one-pass violation block: every missing comment and every
    overlap, not just the first."""
    missing = [o for o in obligations if o["status"] == "missing_comment"]
    print("\n=== violations ===")
    print(f"  MISSING-COMMENT: {len(missing)}")
    print(f"  PRECISE/FN-LEVEL OVERLAP: {len(overlaps)}")
    print(f"  TOTAL: {len(missing) + len(overlaps)}")

    if missing:
        print(f"\n--- MISSING-COMMENT ({len(missing)}) ---")
        for o in sorted(missing, key=lambda o: (o["source"]["file"],
                                                o["source"]["line"] or 0,
                                                int(o["addr"], 16))):
            src = o["source"]
            print(f"  {src['file']}:{src['line']}  addr={o['addr']} "
                  f"flavor={o['sink_flavor']}")
            print(f"      func={src['func']}")

    if overlaps:
        print(f"\n--- PRECISE/FN-LEVEL OVERLAP ({len(overlaps)}) ---")
        for o in overlaps:
            p, f = o["precise"], o["fn_level"]
            print(f"  addr={o['addr']} claimed by both:")
            print(f"      precise  {p['file']}:{p['line']}  {p['raw'].strip()}")
            print(f"      fn-level {f['file']}:{f['line']}  {f['raw'].strip()}")


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


def main(argv=None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--survey", default=os.path.join(here, "panic_survey.json"),
                    help="panic_survey.json to read (default: tools/panic_survey.json)")
    ap.add_argument("--repo-root", default=None,
                    help="repo root (default: parent of tools/)")
    ap.add_argument("--out", default=os.path.join(here, "invariant1_report.json"),
                    help="report path (default: tools/invariant1_report.json)")
    args = ap.parse_args(argv)

    repo_root = os.path.abspath(
        args.repo_root or os.path.dirname(here))

    if not os.path.exists(args.survey):
        sys.exit(f"error: --survey {args.survey} does not exist")
    try:
        records = load_survey(args.survey)
    except (OSError, ValueError, KeyError) as e:
        sys.exit(f"error: could not read survey {args.survey}: {e}")

    # In-scope summary FIRST (audit trail, visible even on a non-zero exit).
    n = len(records)
    n_local = sum(1 for r in records if r.tock_local)
    n_stdlib = n - n_local
    print(f"checking {n} panic instructions: {n_stdlib} stdlib, {n_local} Tock-local")

    markers = collect_markers(repo_root)
    precise_by_addr, fnlevel_by_addr = build_indices(markers)

    obligations, counts = classify(records, precise_by_addr, fnlevel_by_addr)
    overlaps = find_overlaps(records, precise_by_addr, fnlevel_by_addr)

    try:
        rel_survey = os.path.relpath(args.survey, repo_root)
    except ValueError:
        rel_survey = args.survey

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inputs": {"panic_survey_json": rel_survey},
        "summary": {
            "total": n,
            "by_status": counts,
            "overlaps": len(overlaps),
        },
        "obligations": obligations,
        "overlaps": overlaps,
    }

    # The report is always written -- the exit code is the gate, the JSON the
    # diagnostic. Write via a temp + replace so a reader never sees a half file.
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, args.out)

    missing = counts["missing_comment"]
    if missing or overlaps:
        report_violations(obligations, overlaps)
        print(f"\n{missing + len(overlaps)} violation(s); report written to {args.out}.")
        return 1

    print(f"\nclean: all {n_local} Tock-local panic instructions covered "
          f"({counts['comment_precise']} precise, {counts['comment_fn_level']} "
          f"fn-level); report written to {args.out}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
