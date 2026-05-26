# Transitive panic-obligation map — plan & methodology

**Goal (Wednesday deliverable):** reframe "N scattered Flux errors" into a *systemic*
picture — Tock's panics as a transitive obligation graph: the depth=1 panic roots, the
cone of callers that reach them, and the depth>1 obligations that propagate up — plus a
methodology for *trusting* Flux's experimental no-panic inference (PR #1610).

**Thesis:** the obligation count is a function of **(a) proof effort** and **(b) Flux
capability**. It rises monotonically as we convert trusted→proven, toward a **floor** set
by what Flux can do today. **PR #1610 (no-panic inference) is the lever on (b).** The graph
to show: "obligations surfaced" rising as "functions trusted" falls, asymptoting at the
capability floor.

## The three call-graph sources + ground truth

| source | derived from | gives |
|---|---|---|
| `cargo flux -- -Femit-callgraph=/tmp/{crate}.cg.json` (already used by `tools/caller_closure_flux.py`) | Flux source analysis | edges (caller→callee, `edge_kind` ∈ {direct, trait_dispatch_resolved, trait_dispatch_unresolved}, span) |
| **PR flux-rs/flux#1610 "No panic inference"** (`crates/flux-opt/call_graph.rs` + lattice fixpoint) — **OPEN/unmerged** | Flux source analysis | a *second* call graph **+ automated no-panic inference** |
| `tools/panic_survey.json` | the **binary** (nrf52840dk disasm) | which functions *actually* reach a panic `bl` — **ground truth** |

`tools/caller_closure_flux.py` already walks up from panic functions and emits the depth>1
signal: its `blocked_obligation_unmet` verdict = a call site in the closure with a live
`E0999` (a transitive precondition the caller doesn't discharge). So step (3) is largely
built; #1610 is the new automated competitor to validate.

## The two-layer trust frontier (the core framing)

Every `#[flux_rs::trusted]`/`#[flux_rs::ignore]` is a **hole**: Flux takes the sig on faith
and does **not** propagate the callee's `requires` up through it. So the transitive count is
only well-defined **relative to a snapshot of the trust frontier**. That frontier has two
layers:

- **Removable trust** — provable with more work (the research progress): cell-state
  invariants (`blocked_cell`), length preconditions, the mx25 `page_index < 16`, etc.
  Converting each shifts the curve up.
- **Floor trust** — stuck until Flux itself improves; un-trusting brings back a *hard error*:
  - `UnsolvedEvar` (infer.rs:427) / dyn-predicate `assert_eq` (infer.rs:1034) **ICEs**
  - `Index`/`IndexMut` `in_bounds` extern-spec gap (`blocked_flux_index_extern_spec`)
  - existential-tuple-in-`Result` sort rejection + the loop-carry limit (`blocked_flux_loop_carry`)
  - stream `SResult` offset invariant not tracked (`blocked_flux_stream_combinator`)

  PR #1610 is a lever that may **lower the floor**.

## Hard error vs E0999 vs cargo-masking (don't conflate)

- **Hard error** = Flux can't *complete* a crate's analysis (no metadata): an **ICE**, a
  **sort error** ("values of this type cannot be used as base sorted instances"), a **parse
  error** ("illegal binder"), a resolution failure. Makes the crate's *own* verdicts
  unreliable **and** masks downstream. **Currently: none live** (all ICEs are trusted-around).
- **`E0999`** = crate finishes, all fns processed, errors reported — **reliable**. But
  `cargo flux` exits non-zero, so cargo won't build a **downstream** crate (e.g. nrf52840
  behind nrf52). That's **build-ordering masking**, not a wall.

So the count is **gettable now**; "partial" = the trust frontier, which *is* the metric.

## The transitive count is not one integer

> **count = (reachable obligations within the checked + untrusted frontier) + (N trusted-boundary truncations, each enumerable via its `blocked_*` marker) + (Z dep-masked regions).**

