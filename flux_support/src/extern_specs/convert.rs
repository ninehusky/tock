#[flux_rs::extern_spec(core::convert)]
#[flux_rs::assoc(fn from_no_panic() -> bool)]
trait From<T> {
    #[flux_rs::sig(fn(value: T) -> Self)]
    #[flux_rs::no_panic_if(Self::from_no_panic())]
    fn from(value: T) -> Self
    where
        Self: Sized;
}
