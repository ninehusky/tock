#![allow(unused)]
use core::ops::{Index, Range, RangeTo};
use core::slice::{self, Iter, SliceIndex};

// `Range<Idx>` is already refined in extern_specs/range.rs; only `RangeTo<Idx>`
// needs a refinement struct here.
#[flux_rs::extern_spec(core::ops)]
#[flux_rs::refined_by(end: Idx)]
struct RangeTo<Idx> {
    #[field(Idx[end])]
    end: Idx,
}

// `SliceIndex<T>` exposes an associated refinement `in_bounds(idx, v) -> bool`
// that each impl specializes to its own bounds-check predicate. The default
// is `true` so impls without a tighter spec don't accidentally constrain
// callers.
#[flux_rs::extern_spec(core::slice)]
#[flux_rs::assoc(fn in_bounds(idx: Self, v: T) -> bool { true })]
trait SliceIndex<T>
where
    T: ?Sized,
{
}

#[flux_rs::extern_spec(core::slice)]
#[flux_rs::assoc(fn in_bounds(idx: int, len: int) -> bool { idx < len })]
impl<T> SliceIndex<[T]> for usize {}

#[flux_rs::extern_spec(core::slice)]
#[flux_rs::assoc(fn in_bounds(r: Range<int>, len: int) -> bool {
    r.start <= r.end && r.end <= len
})]
impl<T> SliceIndex<[T]> for Range<usize> {}

#[flux_rs::extern_spec(core::slice)]
#[flux_rs::assoc(fn in_bounds(r: RangeTo<int>, len: int) -> bool { r.end <= len })]
impl<T> SliceIndex<[T]> for RangeTo<usize> {}

// `Index<Idx>` declares an `in_bounds` associated refinement that each
// `Index<I> for X` impl specializes. The trait-level default is `true`, so
// types without a refined `Index` impl don't impose an extra precondition.
#[flux_rs::extern_spec(core::ops)]
#[flux_rs::assoc(fn in_bounds(len: int, idx: Idx) -> bool { true })]
trait Index<Idx>
where
    Idx: ?Sized,
{
}

// `Index<I>` for `[T]` forwards `in_bounds` to the underlying `SliceIndex`
// impl, so the `index` method's precondition specializes per-`I` automatically.
#[flux_rs::extern_spec(core::slice)]
#[flux_rs::assoc(fn in_bounds(len: int, idx: I) -> bool {
    <I as SliceIndex<[T]>>::in_bounds(idx, len)
})]
impl<T, I: SliceIndex<[T]>> Index<I> for [T] {
    #[flux_rs::no_panic_if(<Self as Index<I>>::in_bounds(len, idx))]
    #[flux_rs::sig(fn(&Self[@len], I[@idx]) -> &I::Output)]
    fn index(&self, index: I) -> &I::Output;
}

#[flux_rs::extern_spec]
impl<T> [T] {
    #[flux_rs::sig(fn(&[T][@len]) -> usize[len])]
    fn len(v: &[T]) -> usize;

    #[flux_rs::sig(fn(&[T][@len]) -> Iter<T>[0, len])]
    fn iter(v: &[T]) -> Iter<'_, T>;

    #[flux_rs::no_panic_if(mid <= len)]
    #[flux_rs::sig(
        fn(&[T][@len], mid: usize) -> (&[T][mid], &[T][len - mid])
    )]
    fn split_at(v: &[T], mid: usize) -> (&[T], &[T]);
}
