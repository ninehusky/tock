# Step 1: Invariant 1 — every panic instruction is annotated in source

This is the first of two invariants the pipeline checks. Step 0 has already audited and
re-anchored every `// FLUX-` annotation against the current binary; step 1 (this script,
`check_invariant1.py` or similar) now asks the inverse question: for each panic
instruction in the binary, is there an annotation in source that covers its address?

The script consumes `panic_survey.json` (produced upstream by `panic_survey.py`) and the
current Tock source tree, and emits a status report — one entry per panic instruction —
plus a non-zero exit if any Tock-local panic lacks a covering annotation.

This script assumes step 0 has run successfully. It does not re-validate annotation
well-formedness, double-markers, orphans, or ambiguity — step 0 guarantees these.

## In-scope panic instructions

The set of panic instructions is every record in `panic_survey.json`. Each is classified
into one of four statuses (see below). The "Tock-local" filter (whatever step 0 settled
on — likely `effective_frame.file.startswith('./')`) determines which records can have a
covering annotation versus which are intrinsically stdlib.

The script prints a one-line summary at the start of every run:

```
checking N panic instructions: K stdlib, M Tock-local
```

## Status taxonomy

Each panic instruction gets exactly one status:

1. **`stdlib`** — `effective_frame.file` is in `/rustc/.../library/...` (or whatever the
   step-0 filter identifies as non-Tock). No comment expected, no error.

2. **`comment_precise`** — a `FLUX-TODO` or `FLUX-OPT` comment in source references this
   instruction's `addr`, either via `addr=0x...` (single form) or as a member of
   `addrs=[0x..., ...]` (list form). The source line for this panic survived LTO.

3. **`comment_fn_level`** — a `FLUX-TODO-FN-LEVEL` annotation references this `addr` in
   its `addrs=[...]` list. The source line for this panic was lost to LTO; the annotation
   sits on the enclosing function.

4. **`missing_comment`** — a Tock-local panic instruction with no `FLUX-` comment in
   source referencing its `addr`. **This is the fail-fast condition.** If any
   `missing_comment` entries exist, the script exits non-zero after writing the report.

Note: this script checks comment *existence* only. Whether a `flux_support::assert(...)`
follows the comment, and whether Flux discharges it, are invariant 2's concerns.

**Authoring note: a precise `addrs=[...]` list stays on one physical line.** A precise
marker is anchored by its own line, and its obligation must resolve within step 0's
`[P, P+5]` window. A multi-line `addrs=[...]` block taller than that window pushes the
panic past `P + 5`, so the marker orphans itself on step 0's next run — therefore the
list, however long (e.g. the 50-monomorph `grant.rs` site), must stay on a single line.
FN-LEVEL markers are anchored by function name rather than the line window, so they may
wrap. See `reannotate_flux_spec.md`'s "Source convention".

## Output schema

The script emits `invariant1_report.json`:

```json
{
  "generated_at": "2026-05-26T18:00:00Z",
  "inputs": {
    "panic_survey_json": "tools/panic_survey.json"
  },
  "summary": {
    "total": 350,
    "by_status": {
      "stdlib": 22,
      "comment_precise": 280,
      "comment_fn_level": 47,
      "missing_comment": 1
    }
  },
  "obligations": [
    {
      "addr": "0xf978",
      "panic_fn": "core::panicking::panic_fmt",
      "sink_flavor": "explicit_panic",
      "status": "comment_precise",
      "source": {
        "file": "kernel/src/grant.rs",
        "line": 1442,
        "func": "kernel::grant::Grant::enter"
      },
      "annotation": {
        "kind": "FLUX-OPT",
        "addr_form": "list",
        "file": "kernel/src/grant.rs",
        "line": 1440,
        "raw": "// FLUX-OPT addrs=[0xf978,0xf97c,...] flavor=explicit_panic"
      }
    },
    {
      "addr": "0xabcd",
      "panic_fn": "core::panicking::panic_fmt",
      "sink_flavor": "explicit_panic",
      "status": "stdlib",
      "source": {
        "file": "/rustc/.../library/core/src/slice/index.rs",
        "line": 187,
        "func": "core::slice::index::slice_index_len_fail"
      },
      "annotation": null
    },
    {
      "addr": "0x1234",
      "panic_fn": "core::panicking::panic_fmt",
      "sink_flavor": "slice_index",
      "status": "missing_comment",
      "source": {
        "file": "capsules/extra/src/new_thing.rs",
        "line": 42,
        "func": "capsules_extra::new_thing::do_stuff"
      },
      "annotation": null
    }
  ]
}
```

### Field notes

- **Entry granularity is per-instruction**, not per-source-site. The 50
  monomorphization-duplicated `bl`s at `grant.rs:1442` produce 50 entries, all sharing the
  same `annotation` block. This is intentional: every binary `bl` is independently
  accounted for, so a regression where 49 of 50 are covered but one isn't surfaces as
  exactly that — not as "the site is annotated."

