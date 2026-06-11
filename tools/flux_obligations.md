# `flux_obligations.py` — total Flux-obligation census

## What it answers

"If Flux checked **every** function in the board's crate chain whole-crate, what
is the complete list of proof obligations it would emit?" — a number that crate
masking normally hides, because a crate that emits any Flux error fails to
compile, so its dependents are never checked at all.

Output: `tools/flux_obligations.json` — one record per crate with every Flux
diagnostic (code / file / line / col / message / kind), the functions that had
to be temporarily trusted to dodge ICEs (blind spots), and a health verdict.

## How it works (and why it has to be this shape)

Crates are walked in **dependency order**. For each crate:

1. **Measure** — drop `[package.metadata.flux] include` (→ whole-crate), run
   `cargo flux -p <crate>` against already-temp-disabled (clean) dependencies so
   nothing upstream masks it, and collect every own-file diagnostic.
2. **Disable** — temp-set `default_trusted = true` so the crate compiles clean
   as a dependency for everything downstream (sigs/types still export; fn bodies
   are assumed, so no obligations leak into dependents' runs).

All temp edits are reverted via `git checkout` at the end — the tool never
leaves the tree mutated.

### Measuring against trusted-clean deps is mandatory

Earlier attempts unmasked deps by *scope-to-nothing* (`include=["def:__none__"]`)
or `enabled=false`. Both are wrong:
- `enabled=false` breaks any **flux-annotated** crate (`#[flux_rs::spec]` expands
  to `flux_tool::…` which needs the driver active → `E0433`). Only safe for
  spec-less crates.
- *scope-to-nothing* strips the dep's specs, so flux-infer can't reason through
  it and emits **spurious refinement errors AND false ICEs** in the consumer.
  (Observed: nrf52 reported "74 obligations + 2 ICEs" against a scope-none nrf5x;
  against a properly trusted nrf5x its real count is **0**.)

`default_trusted` keeps the dep's sigs, so consumers see the true picture.

## ICEs: what `trusted` can and cannot dodge

Flux processes a fn in two phases; the ICEs split across them:

| phase | `trusted` skips it? | ICE signatures here |
|---|---|---|
| **body-checking** (inference over the body) | **yes** | `infer:1033` (Iterator-item refinement), `projections:382` (impossible case), `fold_unfold:512` (const-generic array in `grant.enter`), `place_ty:496` (`MaybeUninit<[u8;N]>` deref) |
| **sig-elaboration** (build the type signature; callers depend on it) | **no** | `infer:426` `UnsolvedEvar` on `&dyn Fn`/lifetime-parameterized trait-method returns (`map_modulus`, `ListNode::next`) |

The tool **trusts** the body-checking ICE fns (records them as blind spots) so
the rest of the crate is measured. A **sig-elaboration** ICE cannot be dodged —
`trusted` still elaborates the sig, and `#[flux_rs::ignore]` doesn't help either
(it can't sit on a file-backed `mod`, and the elaboration fires transitively
when any consumer touches the type). Such a crate is marked
`health: "elaboration_ice"` and the tool falls back to its curated **scoped**
`include` (which avoids the offending item and completes) for a partial list.

## Result (nrf52840dk chain, current driver — see flux_obligations.json)

- **~151 obligations**, dominated by `refinement_type` and `oob_index`;
  concentrated in **nrf5x (55)**, **capsules-extra (≈80, scoped fallback)**,
  **capsules-core (≈14)**. tock-cells / kernel / cortex-m / cortex-v7m / nrf52 /
  nrf52840 / flux_support are 0–1.
- **Blind spots:** ~15 functions in capsules-core trusted to dodge body ICEs;
  capsules-extra whole-crate blocked by the `UnsolvedEvar` elaboration ICE.
- **Board (nrf52840dk):** 0 own refinement obligations (pure component wiring +
  panic handler). Requires commenting the redundant `#[no_mangle]` on the
  `#[panic_handler]` fn (rustc rejects `#[no_mangle]` on a lang item).

### Known measurement artifacts in the JSON

- **`xcrate_resolution` obligations** are not real proof obligations — they are
  "use of `X::y` not included when checking external crate". A handful (currently
  ~8, all `cortexm4::…` in nrf52) come from `arch/cortex-m4`, which has **no**
  `[package.metadata.flux]` and merely **re-exports** `cortexm`; Flux can't
  resolve the re-export path even though `cortexm` is in the chain. Filter the
  `xcrate_resolution` kind to get only true obligations.
- A crate with `health: "ice_no_loc"` or `"elaboration_ice"` is reported from its
  **scoped** include (partial), not whole-crate — its count is a floor.
- **Crates downstream of cortex-m are unreliable (currently nrf52840, board = 0,
  masked).** cortex-m hits the `rty:813` ICE, forcing the scoped fallback; scoped
  cortex-m under-exports, so nrf52's `cortexm4::…` re-export uses go unresolved →
  nrf52 fails to compile → its dependents are masked. cortex-m whole-crate would
  export fully but ICEs (export-vs-ICE tension, same as capsules-extra). In a run
  where cortex-m happened to export fully, nrf52840 measured **≈29** real
  obligations (refinement + OOB in ieee802154_radio/usbd). Treat the
  reliable census as **tock-cells … capsules-extra**; nrf52840/board need the
  cortex-m `rty:813` fix (or cortexm4 to carry flux metadata) to measure.

## Known issue (filed separately — `flux_ice_catalog.md`)

The `infer:426` `UnsolvedEvar` sig-elaboration ICE is the highest-value fix: it
gates the most coverage (all of capsules-extra whole-crate + the full board
check) and is the one ICE class no source-level workaround silences.
