# Step 0: Audit and re-anchor source annotations

Before invariant 1 can run, every `// FLUX-` annotation in the Flux source must be
(a) well-formed, (b) keyed to a panic instruction that exists in the current release
binary, and (c) free of ambiguity. Addresses drift on every LTO rebuild, so this
audit re-runs every time the pipeline runs. `reannotate_flux.py` is the script that
performs the audit and rewrites stale `addr=` / `addrs=[...]` fields against the
current binary.

## Source convention

Authors place the `// FLUX-` comment 1–6 lines immediately above the panicking
obligation it annotates. This is the layout the script anchors against — every
precise-marker join uses the comment's own physical line number, not any value
carried in the marker's fields. See step 5. (Six lines, not five: the
assert-pairing convention puts a `flux_support::assert(...)` line directly
below the marker, and the panic may then live a few more lines down through
multi-line method-call chains, so the window has to be one line wider than the
"raw" comment-to-panic distance.)

**A precise `addrs=[...]` list must stay on a single physical line**, however long.
A precise marker is anchored by its own line P and its obligation must fall within
`P ≤ line ≤ P + 6` (step 5). Wrapping the list across multiple comment lines makes
the marker block taller than that window, pushing the panic past `P + 6`, so the
marker orphans *itself* on the next run. (FN-LEVEL markers are anchored by function
name, not the line window — see step 6 — so they may wrap; precise markers may not.)

**Every precise marker is paired with a `flux_support::assert(...)` call below it.**
The assert is the proof obligation the marker represents — its predicate is what
Flux will be asked to discharge in invariant 2. Stacked precise markers (multiple
markers immediately above the same panicking statement) all share the one assert
that follows the stack. FN-LEVEL markers are exempt: they sit above a `fn`
declaration and the proof obligation for a line-lost panic lives in the function's
signature or body, not in an assert immediately below the marker. See step 7.

## What the script does

1. Invokes `panic_survey.py` to produce `panic_survey.json` — every `bl` to a panic
   sink in the release binary, with the `addr2line`-resolved `effective_frame`
   (`file`, `line`, `func`) and a `flavor` classification.

2. Walks every in-scope source file (see "In-scope files" below) and parses every
   `// FLUX-...` comment.

3. Classifies each comment by kind:

   - `FLUX-TODO addr=0x... flavor=...` — single-instruction precise marker.
   - `FLUX-TODO addrs=[0x..., ...] flavor=...` — multi-instruction precise marker;
     the source line survived but resolves to N instructions all attributable to
     it (monomorphization, inlining-into-this-line, etc.).
   - `FLUX-OPT addr=0x... flavor=...` / `FLUX-OPT addrs=[0x..., ...] flavor=...` —
     same two forms as `FLUX-TODO`.
   - `FLUX-TODO-FN-LEVEL addrs=[0x..., ...] flavor=...` — line-loss marker; the
     source line was lost in LTO and the panic lives somewhere in the enclosing
     function. The addrs are the binary addresses of the lost-line panics that
     bubbled up to this function. May optionally carry `file=<repo-relative
     path>` to override the file used for the survey join — see step 6.

   Anything else is **malformed**. This includes the deprecated `FLUX-TODO-BLOCKED`
   kind, any precise marker carrying both `addr=` and `addrs=[...]` simultaneously,
   and any marker carrying neither (free-text `// FLUX-TODO:` prose with no address
   field is malformed). The deprecated `covers=` and `line=` fields are handled as
   one-time migrations — see "Migration" below.

   **Flavor values.** The valid `flavor=` values are the sink_flavors emitted by
   `panic_survey.py`, plus the wildcard value `mixed`. `flavor=mixed` is valid
   *only* on `FLUX-TODO-FN-LEVEL` markers, where it indicates that the lost-line
   panics in the enclosing function are of multiple distinct flavors and the
   marker covers all of them. `flavor=mixed` on a precise marker is malformed.

   **Unknown extra fields.** Markers may carry additional unrecognized
   `key=value` fields (e.g. `reason=...`) for human-readable context. These are
   preserved verbatim on rewrite and do not affect classification or join. This
   keeps the grammar forward-compatible.

   **Semantic distinction.** `FN-LEVEL addrs=[...]` means *the obligation
   belongs at the function level*. This covers two structurally similar cases:
   (a) the source line was lost in LTO so addr2line cannot attribute the panic
   to a specific call site, and (b) addr2line did attribute the panic to a
   known line but the predicate is naturally a function-level concern (e.g. a
   panic inside a callee that LTO inlined into this function, where the
   caller-side line is known but the proof obligation is still a property of
   the enclosing function rather than the specific call site). Precise
   `addrs=[...]` (TODO or OPT) means *line known, and the proof obligation
   lives at that specific source line, which the compiler emitted as multiple
   instructions*. These are different cases; the script does not conflate
   them. A precise marker carrying `addr=` and a precise marker carrying
   `addrs=[...]` are the same kind in two forms — pick one form per marker,
   never both.

