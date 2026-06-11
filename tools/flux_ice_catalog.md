# Flux ICE/panic catalog — nrf52840dk whole-crate census

Six distinct Flux-driver ICEs/panics surfaced when enabling whole-crate checking
across the nrf52840dk board chain (driver dated ~Jun 2026). They are the real
blockers to "Flux on everything." Classified by the phase they crash in, which
determines whether `#[flux_rs::trusted]` can work around them.

## Body-checking ICEs — dodge-able by trusting the enclosing fn

These crash during inference over a function body, so marking the fn
`#[flux_rs::trusted]` (skip the body) avoids them. `flux_obligations.py` does
this automatically and records the trusted fns as blind spots.

| # | signature | trigger | sites |
|---|---|---|---|
| 1 | `flux-infer/src/infer.rs:1033` `assertion left == right` | Iterator-item refinement mismatch (`u32[b0]` vs `{u32[b0] \| *}`) iterating a refined slice inside a `.enter(\|...\|)` / `grant.enter` closure | capsules-core ×12 (rng, virtual_flash, virtual_rng), nrf52 (nvmc) |
| 2 | `flux-infer/src/projections.rs:382` "impossible case reached" | `.enter(\|...\|)` alarm callbacks | capsules-core ×2 (virtual_alarm, virtual_timer `alarm`) |
| 3 | `flux-middle/.../fold_unfold.rs:512` "invalid downcast" | const-generic array `[Option<T>; N]` mutated inside `grant.enter` | capsules-core ×1 (low_level_debug `push_entry`) |
| 4 | `flux-refineck/src/type_env/place_ty.rs:496` "invalid deref" | deref of a `MaybeUninit<[u8; N]>` static buffer in board component init | boards/nordic/nrf52840dk `lib.rs:415` |
| 6 | `flux-middle/src/rty/mod.rs:813` "caller should guarantee existence of associated refinement" | associated-refinement elaboration; ICE has **no in-crate source loc** so it can't be dodged by trusting a local fn — `flux_obligations.py` falls back to the crate's scoped include | arch/cortex-m (`cortexm`, whole-crate) |

## Sig-elaboration ICE — NOT dodge-able (highest priority)

This crashes while building a function's *type signature*, before/independent of
body checking. `#[flux_rs::trusted]` still elaborates the sig, so it does not
help; `#[flux_rs::ignore]` also fails (it can't be placed on a file-backed
`mod`, and the elaboration fires transitively whenever a consumer touches the
type — e.g. `List::iter` drags in `ListNode::next`). The only escape is
`default_ignore` on the whole crate, which then stops it exporting refinements.

| # | signature | trigger | sites |
|---|---|---|---|
| 5 | `flux-infer/src/infer.rs:426` `UnsolvedEvar` (panic, `Box<dyn Any>`) | sig-elaboration of a trait method taking/returning a higher-ranked `&dyn Fn(&[u8])` or a lifetime-parameterized reference (`&'a ListLink<…>`) | capsules-extra: `public_key_crypto::rsa_keys` `RsaKey/RsaKeyMut::map_modulus`, `virtual_kv` `ListNode::next`, and ≥8 body sites in `test/*` |

### Minimal repro sketch (#5)
```rust
// trait with a &dyn Fn method (the elaboration trigger)
trait RsaKey { fn map_modulus(&self, closure: &dyn Fn(&[u8])) -> Option<()>; }
struct K;
impl RsaKey for K {
    fn map_modulus(&self, closure: &dyn Fn(&[u8])) -> Option<()> { closure(&[]); Some(()) }
}
// Checking any crate that elaborates this impl panics at
// flux-infer/src/infer.rs:426: called `Result::unwrap()` on `Err(UnsolvedEvar(...))`
// even with the impl/fn marked #[flux_rs::trusted].
```

**Impact:** #5 gates the most coverage — all of capsules-extra whole-crate plus
the full board check. It is the recommended first fix.

## Non-Flux blocker (board binary)

`#[no_mangle]` on the `#[panic_handler]` fn (`nrf52840dk/src/io.rs`) → rustc
"`#[no_mangle]` cannot be used on internal language items". The `#[no_mangle]`
is redundant with `#[panic_handler]`; commenting it out is safe for verification
(no firmware is linked). Not a Flux issue.
