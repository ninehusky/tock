use core::ops::{Deref, DerefMut};

#[flux_rs::extern_spec(core::ops)]
trait DerefMut: Deref {
    #[flux_rs::no_panic]
    fn deref_mut(&mut self) -> &mut Self::Target;
}