4. **Anchor by physical line.** For every well-formed `// FLUX-` comment, the
   anchor is the comment's own physical line number P in the source file, not any
   value carried in the marker's fields.

5. **Join precise markers** (TODO and OPT, both forms). Find the in-scope record in
   `panic_survey.json` with matching `(effective_frame.file, flavor)` and
   `effective_frame.line ≥ P`, minimizing `effective_frame.line − P`. The match
   window is `P ≤ line ≤ P + 6`; no record in that window means the marker is
   orphaned (exit condition 2). The matched record's line is the panic's true
   source line.

   - `addr=` form → rewrite to the matched record's address. If the file+line+flavor
     resolves to more than one record, exit condition 3 (ambiguous) fires unless a
     FN-LEVEL annotation in the same enclosing function covers it.
   - `addrs=[...]` form → rewrite the list to the full set of addresses at that
     file+line+flavor. If the set has exactly one element, exit condition 5
     (over-specified) fires.

   The 6-line window reflects the source convention. Markers placed below their
   obligation, or more than 6 lines above, will orphan. This is intentional — it
   forces a uniform layout that the script can mechanically anchor against, and it
   surfaces drift (panic moved, comment stranded) as a finding rather than silently
   re-anchoring.

   **Closest unclaimed line.** When several precise markers in the same file have
   overlapping windows — stacked above adjacent panics within six lines —
   independently picking "the minimum line ≥ P" for each marker lets two markers
   collide on the same source line, because LTO+addr2line line attributions can
   shift by 1–3 lines on every rebuild, and a shift that puts the prior nearest
   panic *out* of one marker's window leaves it pointing at the next one its
   neighbour was already claiming. To keep the join stable across rebuilds, the
   file's precise markers are processed in anchor-line order; each successful
   match claims its `(flavor, line)` pair, and later markers in the same flavor
   must pick the closest *unclaimed* line in their window. The single-marker
   case is unchanged (the claim set is empty, so the filter is a no-op). If
   every in-window line is already claimed by an earlier marker, the marker
   orphans (exit condition 2) — typically a duplicate stacked marker or one
   whose panic has been deleted.

6. **Join FN-LEVEL markers.** Join on `(effective_frame.file, effective_frame.func,
   flavor)` against *all* records in `panic_survey.json` whose enclosing function
   matches — both LTO-line-lost records (`effective_frame.line == null`) and
   known-line records. The known-line case covers panics that addr2line
   attributes to a specific source line but whose proof obligation is
   structurally a function-level concern (e.g. a callee inlined into this
   function — the line lives in this file but the predicate belongs to the
   enclosing fn, not the specific call site). The annotation-side function for
   the join is derived from the comment's enclosing source function
   (segment-match against `effective_frame.func`). When the marker's flavor is
   `mixed`, the flavor component of the join is wildcarded and any flavor
   matches. Collect all matching binary addresses and rewrite the `addrs=[...]`
   list to that set. Physical-line anchoring does not apply to FN-LEVEL — the
   function name is the anchor, regardless of whether the matched records
   carry a line number.

   **Impl-type disambiguation.** When the marker sits inside an `impl Foo` block
   (with or without `for Bar`; the target type is the one being implemented on,
   i.e. `Bar` in `impl X for Bar` or `Foo` in `impl Foo`), the join additionally
   requires that target type's identifier to appear as a token in
   `effective_frame.func`. Without this, a bare `fn_name in tokens(func)`
   segment-match cross-pulls panics from same-named methods on different types
   in the same file — e.g. an `impl X for MuxAES128CCM`'s `crypt_done` and an
   `impl X for VirtualAES128CCM`'s `crypt_done` both expose `crypt_done` as a
   token. The impl-type constraint keeps the FN-LEVEL above one impl from
   claiming the other's addresses. Free functions (no enclosing impl) keep the
   bare fn-name match — no constraint is added.

   Note: `effective_frame.func` is the binary-side asm owner after inlining, which
   often differs from the source function containing the panicking call site. This
   is expected to orphan a significant fraction of FN-LEVEL markers; those are
   findings for manual triage, not a script bug.

