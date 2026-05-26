# Flux refinement-tuple / loop-carry limitations (sixlowpan next-header `_` arm)

**Status:** open. Blocks the next-header `_ => unreachable` arm in
`capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs` (`decompress`, `addr=0xd0f0`),
tagged `FLUX-TODO-BLOCKED ... blocked_flux_loop_carry`. Minimal repro:
`/Users/andrew/research/flux_tuple_pack_repro` (commit `bc5e545`). Flux fork HEAD `edacb71`,
`cargo-flux 6bf0767273 (2026-03-19)`.

> **Two corrections vs earlier drafts of this doc** (both from the minimal repro):
> 1. The bare existential-tuple return *does* pack — the original "can't pack tuples" claim was
>    wrong.
> 2. The struct-extraction loop-carry failure I hit in tock did **not** reproduce minimally;
>    the minimal model shows struct field-extraction *preserves* the invariant. So the tock
>    blocker's exact cause is **undiagnosed** (see "Open" below) — do not file it as
>    "struct extraction drops the invariant."

## What reproduces minimally (filable)

### Issue 1 — an existential-over-tuple cannot be a `Result`/enum type argument
```rust
// REJECTED at sort level (not merely unprovable):
#[flux_rs::sig(fn(c: bool[@b], x: i32{v: b => v > 0}) -> Result<{r. (bool[r], i32{v: r => v > 0})}, ()>)]
//   error[E0999]: values of this type cannot be used as base sorted instances
```
Controls that **pass**: struct-in-`Result` (`#[refined_by]/#[invariant]`), and a *non-existential*
per-component refined tuple `Result<(bool, i32{v: v>0}), ()>`. So refined tuples live inside
`Result` fine; only the **related (existential)** form is unrepresentable there. Nearby phrasings:
existential *outside* the `Result` → `parameter r cannot be determined`; `#` binder inside the
`Result` tuple → `illegal binder` ("`#` binder not allowed in this position").

### Issue 2 — `@`/`#` binder asymmetry inside a return-tuple component
`(bool[@b], ..)` in a return → `illegal binder` (`@` not allowed); `(bool[#b], ..)` is accepted.
Minor but real.

### Issue 3 — relation lost when an existential tuple is destructured into split `mut` locals across a loop
With a crate-global `qualifier QInv(c,x){ c => (x==0||x==1||x==2) }` and a
`while c { match x { 0|1|2 => …, _ => assert(false) } }` loop:
- single aggregate `mut` tuple local (use `.0`/`.1`) → **PASS**
- **struct**, fields extracted to two `mut` locals → **PASS**
- **existential tuple destructured** to two `mut` locals → **FAIL** (`a precondition cannot be
  proved` at the `_` arm — relation lost at the loop boundary).

The load-bearing distinction is `let (mut c, mut x) = …` (split) vs `let mut p = …; p.0/p.1`
(aggregate). A no-loop control confirms the producer's existential *does* pack out, so the loss is
purely at the loop boundary.

## Open: the actual tock blocker is not isolated

In tock, the struct return (`ExtHdrDecoded` with a self-contained refined `next_header` field,
extracted into the pre-existing `mut is_nhc` / `mut next_header`) made `decompress_ext_hdr` prove
but the `decompress` loop still would not carry `is_nhc => valid_ip6_nh(next_header)` (a probe
`assert` at the top of the loop body fails on the first iteration). The minimal repro's Issue-3
struct case is the *closest* analogue and it **passes** — so something tock-specific is in play
that I did not isolate. Leading suspect: unlike the minimal model (which enters the loop with a
struct-typed value), tock enters with `next_header` whose type was flattened to plain `u8` by the
`if is_nhc { next_header = nhc_to_ip6_nh(..)? }` **join** *before* the loop, so the loop-entry
state can't establish the qualifier invariant. This is a hypothesis, not confirmed.

## Consequence

The existential-tuple return (Issue 1) is unrepresentable in `Result`, so that route is dead. The
struct return proves at the producer but the consuming loop didn't carry the relation in tock for
an un-isolated reason. The arm is left open and documented. No `flux_support::assume` was used
(it lowers to a runtime panic). If revisited, the thing to test is making the **loop-entry**
`next_header` carry the refined type (avoid the pre-loop join flattening it), per the suspect above.
