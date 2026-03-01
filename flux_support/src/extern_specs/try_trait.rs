use core::ops::FromResidual;
use core::ops::Try;

use core::marker::Sized;

#[flux_rs::extern_spec(core::ops::Try)]
#[flux_rs::assoc(fn from_residual_no_panic() -> bool)]
trait FromResidual<R = <Self as Try>::Residual> {
    #[flux_rs::sig(fn(residual: R) -> Self)]
    #[flux_rs::no_panic_if(Self::from_residual_no_panic())]
    fn from_residual(residual: R) -> Self
    where
        Self: Sized;
}