7. **Verify assert pairing (precise markers only).** For every precise marker
   (`FLUX-TODO` / `FLUX-OPT`, either form), scan downward from the line after the
   marker block's last line E, skipping blank lines and any lines whose stripped
   form starts with `//` (this skips the marker's own continuation `//` notes and
   any further stacked precise markers immediately below it). The first
   non-skipped source line must *contain* a `flux_support::assert(` call — the
   match is by substring (`\bflux_support::assert\s*\(`), not by line prefix, so
   match-arm shapes like `_ => { flux_support::assert(false); panic!(...) },`
   pair cleanly. The Rust stdlib macro `assert!(...)` does NOT count — only the
   Flux refinement function `flux_support::assert(...)` is a valid pairing. If
   the first non-skipped line contains no such call — including the case where
   the marker is at end-of-file with no following real source line — the marker
   is unpaired (exit condition 6).

   **Inside the `flux_support` crate itself.** A crate cannot refer to its own
   functions by its crate name (`flux_support::assert(...)` is unresolved from
   inside `flux_support/src/...`); the canonical reference there is
   `crate::assert(...)`. For markers in files under `flux_support/`, the pairing
   substring relaxes to `\b(?:flux_support|crate)::assert\s*\(` — either form
   pairs cleanly. Outside `flux_support/`, only the fully-qualified form
   pairs, since `crate::assert` would resolve to a different function (or
   nothing) in other crates.

   Stacked precise markers share a single assert below the stack. The canonical
   example is `kernel/src/utilities/leasable_buffer.rs::Index::index`, where
   three precise markers (`slice_order`, `slice_end`, `bounds`) all pair with
   one `flux_support::assert(...)` whose compound predicate discharges all
   three flavors. The pairing check resolves each stacked marker to the same
   assert independently — there is no "first marker owns the assert, others
   are unpaired" rule.

   FN-LEVEL markers are not subject to this check. They sit above a `fn`
   declaration (possibly with intervening attributes like
   `#[flux_rs::trusted(...)]` or doc comments) and the proof obligation lives
   in the function's signature or body.

   This step verifies only the assert's *presence*. It does not check whether
   the assert's predicate is sound, whether the predicate matches the panic's
   flavor, or whether Flux can discharge it — those are invariant 2's concerns.

   **Optional `file=<path>` override.** By default the join's file component is
   the marker's own source file; FN-LEVEL markers may carry `file=<repo-relative
   path>` to point the join at a different file instead. addr2line sometimes
   attributes a line-lost panic to the *origin* frame (e.g., a generic helper in
   `kernel/src/grant.rs`) while LTO has inlined the call into a binary symbol
   that lives in a different source file (e.g., `capsules/core/src/console.rs::
   transmitted_buffer`). Authoring the marker above the binary-owning fn but
   pointing it at the origin file via `file=` lets a single FN-LEVEL marker
   claim those addresses. The override path is repo-relative (with or without a
   leading `./`); the marker's enclosing source function still drives the
   `func` segment-match. `file=` on a *precise* marker is malformed — the
   override is FN-LEVEL-only.

## In-scope files

The set of source files this script audits is derived from `panic_survey.json`, not
hardcoded. The set is the union of:

(a) every `effective_frame.file` referenced by a record in `panic_survey.json` that
    represents a Tock-local panic (not a stdlib helper or external crate);
(b) every file in the repo containing a `// FLUX-` annotation.

(b) is necessary because an orphaned annotation in a file with no active panic
instructions should still surface as a violation — otherwise the script would
silently ignore stale annotations after their underlying panic sites disappear.

The filter for "Tock-local" in (a) is: `effective_frame.file` starts with `./`
(328 of the 350 records). This is keyed on the path rather than `origin_bucket`
because it is robust to the survey's bucket taxonomy drifting; it is exactly
equivalent to `origin_bucket != "stdlib"` (the only out-of-scope bucket, the 22
`/rustc/...` stdlib-helper sites). Settled in step A of the handoff.

