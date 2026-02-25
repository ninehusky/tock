use core::default::Default;

#[flux_rs::extern_spec(core::default)]
#[flux_rs::assoc(fn default_no_panic() -> bool { true })]
trait Default {
    #[flux_rs::sig(fn() -> Self)]
    #[flux_rs::no_panic_if(Self::default_no_panic())]
    fn default() -> Self;
}
