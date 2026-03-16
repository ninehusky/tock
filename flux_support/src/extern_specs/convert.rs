#[flux_rs::extern_spec(core::convert)]
#[flux_rs::assoc(fn from_no_panic() -> bool)]
trait From<T> {
    #[flux_rs::sig(fn(value: T) -> Self)]
    #[flux_rs::no_panic_if(Self::from_no_panic())]
    fn from(value: T) -> Self
    where
        Self: Sized;
}

#[flux_rs::extern_spec(core::convert)]
#[flux_rs::assoc(fn into_no_panic() -> bool)]
trait Into<T> {
    #[flux_rs::sig(fn(Self) -> T)]
    #[flux_rs::no_panic_if(Self::into_no_panic())]
    fn into(self) -> T
    where
        Self: Sized;
}

#[flux_rs::extern_spec(core::convert)]
#[flux_rs::assoc(fn into_no_panic() -> bool { <U as From<T>>::from_no_panic() })]
impl<T, U: From<T>> Into<U> for T {
    #[flux_rs::sig(fn(T) -> U)]
    #[flux_rs::no_panic_if(<U as From<T>>::from_no_panic())]
    fn into(self) -> U;
}

// Infallible widening numeric conversions:
#[flux_rs::extern_spec]
#[flux_rs::assoc(fn from_no_panic() -> bool { true })]
impl From<u8> for usize {}
