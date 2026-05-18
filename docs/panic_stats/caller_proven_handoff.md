# Caller-proven workflow — pickup brief

> Tomorrow-morning you + Claude. Self-contained. Don't ask the original
> author questions; everything you need is here or linked from here.

## TL;DR — what to do tomorrow

1. Open `tools/caller_closure_flux.json`. Each entry is a locally-proven
   panic row from `tools/panic_sites.md`.
2. Pick a row whose `coverage == "caller_proven_candidate"`. (Rerun the
   script first; the new strict rules — see §6 — will have moved many former
   candidates into the `blocked_*` buckets.)
3. Its `closure.nodes` lists every transitive caller. Most will be marked
   `flux_status: def_included / whole_file_included / whole_crate_default`.
   Those are already in Flux's check set.
4. Audit: for each caller in the closure, look at the call site (from the
   matching edge's `span` field) and confirm it discharges the
   panic-bearing function's `requires` precondition (recorded in the row's
   Notes column in `tools/panic_sites.md`).
5. If yes for all callers, flip the row's status from `locally proven` to
   `caller proven` (after extending the existing schema; see §6 below).
6. If no, that's the real work — add a Flux sig to the caller that
   establishes the precondition, or propagate the precondition upward.

For `partial_no_flux` rows the work is narrower: find which file is not yet
covered, add it to the appropriate crate's `[package.metadata.flux] include`
list, re-run the tool to confirm.

For `blocked_*` rows, the script has already identified concrete evidence
(assume lines, trusted def_paths) in the row's `blockers` field — discharge
those and re-run. Easy rows first: do them in the order listed in §9.

## 1. What "locally proven" means and where the gap is

`panic_sites.md` is the human-curated table of every panic site in the
nrf52840dk release binary. A row's `Status` is `locally proven` when Flux
verifies the **enclosing function's body** under whatever
`#[flux_rs::sig(...)]` `requires` clause we gave it. That means:

- The body can't panic at this site IF the caller satisfies the sig.
- It does NOT mean every caller actually does satisfy it.
- It does NOT mean the binary has fewer panic instructions.

`caller_proven` means **all three** of the following hold for a row:

1. The enclosing function body contains no `flux_support::assume(...)` calls.
2. No function in the transitive caller closure is `#[flux_rs::trusted]`
   (or sits inside a `#[flux_rs::trusted_impl]` block).
3. No call site in the closure discharges the precondition via a
   `flux_support::assume(...)` earlier in the caller's body.

The strict definition matters because the endgame is replacing each
caller-proven panic-bearing operation with its `_unchecked` variant
(`unwrap_unchecked`, `get_unchecked`, `unreachable_unchecked`) so the panic
is removed from codegen — which is unsound if the proof has a runtime
escape hatch (assume) or an unverified link in the chain (trust marker).
A weaker definition would still be useful for documentation purposes but
would NOT license the unsafe replacement step.

The strategic philosophy is **easy rows first**: pick `caller_proven_candidate`
rows that already pass all three rules, walk their audits end-to-end,
upgrade their status. Rows in the `blocked_*` buckets are harder and need
specific work (discharge an assume, un-trust a boundary) before they qualify.

Current state (2026-05-17): 75 locally proven / 7 actionable. Four real
bugs have been found along the way (see memory entry
`project_real_bugs_found.md` + the LOWPAN_NHC ext-hdr `len < 6` underflow
in `sixlowpan_compression.rs`, papered over by a
`flux_support::assume(len >= 6)` inside the extracted helper — that one's
the freshest, found while extracting decompress for verification).

## 2. The call-graph data

`cargo flux -- -Femit-callgraph=<path>` (run with
`FLUXFLAGS="-Femit-callgraph=/tmp/{crate}.cg.json"`) emits per-crate JSONs.
This was added to upstream Flux as a sibling Claude project; it's installed
in `~/.flux/`. Each JSON looks like:

```json
{
  "crate": "capsules_extra",
  "edges": [
    {
      "caller": "net::sixlowpan::sixlowpan_state::RxState::<'a>::receive_next_frame",
      "callee": "net::sixlowpan::sixlowpan_compression::decompress",
      "edge_kind": "direct" | "trait_dispatch_resolved" | "trait_dispatch_unresolved" | ...,
      "span": "capsules/extra/src/net/sixlowpan/sixlowpan_state.rs:757"
    },
    ...
  ],
  "unresolved": [ { "site": "...", "reason": "AnalyzerPanic(...)" } ]
}
```

**Critical:** the dump happens BEFORE Flux's refinement-type verification,
so `error[E0999]` errors don't block emission. A `flux_support::assume` or
`#[flux_rs::trusted]` does NOT block emission. The only thing that can
block a crate's dump is a rustc-internal panic, and even then per-fn
`catch_unwind` records the failed fn in `unresolved` and keeps walking.

**Where the JSONs live:** `/tmp/<crate>.cg.json` (regenerate any time with
`FLUXFLAGS="-Femit-callgraph=/tmp/{crate}.cg.json" cargo flux --package <pkg>`).
Each package invocation dumps that crate + its build-chain deps.

## 3. The Python tool

`tools/caller_closure_flux.py` consumes those JSONs and produces per-row
analysis. Run with:

```
tools/.venv/bin/python tools/caller_closure_flux.py
```

It produces `tools/caller_closure_flux.json` (graph data) and
`tools/caller_closure_flux.md` (readable summary).

### What it does mechanically

For each row in `panic_sites.md` with `Status == "locally proven"`:

1. Reads file:line from the Location column.
2. Walks back in the file to find the enclosing `fn NAME(...)`.
3. Suffix-matches `NAME` against every node's `def_path_str` in the
   loaded JSONs.
4. If multiple matches, **disambiguates by file**: keeps only
   def_paths whose forward-edge spans land in the panic-row's file.
5. BFS upward via the inverse call graph (callee → callers) from the
   matched def_path(s) up to `--max-depth` (default 6).
6. For each transitive caller, classifies `flux_status`:
   - `def_included` — the function's `def_path_str` contains a `def:NAME`
     substring from the include list.
   - `whole_file_included` — the function's source file appears (as glob
     or path) in the include list.
   - `whole_crate_default` — the crate has `enabled = true` but NO
     `include` filter, so Flux checks everything in that crate.
   - `not_included` / `flux_disabled` / `not_in_flux_crate` — the
     remaining gap categories.
7. Emits a verdict per row (see §4).

### What it doesn't do

- It does NOT verify that callers actually discharge the panic-bearing
  function's `requires`. That's the hand-audit step — read each call
  site, look at what arguments are passed, confirm the precondition holds.
- It does NOT understand `flux_support::assume(...)`. Each `assume` is
  a runtime panic disguised as a precondition; the actual upgrade path
  is to discharge them at their callers too.

## 4. The verdicts

Counts are stale — rerun the script (§5) to get the current distribution
under the strict rules. The verdict space is:

| Verdict | What it means | What to do |
|---|---|---|
| `caller_proven_candidate` | Closure shape clean (resolved edges, Flux-included nodes) AND all three strict rules pass. | Hand-audit the call sites to confirm the precondition discharges, then flip the row to `caller proven`. |
| `blocked_body_assume` | Same closure shape, but the panic-bearing fn's body contains `flux_support::assume(...)`. | Discharge the assume(s) by tightening the fn's sig so the property follows statically. Each assume line is in the row's `blockers.body_assumes` field. |
| `blocked_trust_boundary` | Same closure shape, but at least one node in the closure is `#[flux_rs::trusted]` (or inside a `#[flux_rs::trusted_impl]`). | Un-trust the boundary — verify its body under a Flux sig — or accept it's not (yet) caller-proven. The offending def_paths are in `blockers.trusted_nodes`. |
| `blocked_caller_assume` | Same closure shape, but a caller's body contains a `flux_support::assume(...)` earlier than the call site — runtime discharge of the precondition. | Replace the assume with a static proof (sig on the caller). Call sites + assume lines are in `blockers.caller_assumes`. |
| `partial_no_flux` | Edges resolve, but at least one caller's file isn't in any Flux include. | Add that file to the crate's `[package.metadata.flux] include = [...]`; re-run. |
| `partial_unresolved_edges` | Some edge is `trait_dispatch_unresolved` or similar. | Resolve the dispatch (often: add associated refinements; see [[feedback_associated_refinements]]). |
| `no_callers_in_loaded_graphs` | Function exists in the graphs but has no inbound edges. | Dump callgraph for the board crate (`nrf52840dk`); if still no callers, it's an entry point and that's the boundary. |
| `no_def_path_match` | Panic-fn's simple name didn't match any node in the loaded callgraphs. | Almost always means the row's crate's callgraph wasn't loaded — see §5. |
| `no_line_in_panic_sites` | `panic_sites.md` has no `:line` for this row (stale address from old binary). | Re-survey against a fresh release binary. |
| `no_enclosing_fn` | Parser couldn't find `fn NAME(` decl from file:line. | Edge case in the regex; investigate manually. |

## 5. Regenerating the call-graph data

```bash
cargo flux clean
rm -f /tmp/*.cg.json
FLUXFLAGS="-Femit-callgraph=/tmp/{crate}.cg.json" cargo flux --package capsules-extra
FLUXFLAGS="-Femit-callgraph=/tmp/{crate}.cg.json" cargo flux --package nrf52
# Add the board crate to also capture entry-point callers:
FLUXFLAGS="-Femit-callgraph=/tmp/{crate}.cg.json" cargo flux --package nrf52840dk-lib  # not yet flux-enabled; needs setup
tools/.venv/bin/python tools/caller_closure_flux.py
```

The `--package` invocations process that crate + its build-chain deps.
`{crate}` is interpolated automatically.

## 6. The handoff workflow per row

### Operative definition (strict)

A `locally proven` row in `panic_sites.md` may be upgraded to `caller proven`
iff **all three** conditions hold:

1. **No body assumes.** The enclosing function body of the panic site
   contains no `flux_support::assume(...)` calls (or bare `assume(...)` from
   `use flux_support::assume`).
2. **No trusted callers.** No function in the transitive caller closure is
   marked `#[flux_rs::trusted]`, and no caller sits inside a
   `#[flux_rs::trusted_impl]` block.
3. **No caller-site assumes.** No call site in the closure has a
   `flux_support::assume(...)` earlier in its enclosing function (i.e., the
   precondition is not discharged via a runtime panic).

The script (`tools/caller_closure_flux.py`) enforces all three: rows that
pass land in `caller_proven_candidate`; rows that fail are routed to
`blocked_body_assume`, `blocked_trust_boundary`, or `blocked_caller_assume`,
with concrete evidence in the row's `blockers` field. Priority order if a
row would fail multiple rules: body > trust > caller-assume.

### Steps for a single row

1. **Read the row in `panic_sites.md`.** Note its `requires` precondition
   from the Notes column (or from the sig in source).
2. **Look up the row in `caller_closure_flux.json`.** Confirm
   `coverage == "caller_proven_candidate"`. If it's `blocked_*`, the
   `blockers` field tells you exactly what to discharge first.
3. **For each edge in `closure.edges`:** read the call site at
   `<file>:<line>` from `edge.span`. Confirm that at that call site, the
   arguments passed satisfy the callee's `requires`.
   - If the caller is itself Flux-verified with a stricter sig that
     propagates the precondition upward, ok — the closure stays clean.
   - If the call site just literally passes ok values (e.g. a buf with
     a constant length), document it.
   - If the call site can't satisfy the precondition, either add a sig
     to the caller that enforces it (propagate upward), or — for purely
     verification purposes only — use `flux_support::assert(...)` at the
     call site. Do NOT use `flux_support::assume(...)`: that would fail
     rule 3 and demote the row.