At the start of every run, the script prints a one-line summary of the derived audit
scope:

```
auditing N annotations across M files in K crates: [crate1, crate2, ...]
```

Crate name is derived from the file path (e.g., `kernel/src/grant.rs` → `kernel`).
This summary is the audit trail for "what did the pipeline actually look at" — it
makes silent scope drift visible. Print it at the *start* of the run, not the end,
so it's visible even if the script exits non-zero partway through.

## Exit conditions

The script exits non-zero — *without* writing any changes to disk — if any of the
following are true. All violations are collected and reported in one pass; do not
stop at the first.

1. **Malformed `// FLUX-` comment.** Any comment starting with `// FLUX-` that
   doesn't match one of the kinds in step 3. Includes the deprecated
   `FLUX-TODO-BLOCKED` kind, precise markers carrying both `addr=` and `addrs=[...]`
   simultaneously, precise markers carrying `flavor=mixed`, precise markers
   carrying `file=` (the override is FN-LEVEL-only — see step 6), markers
   carrying neither `addr=` nor `addrs=[...]` (free-text prose), and — on runs
   after the first — markers still carrying the deprecated `line=` or `covers=`
   fields. Report file, line, raw text.

2. **Orphaned annotation.** A precise marker (TODO/OPT, either form) whose anchor
   line P has no `panic_survey.json` record with matching `(file, flavor)` and
   `P ≤ line ≤ P + 6`. Or a `FN-LEVEL` marker whose `(file, func, flavor)` tuple
   matches no record (with `flavor` wildcarded when the marker is `flavor=mixed`).
   Report the annotation's location and what it failed to match against — the
   anchor line P and the `(file, flavor)` for precise markers; the `(file, func,
   flavor)` tuple for FN-LEVEL.

