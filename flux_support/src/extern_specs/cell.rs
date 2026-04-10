use core::cell::Cell;

#[flux_rs::extern_spec(core::cell)]
#[flux_rs::refined_by(value: T)]
struct UnsafeCell<T: ?Sized> {
    #[flux_rs::field(T[value])]
    value: T,
}

#[flux_rs::extern_spec(core::cell)]
#[flux_rs::refined_by(value: T)]
struct Cell<T: ?Sized> {
    #[flux_rs::field(UnsafeCell<T>[value])]
    value: UnsafeCell<T>,
}

#[flux_rs::extern_spec(core::cell)]
impl<T: Default> Cell<T> {
    #[sig(fn(&Self) -> T)]
    #[flux_rs::no_panic_if(T::default_no_panic())]
    fn take(&self) -> T;
}

#[flux_rs::extern_spec(core::cell)]
impl<T: Copy> Cell<T> {
    #[flux_rs::no_panic]
    #[flux_rs::sig(fn(&Self[@value]) -> T[value])]
    fn get(&self) -> T;
}