4. **Run `cargo flux clean && cargo flux --package <crate>`** (per-crate, per
   [[feedback_flux_scoped_runs]]). Make sure no new `error[E0999]` errors are
   introduced by the additional sigs.
5. **Flip the row's `Status` to `caller proven`** in `panic_sites.md`
   (this requires extending the status enum and the
   `tools/panic_stats_md.py` color palette to recognize it — see §9 step 2).

### Cautionary example: row `0xadbc` (decompress, sixlowpan)

Under the strict definition this row is **NOT** caller-provable today. It's
worth walking through anyway because it illustrates each rule firing:

- `panic_fn`: `decompress` in `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:730`
- Precondition (from Notes): `requires buf_len >= 42 && out_len >= 40`
- Rule 1 violation: the body contains multiple `flux_support::assume(...)`
  calls (e.g. `len >= 6` on line 637, `buf.len() - consumed >= 42` on
  line 781). Each is a runtime escape hatch covering a protocol invariant.
- Rule 2 violation: the closure includes `RxState::receive_next_frame` and
  `receive_single_packet`, both `#[flux_rs::trusted]` in sixlowpan_state.rs.
- Verdict from the script: `blocked_body_assume` (body wins under the
  priority order).

To upgrade: discharge the body assumes by tightening `decompress`'s sig (or
by hoisting them to the caller as `requires` clauses), AND un-trust the
two sixlowpan_state.rs callers by verifying their bodies. Both are real
work, not boilerplate — these 8 sixlowpan rows are firmly in the "hard"
bucket. Tackle the easy rows in §9 first.

## 7. Pointers (files to read first)

In order:

1. `tools/panic_sites.md` — the canonical row table.
2. `tools/caller_closure_flux.py` — read top-to-bottom; ~400 lines.
3. `tools/caller_closure_flux.json` + `tools/caller_closure_flux.md` —
   current output.
4. Memory entry `project_real_bugs_found.md` — the real bugs found via Flux
   verification, for background context on the project's flavor.
5. `flux/crates/flux-opt/src/lib.rs` (in the Flux repo) — the
   `build_call_graph` primitive and the `dump_call_graph` extension.

Memory entries in `/Users/andrew/.claude/projects/-Users-andrew-research-tock/memory/`:
- `feedback_flux_include_filter_quirks.md` — quirks of `def:` / `span:` / glob.
- `feedback_flux_scoped_runs.md` — always run `cargo flux` per-crate.
- `feedback_flux_defs_macro.md` — `flux_rs::defs!` vs `#[flux::defs{}]`.
- `feedback_no_behavior_changes.md` — preserve Tock's behavior; assumes are
  the gray-zone case.

## 8. Known issues to be aware of

- **`flux_support::assume(...)` calls in already-flipped rows.** Each is a
  runtime panic for a protocol invariant. The biggest one — `len >= 6`
  in `decompress_ext_hdr` — papers over a real Tock input-validation
  bug. Each row's Notes column lists which assumes its proof depends on.
  To get from `caller_proven` to "binary panic free," every assume needs
  to be discharged.
- **The new Flux build is on `nightly-2025-11-25`.** Tock's
  `rust-toolchain.toml` pins `nightly-2025-03-14`. There are some
  refinement-type regressions in capsules-extra: two sites flag
  `error[E0999]: refinement type error` under the new flux but were clean
  under the old build. Specifically: `compress` call at
  `sixlowpan_state.rs:494`, `encode_ccm_nonce_buf` call at `framer.rs:252`.
  Callgraph emission still works (it happens before verify), but the
  refinement errors are real and worth investigating separately.
- **Generic-name fan-out.** Functions named `new`, `next`,
  `copy_from_slice` etc. share their name with many unrelated impls. The
  Python tool uses the panic-row's file path to disambiguate forward
  edges. One row (`0xa0f8 new` in `leasable_buffer.rs`) still fans out
  because `new` is a constructor with no informative outgoing edges; that
  one needs hand-pinning of the right `def_path_str`.