- **`source` mirrors `panic_survey.json`'s `effective_frame`** with the same field names
  (`file`, `line`, `func`, `sink_flavor`). Don't rename. `func` is the post-LTO asm owner,
  not the source-enclosing function; this can differ for cross-crate inlined panics.

- **`annotation.addr_form`** is `single` if the comment uses `addr=0x...` (one
  instruction), `list` if it uses `addrs=[0x..., ...]` (multiple instructions). For
  `comment_fn_level` entries this is always `list`. Recorded so future analyses can
  distinguish "this site survived LTO as one instruction" from "this site
  monomorphized to N instructions" from "this site was lost to LTO."

- **`annotation.raw`** is the verbatim comment text. Useful for debugging when something
  looks wrong; cheap to include.

- **`annotation.line`** is the line of the comment itself, which differs from
  `source.line` (the panic instruction's resolved line). For precise annotations the
  comment usually sits one line above the panic; for `FN-LEVEL` it sits on the enclosing
  function definition.

- **`annotation: null`** for `stdlib` and `missing_comment` entries.

## Exit policy

- Exit 0 if every Tock-local panic has a covering annotation (`comment_precise` or
  `comment_fn_level`).
- Exit non-zero if any `missing_comment` entries exist. The report is written regardless;
  the exit code is the gate, the JSON is the diagnostic.
- The script reports all `missing_comment` entries in a single pass. It does not stop at
  the first.

## What this script does NOT do

- It does not add or modify any `// FLUX-` comments. Authoring annotations is manual.
- It does not check whether a `flux_support::assert(...)` follows an annotation. That's
  invariant 2.
- It does not validate the semantic correctness of any annotation's predicate. Also
  invariant 2.
- It does not re-validate step 0's invariants. If step 0 hasn't been run since the last
  source change, the script may produce inconsistent results — that's a workflow issue,
  not a step-1 concern. (Future improvement: a freshness check.)

## Dependencies

- `panic_survey.json` exists and is fresh. Schema documented in `methodology.md`.
- Step 0 (`reannotate_flux.py`) has run successfully on the current source. All `addr=`
  and `addrs=[...]` fields in source comments are up-to-date.

## Usage

### Invoking

```bash
# Canonical run: audit the current tree against the survey step 0 just re-anchored.
tools/.venv/bin/python3 tools/check_invariant1.py

# Audit against a survey at a non-default path:
tools/.venv/bin/python3 tools/check_invariant1.py --survey path/to/panic_survey.json

# Write the report somewhere other than tools/invariant1_report.json:
tools/.venv/bin/python3 tools/check_invariant1.py --out /tmp/invariant1_report.json
```

The script is stdlib-only, so plain `python3` works. Unlike step 0, step 1 never
rebuilds the board or invokes `panic_survey.py` — it reads an existing
`panic_survey.json` (default `tools/panic_survey.json`), because step 0 has already
produced a fresh one. `--repo-root` overrides the audited tree (default: the parent of
`tools/`). The first line of every run is the scope summary
(`checking N panic instructions: K stdlib, M Tock-local`) — the audit trail of what
was looked at; it prints even when the run exits non-zero.

Step 1 assumes step 0 has been run successfully against this same survey. If it
hasn't, the markers' `addr=` / `addrs=[...]` fields are stale relative to the binary
and step 1 will report nearly every Tock-local panic as `missing_comment`. That is a
workflow symptom, not a step-1 finding — run step 0 first, then step 1.

### Exit codes

| code | meaning | report |
|---|---|---|
| `0` | every Tock-local panic instruction is covered; no overlaps | `invariant1_report.json` written |
| `1` | one or more `missing_comment` instructions and/or precise/FN-LEVEL overlaps | `invariant1_report.json` written — the exit code is the gate, the JSON the diagnostic |
| `2` | usage / I/O / survey error | not written |

The report is written on both `0` and `1`; all violations are reported in a single
pass (every `missing_comment` and every overlap, not just the first).

### When a violation fires

- **`missing_comment`** — a Tock-local panic instruction that no `// FLUX-` annotation
  claims. Author a marker for it: a precise `FLUX-TODO`/`FLUX-OPT` 1–5 lines above the
  obligation if its source line survived LTO, or a `FLUX-TODO-FN-LEVEL` on the
  enclosing function if the line was lost. Then re-run step 0 to anchor the new
  marker's addresses against the binary, then re-run step 1. Step 1 will not pass
  until every Tock-local panic is claimed by some annotation.

- **precise/FN-LEVEL overlap** — a single address is claimed by both a precise marker
  (`addr=` / `addrs=[...]`) and a `FLUX-TODO-FN-LEVEL` marker's `addrs=[...]`. The
  methodology keeps these address sets disjoint: precise markers own line-survived
  addresses, FN-LEVEL owns line-lost ones. Resolve it in source — either fold the
  whole site into a single precise `addrs=[...]` marker covering all instructions, or
  drop the address from whichever marker should not own it. (This is the narrow case
  step 0's exit-condition-3 carve-out tolerates; step 1 does not — let the
  contradiction surface here and fix it in source.)