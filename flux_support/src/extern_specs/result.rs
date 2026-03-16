use core::marker::Destruct;

#[flux_rs::extern_spec]
#[flux_rs::refined_by(b: bool)]
enum Result<T, E> {
    #[variant({T} -> Result<T, E>[true])]
    Ok(T),
    #[variant({E} -> Result<T, E>[false])]
    Err(E),
}

#[flux_rs::extern_spec]
impl<T, E> Result<T, E> {
    #[sig(fn(&Result<T,E>[@b]) -> bool[b])]
    const fn is_ok(&self) -> bool;

    #[sig(fn(&Result<T,E>[@b]) -> bool[!b])]
    const fn is_err(&self) -> bool;

    #[sig(fn(Self) -> T)]
    #[flux_rs::no_panic_if(T::default_no_panic())]
    fn unwrap_or_default(self) -> T
    where
        T: Default;

    #[sig(fn(Self, F) -> T)]
    #[flux_rs::no_panic_if(F::no_panic())]
    const fn unwrap_or_else<F>(self, op: F) -> T
    where
        F: [const] FnOnce(E) -> T + [const] Destruct;

    #[sig(fn(Self, U, F) -> _)]
    #[flux_rs::no_panic_if(F::no_panic())]
    const fn map_or<U, F>(self, default: U, f: F) -> U
    where
        F: [const] FnOnce(T) -> U + [const] Destruct,
        T: [const] Destruct,
        E: [const] Destruct,
        U: [const] Destruct;

    #[sig(fn(Self, F) -> _)]
    #[flux_rs::no_panic_if(F::no_panic())]
    const fn and_then<U, F>(self, op: F) -> Result<U, E>
    where
        F: [const] FnOnce(T) -> Result<U, E> + [const] Destruct;


    #[sig(fn(Self, F) -> _)]
    #[flux_rs::no_panic_if(F::no_panic())]
    const fn map<U, F>(self, op: F) -> Result<U, E>
    where
        F: [const] FnOnce(T) -> U + [const] Destruct;

    #[sig(fn(Self, D, F) -> _)]
    #[flux_rs::no_panic_if(D::no_panic() && F::no_panic())]
    const fn map_or_else<U, D, F>(self, default: D, f: F) -> U
    where
        D: [const] FnOnce(E) -> U + [const] Destruct,
        F: [const] FnOnce(T) -> U + [const] Destruct;

    #[sig(fn(Self, O) -> _)]
    #[flux_rs::no_panic_if(O::no_panic())]
    const fn map_err<F, O>(self, op: O) -> Result<T, F>
    where
        O: [const] FnOnce(E) -> F + [const] Destruct;
}
