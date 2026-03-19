#[flux_rs::extern_spec(core)]
impl bool {
    #[flux_rs::sig(fn(_, _) -> _)]
    #[flux_rs::no_panic_if(F::no_panic())]
    fn then<T, F: FnOnce() -> T>(self, f: F) -> Option<T>;
}
