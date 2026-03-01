use core::cell::Cell;

// #[flux_rs::extern_spec(core::cell)]
// struct Cell<T> {
//     value: T,
// }

#[flux_rs::extern_spec(core::cell)]
impl<T: Default> Cell<T> {
    #[sig(fn(&Self) -> T)]
    #[flux_rs::no_panic_if(T::default_no_panic())]
    fn take(&self) -> T;
}
