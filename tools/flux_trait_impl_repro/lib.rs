// MINIMAL REPRO: a method implementing tickv::FlashController is NOT checked by Flux,
// while an inherent method on the same struct IS. `check(false)` errors iff the body
// is actually analyzed.
#![allow(dead_code)]
use tickv::flash_controller::FlashController;
use tickv::error_codes::ErrorCode;

#[flux_rs::sig(fn(b: bool[true]))]
fn check(_b: bool) {}

pub struct M;

impl M {
    fn inherent(&self) { check(false); }            // CONTROL: errors (checked)
}

impl<const S: usize> FlashController<S> for M {
    fn read_region(&self, _rn: usize, _b: &mut [u8; S]) -> Result<(), ErrorCode> {
        check(false);                                // BUG: silent (body NOT checked)
        Err(ErrorCode::ReadFail)
    }
    fn write(&self, _a: usize, _b: &[u8]) -> Result<(), ErrorCode> { Ok(()) }
    fn erase_region(&self, _rn: usize) -> Result<(), ErrorCode> { Ok(()) }
}
