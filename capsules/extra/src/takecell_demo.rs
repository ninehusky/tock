//! TakeCell refinement demo — exactly what Flux can and cannot prove about the
//! value INSIDE the real `kernel::utilities::cells::TakeCell`.
//!
//! MECHANISM ("Design C"): `TakeCell` is transparent to Flux
//! (`val: Cell<Option<&'a mut T>>`, no `#[opaque]`, no flux attrs). So refining
//! the TYPE ARGUMENT in a `#[flux_rs::field(...)]` annotation makes the contained
//! value carry an invariant, and that invariant threads through the GENERIC
//! `take()` / `map_or()` bodies to the reader. NO changes to tock-cells.
//!
//! Every fn is labelled with its EXPECTED Flux outcome:
//!   SILENT = proves (no error)   |   E0999 = refinement error (boundary / gate)
//!
//! This file is in the flux `include` list purely as living documentation. The
//! `*_bad` / `*_unwrap` fns are DELIBERATE E0999s (soundness gates + the known
//! blockers); they are what makes the demo trustworthy, not regressions.

#![allow(dead_code)]

use flux_support::assert;
use kernel::utilities::cells::TakeCell;

// ============================================================================
// A. Scalar: `TakeCell<i32>` whose contained value is always > 0.
//    ("TakeCell<i32> => take().unwrap() is > 0", your mental model.)
// ============================================================================

#[flux_rs::refined_by()]
struct NumDemo {
    #[flux_rs::field(TakeCell<i32{v: v > 0}>)]
    tc: TakeCell<'static, i32>,
}

impl NumDemo {
    // CONSTRUCTION: build from a provably-positive value. SILENT.
    #[flux_rs::sig(fn(v: &mut i32{v: v > 0}) -> NumDemo)]
    fn new(v: &'static mut i32) -> NumDemo {
        NumDemo { tc: TakeCell::new(v) }
    }

    // CONSTRUCTION soundness gate: build from an UNCONSTRAINED value. E0999.
    #[flux_rs::sig(fn(v: &mut i32) -> NumDemo)]
    fn new_bad(v: &'static mut i32) -> NumDemo {
        NumDemo { tc: TakeCell::new(v) }
    }

    // READER: the `> 0` invariant threads through `take()`. Presence handled by
    // `if let` (so no presence obligation here — see `read_unwrap` for that).
    fn read(&self) {
        if let Some(v) = self.tc.take() {
            assert(*v > 0); // SILENT: invariant carried across take()
            assert(*v > 3); // E0999: `> 3` is NOT implied by `> 0` (the boundary)
            self.tc.replace(v); // SILENT: putting back a value we know is > 0
        }
    }

    // PUT-BACK soundness gate: replace with an unconstrained value. E0999.
    #[flux_rs::sig(fn(&NumDemo, other: &mut i32))]
    fn put_bad(&self, other: &'static mut i32) {
        self.tc.replace(other);
    }

    // PUT-BACK ok: replace with a positive value. SILENT.
    #[flux_rs::sig(fn(&NumDemo, other: &mut i32{v: v > 0})) ]
    fn put_ok(&self, other: &'static mut i32) {
        self.tc.replace(other);
    }

    // PRESENCE: the thing we explicitly CANNOT prove (the cell-presence wall, the
    // shared blocker behind asserts #1/2/4/6/9/10/12/...). Note the asymmetry:
    //
    //  - A bare `.unwrap()` is SILENT — `Option::unwrap` has NO Flux extern spec
    //    (only `is_some`/`is_none` do), so Flux generates NO presence obligation
    //    for it. Flux does NOT flag unwrap panics here. The VALUE invariant `> 0`
    //    still survives the unwrap unrefined-ly (the `assert(*v > 0)` is silent).
    fn read_unwrap_silent(&self) {
        let v = self.tc.take().unwrap(); // SILENT: unwrap carries no presence check
        assert(*v > 0); // SILENT: value invariant `> 0` survives take()/unwrap()
    }

    //  - The EXPLICIT `assert(self.tc.is_some())` form (what the real capsules use
    //    as a panic marker) DOES fire: the field carries no PRESENCE refinement
    //    (we refined the value, not whether the cell is full), so Flux cannot prove
    //    `is_some`. This is the genuine cell-presence boundary.
    fn read_presence_gate(&self) {
        assert(self.tc.is_some()); // E0999: cannot prove presence (the cell wall)
    }
}

// ============================================================================
// B. Slice: `TakeCell<[u8]>` with a min-length invariant. The real #8 shape.
// ============================================================================

#[flux_rs::refined_by()]
struct BufDemo {
    #[flux_rs::field(TakeCell<[u8]{n: n > 0}>)]
    tc: TakeCell<'static, [u8]>,
}

impl BufDemo {
    // CONSTRUCTION: build from a provably-non-empty buffer. SILENT.
    #[flux_rs::sig(fn(b: &mut [u8]{n: n > 0}) -> BufDemo)]
    fn new(b: &'static mut [u8]) -> BufDemo {
        BufDemo { tc: TakeCell::new(b) }
    }

    // The #8 payoff: take via map_or, then index [0] with NO assert needed.
    fn read(&self) {
        self.tc.map_or((), |buf| {
            assert(buf.len() > 0); // SILENT: len > 0 carried across take()
            buf[0] = 0xAB; // SILENT: in-bounds directly from the invariant
        });
    }

    // BOUNDARY: a stronger length claim is not implied. E0999.
    fn read_too_strong(&self) {
        self.tc.map_or((), |buf| {
            assert(buf.len() > 3); // E0999: `> 3` not implied by `> 0`
        });
    }

    // PUT-BACK soundness gate: replace with an unconstrained buffer. E0999.
    // (This is exactly the mx25 `:283` residual: a buffer of unknown length
    //  returned from the SPI HIL cannot be put back into the refined cell.)
    #[flux_rs::sig(fn(&BufDemo, other: &mut [u8]))]
    fn put_bad(&self, other: &'static mut [u8]) {
        self.tc.replace(other);
    }

    // PUT-BACK ok: replace with a non-empty buffer. SILENT.
    #[flux_rs::sig(fn(&BufDemo, other: &mut [u8]{n: n > 0}))]
    fn put_ok(&self, other: &'static mut [u8]) {
        self.tc.replace(other);
    }
}
