#[flux_rs::extern_spec(core::cmp)]
#[flux_rs::assoc(fn min_no_panic() -> bool)]
trait Ord {
    #[flux_rs::sig(fn (Self, other: Self) -> Self)]
    #[flux_rs::no_panic_if(Self::min_no_panic())]
    fn min(self, other: Self) -> Self
    where
        Self: Sized;
}