3. **Ambiguous annotation.** A precise marker carrying `addr=` (single form) whose
   anchor-line match resolves to more than one binary instruction, *and* isn't
   covered by a `FLUX-TODO-FN-LEVEL` annotation in the same enclosing function.
   Report the marker and the multiple binary addresses. The author should either
   switch the marker to the `addrs=[...]` form (line known, multiple instructions)
   or add a FN-LEVEL carve-out. The canonical many-to-one example is
   `grant.rs:1442` (50 monomorphization-duplicated instructions → 1 source line).

   **FN-LEVEL carve-out.** A precise marker (TODO or OPT, either `addr=` or
   `addrs=[...]` form) sitting inside the body of a function that also carries
   a FN-LEVEL marker is *carved out* when its resolved address(es) intersect
   the FN-LEVEL marker's claimed `addrs` set. Carved precise markers are left
   unchanged (no re-anchoring) and are excluded from the double-marker check
   in exit condition 4. This generalises the original ambiguous-`addr=` rule:
   it also covers the case where addr2line attributes the panic to a known
   line but the FN-LEVEL marker rightfully claims the same address because
   the predicate is a function-level concern (see step 3's "Semantic
   distinction").

4. **Double-marker.** Two distinct `// FLUX-` annotations whose `addr=` fields, or
   any element of their `addrs=[...]` lists, are equal after the proposed rewrite.
   Report both annotations and the shared address. Markers carved out by exit
   condition 3's rule are excluded from this check.

5. **Over-specified plural marker.** A precise marker carrying `addrs=[...]` whose
   anchor-line match resolves to exactly one binary instruction. Report the marker;
   the author should consider promoting it to `addr=`. The script does not
   auto-promote, because a singleton `addrs=[...]` may indicate that other
   monomorphizations were dead-code-eliminated this build and will return in the
   next — auto-promoting would cause the marker to flap between builds.

6. **Unpaired precise marker.** A `FLUX-TODO` or `FLUX-OPT` marker whose first
   non-blank, non-comment source line below the marker block contains no
   `flux_support::assert(...)` call — see step 7. Report the marker's location
   and what the script actually found in that slot (the next real source line,
   or "end of file" if there is none). The fix is to insert the assert that
   discharges the panic the marker references; do not delete the marker without
   first checking whether invariant 1 still has coverage for the underlying
   binary address. The Rust stdlib `assert!(...)` macro is not a valid pairing
   — replace it with `flux_support::assert(...)` (and, if needed, keep the
   `assert!` call as well for the runtime check). FN-LEVEL markers are exempt.

Atomicity: collect all violations across the full source tree, then either write
all rewrites (if zero violations) or write none (if any). No partial progress on a
tree with violations.

If the script exits successfully, the source tree has been modified in place and is
ready for invariant 1.

## Migration

The grammar in step 3 differs from the previous spec in three ways: `line=` is gone
from precise markers, `covers=` is renamed to `addrs=` on FN-LEVEL markers, and
precise markers gain an `addrs=[...]` form. On the first run against a tree
carrying the old grammar:

- **`line=` on precise markers** is silently stripped as part of the normal
  rewrite pass. The cached values are known-stale (the data showed only a small
  minority matched the current binary), there is no per-marker decision to make,
  and surfacing each as a malformed finding would block the entire run on a
  cleanup pass with no judgment in it.
- **`covers=[...]` on FN-LEVEL markers** is silently renamed to `addrs=[...]` as
  part of the normal rewrite pass. The change is mechanical and lossless: same
  field semantics, new name.

On subsequent runs, encountering either `line=` on a precise marker or `covers=`
on a FN-LEVEL marker is exit-1 malformed, because the rewrite removed them from
any clean tree. The script does not need a flag to distinguish "first run" from
"subsequent" — it always silently migrates, and the malformed-on-subsequent-runs
rule is enforced by the fact that the rewrite removes the deprecated fields.

## What this script does NOT do

- It does not *add* new annotations. The set of FLUX-annotated panic sites is
  determined manually (Phase 1 of `methodology.md`); the script only updates
  existing ones.
- It does not validate that the `flux_support::assert(...)` predicate is sound,
  matches the panic's flavor, or is discharged by Flux. The script checks only
  that the assert is *present* below every precise marker (step 7); predicate
  correctness and Flux discharge are invariant 2's concerns.
- It does not allowlist any annotations as "intentionally double-marked,"
  "intentionally orphaned," etc. If something legitimately needs to violate one of
  the exit conditions, the methodology doc must be updated to explain why and the
  script must be updated to allow it — visibly, in source, not as a quiet config
  file.
- It does not touch any content between a `// FLUX-` comment and the obligation it
  annotates. Inline notes, blank lines, or other commentary in that gap stay
  byte-for-byte identical. The script edits only `addr=`, `addrs=[...]`, the
  one-time `covers=` → `addrs=` rename on FN-LEVEL markers, and the one-time
  `line=` strip on precise markers. Unknown extra fields (`reason=`, etc.) are
  preserved verbatim.

## Dependencies

- `panic_survey.py` exists and emits `panic_survey.json` with the schema documented
  in `methodology.md`. The script assumes that schema. `effective_frame.line` is
  used for the physical-line-anchor match on precise markers; FN-LEVEL markers
  join on `(file, func, flavor)` and accept records with either a null or a
  known `effective_frame.line` (see step 6). A null-line record with no
  FN-LEVEL marker in its function is not an error from step 0's perspective —
  it just means no annotation claims that panic, which is invariant 1's
  problem.
- The filter for Tock-local panics is `effective_frame.file` starting with `./`
  (see "In-scope files"): 328 of 350 records, equivalent to
  `origin_bucket != "stdlib"`.

## Usage

### Invoking

```bash
# Canonical pipeline run: rebuild the board, regenerate panic_survey.json, audit,
# and (if clean) re-anchor the tree in place.
tools/.venv/bin/python3 tools/reannotate_flux.py

# Skip the (slow) rebuild and reuse an ELF you already have:
tools/.venv/bin/python3 tools/reannotate_flux.py --skip-build --elf <path-to-release-elf>

# Audit against an already-generated survey, never rebuilding (CI, fast iteration):
tools/.venv/bin/python3 tools/reannotate_flux.py --survey tools/panic_survey.json

# Audit and report only -- never write, even on a clean tree (inspect proposed rewrites):
tools/.venv/bin/python3 tools/reannotate_flux.py --survey tools/panic_survey.json --dry-run
```

The script itself is stdlib-only, so plain `python3` works for everything except the
default path (which shells out to `panic_survey.py`; use the same interpreter
`panic_survey.py` needs). `--repo-root` overrides the audited tree (default: the
parent of `tools/`). The first line of every run is the scope summary — the audit
trail of what was looked at; it prints even when the run exits non-zero.

**`--survey` (and `--skip-build`) is audit-only.** It re-anchors and reports against
an *existing* survey without rebuilding, which is correct only when the source tree
has not changed since that survey was generated. Any pass that *mutates* source —
notably a marker generator that inserts `// FLUX-` comments — shifts line numbers in
the next build's debuginfo, so the survey's `effective_frame.line` no longer matches
the tree and re-anchoring orphans every shifted marker. After any source mutation,
re-anchor via the canonical path (no `--survey`), which rebuilds and re-surveys
before auditing. The fast `--survey` path is for iterating on a *static* tree (CI,
inspecting proposed rewrites).

### Exit codes

| code | meaning | tree state |
|---|---|---|
| `0` | clean — zero violations | rewrites written in place (or, with `--dry-run`, would have been); ready for invariant 1 |
| `1` | one or more violations | **nothing written**; every violation reported in one pass |
| `2` | usage / I/O / survey error | nothing written |

The run is atomic: a single violation anywhere blocks *all* rewrites, so a `1` never
leaves the tree half-migrated. Re-run after fixing violations until you get a `0`.

### When a violation fires

All violations are findings for a human to resolve in source — the script never
auto-resolves them, and there is no allowlist (see "What this script does NOT do").

- **Malformed** — fix the comment to match a step-3 grammar. Common causes and fixes:
  give a precise marker its `flavor=`; convert a `// FLUX-TODO:` free-text note into a
  real marker (or delete it if it documents nothing actionable); migrate a deprecated
  `FLUX-TODO-BLOCKED` to a real kind (or drop it); a precise marker using `covers=`
  should become `addrs=[...]`; `flavor=mixed` on a precise marker is wrong — split it
  into per-flavor precise markers or, if the line was genuinely lost, make it FN-LEVEL.

- **Orphaned** — the marker's anchor finds no panic within `P..P+6`. Either the comment
  drifted away from its obligation (move it back to 1–6 lines above the panic), the
  panic was removed (delete the marker), or the panic is line-lost and the marker
  should be `FLUX-TODO-FN-LEVEL` instead of precise. FN-LEVEL orphans are frequently
  the expected asm-owner≠source-fn case (see step 6) — triage, don't assume a bug.

- **Ambiguous** — a single-instruction `addr=` marker sits on a line the compiler
  emitted as several instructions. Switch it to the `addrs=[...]` form (line known,
  multiple instructions) or add a `FLUX-TODO-FN-LEVEL` carve-out in the enclosing
  function.

- **Over-specified** — an `addrs=[...]` marker resolved to a single instruction this
  build. Usually means other monomorphizations were dead-code-eliminated; leave it if
  you expect them back, or promote it to `addr=` if the line is genuinely singular.
  The script will not auto-promote (it would flap between builds).

- **Double-marker** — two annotations claim the same address after re-anchoring,
  typically two markers stacked above two adjacent panics where both anchored to the
  nearest one. Re-space them so each owns its own panic (1–6 lines above each), or
  collapse them if they truly describe one obligation.

- **Unpaired** — a precise marker has no `flux_support::assert(...)` below it.
  Either the assert was never authored (do it now — the marker advertises a
  proof obligation, the assert *is* the obligation), or the assert drifted away
  from the marker (move the marker back so it sits directly above the assert,
  with only blank lines and other stacked markers in between). Deleting the
  marker is rarely the right fix: it just hides the obligation from invariant
  2 while the panic remains in the binary.

### First run / migration

The first run against a tree carrying the old grammar performs two silent migrations
as part of the rewrite: it strips the stale `line=` field from precise markers and
renames `covers=` to `addrs=` on FN-LEVEL markers (see "Migration"). These are not
violations and need no action. Because the run is atomic, those migrations only land
once the tree is otherwise violation-free — so the practical first-run workflow is:
run, resolve the reported violations in source, re-run, repeat until exit `0`, at
which point `line=`/`covers=` are gone and the tree is re-anchored.

On the first run after the assert-pairing rule (step 7) lands, expect a backlog
of unpaired-marker findings — markers authored before the rule existed may sit
above panic operations without an accompanying `flux_support::assert(...)`. There
is no silent migration for this: each unpaired marker is a real authoring gap
(the proof obligation was never written down) and must be resolved by inserting
the assert. The atomic rule still applies: the tree is unchanged until every
unpaired marker is paired.