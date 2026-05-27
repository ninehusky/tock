#!/usr/bin/env python3
"""Delete every `// FLUX-` annotation that step 0 (reannotate_flux.py) flags as a
violation, so the pipeline runs clean, and log each deletion richly enough to
reintroduce the site later (when invariant 1 flags its panic as unannotated).

Deletes the comment block only -- the `// FLUX-` kind line, any multi-line
`addrs=[...]` continuation, and the contiguous `//` note lines immediately below
it. Code and live `flux_support::assert(...)` calls are NOT touched.

Default is dry-run (prints what it would delete + the log). Pass --apply to write.

Log: step0-deletions-log.json (list of records; see --help output / README header).
"""
import argparse
import json
import os
import sys

import reannotate_flux as rf


def contiguous_comment_block(lines, start, end):
    """Deletion range [start, last] (0-based, inclusive): the marker's own lines
    (start..end, which already covers a multi-line addrs list) plus contiguous
    `//` comment lines immediately below. Stops at the first blank or code line,
    so it never eats unrelated comments or code."""
    last = end
    j = end + 1
    while j < len(lines) and lines[j].lstrip().startswith("//"):
        last = j
        j += 1
    return start, last


def find_assert(lines, block_last):
    """Return (assert_text, disposition). Prefer a live `flux_support::assert(...)`
    on the first non-blank code line after the block (retained). Otherwise, if the
    block carried a commented-out `// flux_support::assert(...)`, report that
    (removed with the block)."""
    j = block_last + 1
    while j < len(lines) and lines[j].strip() == "":
        j += 1
    if j < len(lines) and "flux_support::assert" in lines[j] and not lines[j].lstrip().startswith("//"):
        return lines[j].strip(), "retained (live code below block)"
    return None, None


def commented_assert_in_block(block_lines):
    for ln in block_lines:
        s = ln.strip()
        if s.startswith("//") and "flux_support::assert" in s:
            return s
    return None


def first_code_after(lines, block_last):
    j = block_last + 1
    while j < len(lines) and (lines[j].strip() == "" or lines[j].lstrip().startswith("//")):
        j += 1
    return lines[j].strip() if j < len(lines) else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--survey", default=None,
                    help="panic_survey.json (default: tools/panic_survey.json)")
    ap.add_argument("--repo-root", default=None)
    ap.add_argument("--log", default=None, help="output JSON log path")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete (default: dry-run, no writes)")
    args = ap.parse_args(argv)

    tools_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(args.repo_root or os.path.dirname(tools_dir))
    survey = args.survey or os.path.join(tools_dir, "panic_survey.json")
    log_path = args.log or os.path.join(repo_root, "step0-deletions-log.json")

    records, present_flavors = rf.load_survey(survey)
    valid_flavors = rf.tool_flavor_vocabulary(tools_dir) | present_flavors
    auditor = rf.Auditor(repo_root, records, valid_flavors)
    scope, _ = rf.discover_scope(repo_root, records)
    for path in sorted(scope):
        auditor.audit_file(path)
    auditor.check_double_markers()

    # markers to delete, deduped by identity; remember category per marker
    to_delete = {}   # id -> (marker, category, detail, hint)
    for v in auditor.violations:
        for mk in (v.marker, v.extra):
            if mk is None:
                continue
            to_delete.setdefault(id(mk), (mk, v.kind, v.detail, v.hint))

    # group by file
    by_file = {}
    for mk, kind, detail, hint in to_delete.values():
        by_file.setdefault(mk.path, []).append((mk, kind, detail, hint))

    log = []
    files_touched = 0
    for path in sorted(by_file):
        full = os.path.join(repo_root, path)
        with open(full, encoding="utf-8") as fh:
            lines = fh.read().split("\n")

        ranges = []  # (start, last, record)
        for mk, kind, detail, hint in by_file[path]:
            start, last = contiguous_comment_block(lines, mk.start, mk.end)
            block = lines[start:last + 1]
            assert_text, disposition = find_assert(lines, last)
            commented = commented_assert_in_block(block)
            rec = {
                "file": path,
                "line": mk.anchor,
                "category": kind,
                "category_detail": detail,
                "hint": hint,
                "comment": block,
                "assert": assert_text or commented,
                "assert_disposition": disposition or (
                    "removed (commented-out, part of block)" if commented else "none"),
                "next_code": first_code_after(lines, last),
            }
            log.append(rec)
            ranges.append((start, last))

        # delete descending so earlier indices stay valid
        for start, last in sorted(ranges, reverse=True):
            del lines[start:last + 1]

        files_touched += 1
        if args.apply:
            with open(full, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))

    log.sort(key=lambda r: (r["file"], r["line"]))
    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"[{mode}] {len(log)} annotations across {files_touched} files")
    by_cat = {}
    for r in log:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    for c, n in sorted(by_cat.items()):
        print(f"   {c}: {n}")

    if args.apply:
        with open(log_path, "w", encoding="utf-8") as fh:
            json.dump(log, fh, indent=2)
        print(f"   log written: {log_path}")
    else:
        print(f"   (dry-run) log preview -> {log_path}.preview")
        with open(log_path + ".preview", "w", encoding="utf-8") as fh:
            json.dump(log, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
