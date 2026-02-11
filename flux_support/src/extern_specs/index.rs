#[flux_rs::extern_spec(core::ops)]
trait Index<Idx> {
    #![flux_rs::assoc(fn in_bounds(v: Self, idx: Idx) -> bool { true })]

    #[flux_rs::sig(fn(self: &Self[@v], index: Idx { <Self as Index<Idx>>::in_bounds(v, index) }) -> &Self::Output)]
    fn index(&self, index: Idx) -> &Self::Output;
}
