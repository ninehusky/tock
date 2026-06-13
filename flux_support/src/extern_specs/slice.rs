#![allow(unused)]
use core::ops::{Index, IndexMut, Range, RangeFrom, RangeTo};
use core::slice::{self, Iter, SliceIndex};

#[flux_rs::extern_spec(core::ops)]
#[flux_rs::refined_by(end: Idx)]
struct RangeTo<Idx> {
    #[field(Idx[end])]
    end: Idx,
}

#[flux_rs::extern_spec(core::ops)]
#[flux_rs::refined_by(start: Idx)]
struct RangeFrom<Idx> {
    #[field(Idx[start])]
    start: Idx,
}

#[flux_rs::extern_spec(core::slice)]
// TODO! Remove the `{ true }` default once every `SliceIndex` impl in leasable_buffer has been
// given an explicit one.
#[flux_rs::assoc(fn in_bounds(idx: Self, v: T) -> bool { true })]
#[flux_rs::assoc(fn output_pred(idx: Self, v: T, out: Self::Output) -> bool)]
trait SliceIndex<T>
where
    T: ?Sized,
{
}

#[flux_rs::extern_spec(core::slice)]
#[flux_rs::assoc(fn in_bounds(idx: int, len: int) -> bool { idx < len })]
// We don't care about the output predicate here.
#[flux_rs::assoc(fn output_pred(idx: int, len: int, out: T) -> bool { true })]
impl<T> SliceIndex<[T]> for usize {}

#[flux_rs::extern_spec(core::slice)]
#[flux_rs::assoc(fn in_bounds(r: Range<int>, len: int) -> bool {
    r.start <= r.end && r.end <= len
})]
#[flux_rs::assoc(fn output_pred(r: Range<int>, len: int, out: int) -> bool {
    out == r.end - r.start
})]
impl<T> SliceIndex<[T]> for Range<usize> {}

#[flux_rs::extern_spec(core::slice)]
#[flux_rs::assoc(fn in_bounds(r: RangeTo<int>, len: int) -> bool { r.end <= len })]
#[flux_rs::assoc(fn output_pred(r: RangeTo<int>, len: int, out: int) -> bool {
    out == r.end
})]
impl<T> SliceIndex<[T]> for RangeTo<usize> {}

#[flux_rs::extern_spec(core::slice)]
#[flux_rs::assoc(fn in_bounds(r: RangeFrom<int>, len: int) -> bool { r.start <= len })]
#[flux_rs::assoc(fn output_pred(r: RangeFrom<int>, len: int, out: int) -> bool {
    out == len - r.start
})]
impl<T> SliceIndex<[T]> for RangeFrom<usize> {}

// Canonical form (flux-core ops/index.rs): the index precondition rides on the
// trait method's arg refinement (`{Idx | <Self as Index>::in_bounds(v, idx)}`),
// so impl bodies inherit it. `{ true }` default lets impls that don't override
// compile; no `?Sized` (that bound forced `unsized_fn_params`).
#[flux_rs::extern_spec(core::ops)]
#[flux_rs::assoc(fn in_bounds(v: Self, idx: Idx) -> bool)]
#[flux_rs::assoc(fn output_pred(v: Self, idx: Idx, out: Self::Output) -> bool { true })]
trait Index<Idx> {
    #[flux_rs::sig(fn(self: &Self[@v], index: Idx { <Self as Index<Idx>>::in_bounds(v, index) }) -> &Self::Output{out: <Self as Index<Idx>>::output_pred(v, index, out)})]
    fn index(&self, index: Idx) -> &Self::Output;
}

#[flux_rs::extern_spec(core::slice)]
#[flux_rs::assoc(fn in_bounds(len: int, idx: I) -> bool { <I as SliceIndex<[T]>>::in_bounds(idx, len) })]
#[flux_rs::assoc(fn output_pred(len: int, idx: I, out: <I as SliceIndex<[T]>>::Output) -> bool { <I as SliceIndex<[T]>>::output_pred(idx, len, out) })]
impl<T, I: SliceIndex<[T]>> Index<I> for [T] {
    #[flux_rs::no_panic]
    #[flux_rs::sig(fn(&Self[@len], {I[@idx] | <Self as Index<I>>::in_bounds(len, idx)}) -> &I::Output{out: <I as SliceIndex<[T]>>::output_pred(idx, len, out)})]
    fn index(&self, index: I) -> &I::Output;
}

#[flux_rs::extern_spec(core::ops)]
#[flux_rs::assoc(fn in_bounds(v: Self, idx: Idx) -> bool)]
trait IndexMut<Idx> {
    #[flux_rs::sig(fn(self: &mut Self[@v], index: Idx { <Self as IndexMut<Idx>>::in_bounds(v, index) }) -> &mut Self::Output)]
    fn index_mut(&mut self, index: Idx) -> &mut Self::Output;
}

#[flux_rs::extern_spec(core::slice)]
#[flux_rs::assoc(fn in_bounds(len: int, idx: I) -> bool { <I as SliceIndex<[T]>>::in_bounds(idx, len) })]
impl<T, I: SliceIndex<[T]>> IndexMut<I> for [T] {
    #[flux_rs::no_panic]
    #[flux_rs::sig(fn(&mut Self[@len], {I[@idx] | <Self as IndexMut<I>>::in_bounds(len, idx)}) -> &mut I::Output{out: <I as SliceIndex<[T]>>::output_pred(idx, len, out)})]
    fn index_mut(&mut self, index: I) -> &mut I::Output;
}

#[flux_rs::extern_spec]
impl<T> [T] {
    #[flux_rs::sig(fn(&[T][@len]) -> usize[len])]
    fn len(v: &[T]) -> usize;

    #[flux_rs::sig(fn(&[T][@len]) -> Iter<T>[0, len])]
    fn iter(v: &[T]) -> Iter<'_, T>;

    #[flux_rs::sig(
        fn(&[T][@len], mid: usize) -> (&[T][mid], &[T][len - mid])
        requires mid <= len
    )]
    fn split_at(v: &[T], mid: usize) -> (&[T], &[T]);

    #[flux::sig(
        fn(&mut [T][@len], src: &[T][@src_len]) -> ()
        requires src_len == len
    )]
    const fn copy_from_slice(&mut self, src: &[T])
    where
        T: Copy;
}
