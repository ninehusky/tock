#[flux_rs::extern_spec(core::cmp)]
#[flux_rs::sig(fn (T, T) -> T)]
#[flux_rs::no_panic_if(<T as Ord>::min_no_panic())]
fn min<T: Ord>(v1: T, v2: T) -> T;

#[flux_rs::extern_spec(core::cmp)]
#[flux_rs::assoc(fn min_no_panic() -> bool { true })]
impl Ord for usize {}
