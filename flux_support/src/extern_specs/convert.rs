// Taken directly from `flux-core`: https://github.com/flux-rs/flux/blob/main/lib/flux-core/src/convert/mod.rs

#[flux_rs::extern_spec(core::convert)]
#[flux_rs::assoc(fn succeeds(s: Self, out: Result) -> bool { true })]
#[flux_rs::assoc(fn into_val(s: Self, into: T) -> bool { true })]
trait TryInto<T>: Sized {
    #[flux_rs::sig(fn(Self[@s]) -> Result<T{v: Self::into_val(s, v)}, Self::Error>{out: Self::succeeds(s, out)})]
    fn try_into(self) -> Result<T, Self::Error>;
}

#[flux_rs::extern_spec(core::convert)]
#[flux_rs::assoc(fn succeeds(s: T, out: Result) -> bool { true })]
#[flux_rs::assoc(fn from_val(s: T, into: Self) -> bool { true })]
trait TryFrom<T>: Sized {
    #[flux_rs::sig(fn(T[@s]) -> Result<Self{v: Self::from_val(s, v)}, Self::Error>{out: Self::succeeds(s, out)})]
    fn try_from(value: T) -> Result<Self, Self::Error>;
}

#[flux_rs::extern_spec(core::convert)]
#[flux_rs::assoc(fn succeeds(s: T, out: Result) -> bool { <U as TryFrom<T>>::succeeds(s, out) })]
#[flux_rs::assoc(fn into_val(s: T, into: U) -> bool { <U as TryFrom<T>>::from_val(s, into) })]
impl<T, U: TryFrom<T>> TryInto<U> for T {
    #[flux_rs::sig(fn(T[@s]) -> Result<U{v: <U as TryFrom<T>>::from_val(s, v)}, U::Error>{out: <U as TryFrom<T>>::succeeds(s, out)})]
    fn try_into(self) -> Result<U, U::Error>;
}