That decomposition *is* the deliverable — it shows where the analysis bottoms out: proven /
failing / removable-trust / floor-trust / dep-masked.

## Step 0 — Flux-complete baseline via collect-then-traverse (no obligations lost)

1. Run flux **per crate** → record each crate's `E0999` set + emit its `-Femit-callgraph`
   (both emit despite non-zero exit).
2. To traverse *into* a downstream crate (nrf52840 behind nrf52), `#[trusted(reason=…)]` (or
   prove) the upstream errors so it exits 0 — **after** recording the upstream obligations.
3. Run flux in the downstream crate; record its obligations.
4. Stitch per-crate callgraphs; Σ each crate's real obligations.

Board crates for nrf52840dk: `kernel` (clean), `tock-cells`, `tickv`, `capsules-core`,
`capsules-extra`, `nrf52`, `nrf52840`, `nrf5x`, `cortex-m`, `cortex-v7m`, `arch/*`.

## #1610 audit — how to trust it (anchor on the binary)

- **Soundness (decisive):** `#1610-no-panic ∩ survey-panic-functions` must be **empty**. Any
  overlap = #1610 over-claims (bug, or it reasons about source the binary already
  proved/DCE'd — itself a finding).
- **Agreement:** #1610's "can panic / owes P" should line up with hand-derived obligations +
  `caller_closure_flux` verdicts. Overlap → trust accrues.
- **Disagreements = the deliverable.** Classify: inlining, monomorphization, `dyn` dispatch
  (`trait_dispatch_unresolved` edges the prototype already flags), or #1610 bugs.
- **First 10-min subtask:** is #1610's graph the *same* `-Femit-callgraph` the prototype
  consumes, or a *new* graph from `flux-opt`? Same flag → graph-vs-graph compare; new →
  that's the first agreement check.

## Worklist (tomorrow / fresh session)

1. **Reconcile the addr-drift first** (or the map will lie): markers' `addr=`/`covers=[]`
   join to the refreshed `panic_survey.json` at only **1/349**. Re-anchor the join key to
   `file:line + flavor` (or `tools/panic_accounting.py` source-operation census). This is the
   single highest-leverage cleanup — script it.
2. Collect per-crate `-Femit-callgraph` + `E0999` for all board crates (step 0).
3. Build the depth-layered cone: depth=1 = panic-bearing fns (survey effective_frame +
   `flux_support::assert` sites); walk up via the stitched graph; tag each leaf
   proven/failing/removable-trust/floor-trust/dep-masked.
4. Run #1610 on the crates; audit per the section above.
5. Render: per-panic obligation tree + the monotone "obligations vs trusted" curve with the
   capability floor.

## Current state snapshot (end of 2026-05-24 session)

- **capsules-extra: 23 live `E0999`** — tickv 6, ble_advertising_driver 5, framer 3, driver 3,
  mx25r6435f 3, sixlowpan_state 2, sixlowpan_compression 1. Classified: ~7 local depth=1
  asserts, ~5 direct index bounds, ~2 propagated buffer specs, **~8–9 transitive callee
  preconditions** (tickv→async_ops, sixlowpan_state→compress, framer→encode_ccm_nonce).
- **nrf52: 40 live `E0999`** — usbd 32, adc 6, chip 1, ble_radio 1. **No ICE.** nrf52840 masked
  behind it (cargo ordering).
- Trust/ignore added this session: `decode_key_id` (`blocked_flux_stream_combinator`), cell-y
  `.is_some()` ignored, mx25 `read_sector` + ICE fns, mx25 `Index` reverted to safe checked
  + `blocked_flux_index_extern_spec`. `udp_port_table` `get_unchecked` (DCE'd, deferred —
  see soundness caveat: safe-by-absence only, unsound source-wide).
- Flux limits documented this session (all in memory + sibling docs): tuple-pack-in-Result,
  loop-carry, Index extern-spec gap, stream combinator, `assume`=runtime-panic, addr-drift.