- **`flux::defs{...}` vs `flux_rs::defs! { ... }`.** Only the proc-macro
  form (`flux_rs::defs!`) survives non-flux builds. The tool-attribute
  form needs `#![register_tool(flux)]`. We use the macro form throughout
  Tock.

## 9. Concrete next session goals (ranked)

1. **Re-run the script under the strict rules and triage.**
   `tools/.venv/bin/python tools/caller_closure_flux.py` — the new verdict
   distribution will tell you which rows survive as
   `caller_proven_candidate` (the easy bucket) vs which got demoted to
   `blocked_body_assume`, `blocked_trust_boundary`, or
   `blocked_caller_assume` (the harder bucket, with concrete worklists in
   each row's `blockers` field).
2. **Pick 1-2 small-closure `caller_proven_candidate` rows and walk the
   audit end-to-end.** Validate the workflow and produce the first
   `caller proven` rows. Pick rows with shallow closures (≤3 nodes) and
   simple preconditions (a single length constraint, not a structural
   invariant). Avoid the sixlowpan rows — they all sit in
   `blocked_body_assume` per §6's cautionary example.
3. **Extend `panic_sites.md`'s Status enum to include `caller proven`.**
   Update `tools/panic_stats_md.py` to render the new status (add a color
   to `COLORS`, treat it as a separate bucket). Flip the audited rows.
4. **Dump the board callgraph** to resolve the `no_callers_in_loaded_graphs`
   rows. The board crate isn't currently Flux-enabled; needs a small
   Cargo.toml setup. (See `boards/nordic/nrf52840dk/Cargo.toml` for what
   needs adding.)
5. **Re-survey to pin the stale-line rows.** Run
   `tools/.venv/bin/python tools/panic_survey.py` against a fresh release
   binary; cross-reference with the addresses in `panic_sites.md`.
6. **Pick off one `blocked_body_assume` row by discharging its assume(s).**
   Each assume is a documented protocol invariant — tightening the sig to
   establish it statically is the unit of work. Start with whichever
   `blocked_body_assume` row has the fewest assumes.
7. **(Stretch) Replace a fully caller-proven panic with its `_unchecked`
   variant.** Pick a row that's been `caller proven` for a while, swap
   `unwrap()` → `unwrap_unchecked()` (or `[i]` → `get_unchecked(i)`),
   rebuild the release binary, and confirm the panic disappears from the
   survey output. This is the payoff of the strict definition — and the
   sanity check that the proof chain is actually load-bearing.
8. **Investigate the new-flux refinement regressions** at
   `sixlowpan_state.rs:494` and `framer.rs:252`. Are these real
   tightenings (the OLD flux had unsoundness) or new flux regressions?

## 10. The single-paragraph "start here" for a fresh Claude session

You're picking up a Tock panic-verification project. ~75 panic sites have
been verified `locally proven` by Flux, given preconditions. The goal of
this push is to upgrade them to `caller proven` — defined strictly in §6:
the body has no `flux_support::assume(...)`, no caller in the closure is
`#[flux_rs::trusted]` / inside a `trusted_impl`, and no caller-site assume
discharges the precondition at runtime. The strict definition matters
because the eventual payoff is replacing each caller-proven panic with its
`_unchecked` variant in code so the panic leaves the binary — unsafe
that's only sound under the strict rule. We have a Flux-emitted call graph
(per-crate JSON dumps in `/tmp/*.cg.json`) and a Python tool
(`tools/caller_closure_flux.py`) that consumes them. The current report is
in `tools/caller_closure_flux.json` (+ .md). Re-run the tool first; rows
that survive the strict rules get `coverage == "caller_proven_candidate"`,
rows that fail are routed to `blocked_body_assume` /
`blocked_trust_boundary` / `blocked_caller_assume` with concrete evidence
in their `blockers` field. **Easy rows first** — pick a shallow-closure
`caller_proven_candidate` row and walk its audit end-to-end before going
near the sixlowpan cluster (§6 cautionary example shows why that's hard).
