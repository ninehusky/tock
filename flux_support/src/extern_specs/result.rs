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

    
}
