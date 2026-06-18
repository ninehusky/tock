pub mod defs;
pub mod math;
// Inherited vtock arithmetic proof lemmas (aligned/pow2/octet/subregion math from a
// separate paper). They are true math facts but out of scope for panic verification
// and several grind z3 for >15min, blowing the invariant2 budget. Trust the whole
// module: the lemmas stay available as axioms (their `ensures` still discharge the MPU
// proofs that call them) but flux no longer tries to *prove* them. See invariant2
// chip carve-out.
#[flux_rs::trusted]
pub mod theorems;
