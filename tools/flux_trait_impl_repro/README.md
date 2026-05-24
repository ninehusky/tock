# Minimal repro: Flux silently does not check `impl tickv::FlashController` method bodies

`lib.rs` is the body of a crate that depends on `flux-rs` and `tickv` (paths in the
Cargo below). Running `cargo flux` on it:

- the **inherent** method `M::inherent` → `check(false)` ERRORS (body is checked) ✓
- `<M as tickv::FlashController>::read_region` → `check(false)` is **SILENT** (body
  never analyzed) ✗   ← the bug

`check` is `#[flux_rs::sig(fn(b: bool[true]))]`, so `check(false)` is an error iff Flux
actually analyzes the enclosing body.

## What was ruled OUT (none reproduce it — all get checked)
free fn · inherent method · generic struct · local trait impl · external (core)
trait impl (`Default`) · const-generic trait · cross-crate const-generic trait ·
associated-type field (`F::Page`) · body content (minimal body still silent) ·
trusted sibling method · matching the exact signature shape · a synthetic
byte-identical `FlashController` copy (CHECKS fine).

## What DOES reproduce it
Implementing the **real `tickv::FlashController`** (this file). So the trigger is
something the *tickv crate* does to that trait — NOT the trait's shape. Likely
candidate to bisect next: tickv defines `#[flux_rs::refined_by] TicKV<C:
FlashController<S>>` and has flux `sig`s on methods that call `controller.read_region`
etc., which may give the trait method a derived spec so impls are compared-not-checked.

Cargo.toml:
    [dependencies]
    flux-rs = { path = "/Users/andrew/research/flux/lib/flux-rs" }
    tickv   = { path = "<tock>/libraries/tickv" }
    [package.metadata.flux]
    enabled = true

NOTE: build on a clean tree — an uncommitted `assert(false)` in tickv's async_ops.rs
makes tickv fail to flux-compile and masks everything downstream.
