// alignment of data types must be at least 0:
// https://doc.rust-lang.org/reference/type-layout.html
#[flux_rs::extern_spec(core::mem)]
#[flux_rs::sig(fn<T>() -> usize{align: align > 0})]
fn align_of<T>() -> usize;

// TODO: a `size_of` extern spec mirroring flux-core
// (`#[flux_rs::sig(fn() -> usize[T::size_of()])]`) ICEs here —
// `crates/flux-middle/src/queries.rs:890` "assoc refinement on extern crate is
// not builtin". The `T::size_of()` builtin assoc reft only resolves inside
// flux-core's own specs, not a user-crate extern_spec. Needs a flux-side fix.
