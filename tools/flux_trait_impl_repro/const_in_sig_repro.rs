// Minimal repro #2: a #[flux_rs::sig] that references a Rust `const` in its
// refinement makes Flux SILENTLY SKIP the fn body (no check, no error).
// Single crate; depends only on flux-rs. `check(false)` errors iff the body is analyzed.
//
//   cargo flux  =>  c() errors (checked);  d() does NOT (body skipped).  <-- bug
//
// This explains the "SIG_EDGE" vacuous bucket: e.g. capsules/extra framer.rs
// `requires n >= radio::PSDU_OFFSET + LQI_SIZE` silently disables checking of
// incoming_frame_security. A spec meant to ADD an obligation instead removes one.
#![allow(dead_code)]
#[flux_rs::sig(fn(b: bool[true]))]
fn check(_b: bool) {}

const K: usize = 4;

#[flux_rs::sig(fn(n: usize) requires n >= 4)]   // literal -> body CHECKED
fn c(_n: usize) { check(false); }               // ERRORS (good)

#[flux_rs::sig(fn(n: usize) requires n >= K)]   // const  -> body SILENTLY SKIPPED
fn d(_n: usize) { check(false); }               // no error (BUG)
