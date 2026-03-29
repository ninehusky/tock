#![allow(unused)]
use core::ops;
use core::ops::{Index, IndexMut, Range, RangeFrom, RangeTo};
use core::slice::{self, Iter, SliceIndex};

#[flux_rs::extern_spec(core::slice)]
#[flux_rs::assoc(fn in_bounds(idx: Self, len: int) -> bool)]
trait SliceIndex<T: ?Sized> {}

#[flux_rs::extern_spec(core::slice)]
#[flux_rs::assoc(fn in_bounds(idx: int, len: int) -> bool { idx < len })]
impl<T> SliceIndex<[T]> for usize {}

#[flux_rs::extern_spec(core::slice)]
#[flux_rs::assoc(fn in_bounds(idx: Range<int>, len: int) -> bool { idx.start <= idx.end && idx.end <= len })]
impl<T> SliceIndex<[T]> for Range<usize> {}

#[flux_rs::extern_spec(core::slice)]
#[flux_rs::assoc(fn in_bounds(idx: RangeTo<int>, len: int) -> bool { idx.end <= len })]
impl<T> SliceIndex<[T]> for RangeTo<usize> {}

#[flux_rs::extern_spec(core::slice)]
#[flux_rs::assoc(fn in_bounds(idx: RangeFrom<int>, len: int) -> bool { idx.start <= len })]
impl<T> SliceIndex<[T]> for RangeFrom<usize> {}

#[flux_rs::extern_spec(core::slice)]
impl<T, I: SliceIndex<[T]>> ops::Index<I> for [T] {
    #[flux_rs::sig(fn(&[T][@n], I[@idx]) -> &I::Output)]
    #[flux_rs::no_panic_if(I::in_bounds(idx, n))]
    fn index(&self, index: I) -> &I::Output;
}

#[flux_rs::extern_spec(core::slice)]
impl<T, I: SliceIndex<[T]>> ops::IndexMut<I> for [T] {
    #[flux_rs::sig(fn(&mut [T][@n], I[@idx]) -> &mut I::Output)]
    #[flux_rs::no_panic_if(I::in_bounds(idx, n))]
    fn index_mut(&mut self, index: I) -> &mut I::Output;
}

#[flux_rs::extern_spec]
impl<T> [T] {
    #[flux_rs::sig(fn(&[T][@len]) -> usize[len])]
    fn len(v: &[T]) -> usize;

    #[flux_rs::sig(fn(&[T][@len]) -> Iter<T>[0, len])]
    fn iter(v: &[T]) -> Iter<'_, T>;

    #[flux_rs::no_panic]
    fn get<I>(&self, index: I) -> Option<&I::Output>
    where
        I: SliceIndex<[T]>;
}
