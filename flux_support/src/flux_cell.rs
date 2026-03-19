use core::cell::Cell;

pub struct FluxCell<T: ?Sized> {
    inner: Cell<T>,
}
