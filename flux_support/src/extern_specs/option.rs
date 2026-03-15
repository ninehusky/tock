use core::marker::Destruct;

#[flux_rs::extern_spec]
#[flux_rs::refined_by(b: bool)]
enum Option<T> {
    #[variant(Option<T>[false])]
    None,
    #[variant({T} -> Option<T>[true])]
    Some(T),
}

#[flux_rs::extern_spec]
impl<T> Option<T> {
    #[sig(fn(&Option<T>[@b]) -> bool[b])]
    const fn is_some(&self) -> bool;

    #[sig(fn(&Option<T>[@b]) -> bool[!b])]
    const fn is_none(&self) -> bool;

    #[sig(fn(Self) -> T)]
    #[flux_rs::no_panic_if(T::default_no_panic())]
    fn unwrap_or_default(self) -> T
    where
        T: Default;


    #[sig(fn(Self, F) -> _)]
    #[flux_rs::no_panic_if(F::no_panic())]
    const fn map<U, F>(self, f: F) -> Option<U>
    where
        F: [const] FnOnce(T) -> U + [const] Destruct;

    #[sig(fn(Self, U, F) -> _)]
    #[flux_rs::no_panic_if(F::no_panic())]
    const fn map_or<U, F>(self, default: U, f: F) -> U
    where
        F: [const] FnOnce(T) -> U + [const] Destruct,
        U: [const] Destruct;

    #[sig(fn(Self, F) -> Self)]
    #[flux_rs::no_panic_if(F::no_panic())]
    const fn inspect<F>(self, f: F) -> Self
    where
        F: [const] FnOnce(&T) + [const] Destruct;

}
