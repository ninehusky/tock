// Licensed under the Apache License, Version 2.0 or the MIT License.
// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright Tock Contributors 2022.

//! Interface for LEDs that abstract away polarity and pin.
//!
//!  Author: Philip Levis <pal@cs.stanford.edu>
//!  Date: July 31, 2015
//!

use crate::hil::gpio;

/// Simple on/off interface for LED pins.
///
/// Since GPIO pins are synchronous in Tock the LED interface is synchronous as
/// well.
#[flux_rs::assoc(fn init_no_panic() -> bool)]
#[flux_rs::assoc(fn on_no_panic() -> bool)]
#[flux_rs::assoc(fn off_no_panic() -> bool)]
#[flux_rs::assoc(fn toggle_no_panic() -> bool)]
pub trait Led {
    /// Initialize the LED. Must be called before the LED is used.
    #[flux_rs::sig(fn (&Self) -> ())]
    #[flux_rs::no_panic_if(Self::init_no_panic())]
    fn init(&self);

    /// Turn the LED on.
    #[flux_rs::sig(fn (&Self) -> ())]
    #[flux_rs::no_panic_if(Self::on_no_panic())]
    fn on(&self);

    /// Turn the LED off.
    #[flux_rs::sig(fn (&Self) -> ())]
    #[flux_rs::no_panic_if(Self::off_no_panic())]
    fn off(&self);

    /// Toggle the LED.
    #[flux_rs::sig(fn (&Self) -> ())]
    #[flux_rs::no_panic_if(Self::toggle_no_panic())]
    fn toggle(&self);

    /// Return the on/off state of the LED. `true` if the LED is on, `false` if
    /// it is off.
    fn read(&self) -> bool;
}

/// For LEDs in which on is when GPIO is high.
pub struct LedHigh<'a, P: gpio::Pin> {
    pub pin: &'a P,
}

/// For LEDs in which on is when GPIO is low.
pub struct LedLow<'a, P: gpio::Pin> {
    pub pin: &'a P,
}

impl<'a, P: gpio::Pin> LedHigh<'a, P> {
    pub fn new(p: &'a P) -> Self {
        Self { pin: p }
    }
}

impl<'a, P: gpio::Pin> LedLow<'a, P> {
    pub fn new(p: &'a P) -> Self {
        Self { pin: p }
    }
}

#[flux_rs::assoc(fn init_no_panic() -> bool { P::make_output_no_panic() })]
#[flux_rs::assoc(fn on_no_panic() -> bool { P::set_no_panic() })]
#[flux_rs::assoc(fn off_no_panic() -> bool { P::clear_no_panic() })]
#[flux_rs::assoc(fn toggle_no_panic() -> bool { P::toggle_no_panic() })]
impl<P: gpio::Pin> Led for LedHigh<'_, P> {
    #[flux_rs::sig(fn (&Self) -> ())]
    #[flux_rs::no_panic_if(Self::init_no_panic())]
    fn init(&self) {
        self.pin.make_output();
    }

    #[flux_rs::sig(fn (&Self) -> ())]
    #[flux_rs::no_panic_if(Self::on_no_panic())]
    fn on(&self) {
        self.pin.set();
    }

    #[flux_rs::sig(fn (&Self) -> ())]
    #[flux_rs::no_panic_if(Self::off_no_panic())]
    fn off(&self) {
        self.pin.clear();
    }

    #[flux_rs::sig(fn (&Self) -> ())]
    #[flux_rs::no_panic_if(Self::toggle_no_panic())]
    fn toggle(&self) {
        self.pin.toggle();
    }

    fn read(&self) -> bool {
        self.pin.read()
    }
}

#[flux_rs::assoc(fn init_no_panic() -> bool { P::make_output_no_panic() })]
#[flux_rs::assoc(fn on_no_panic() -> bool { P::clear_no_panic() })]
#[flux_rs::assoc(fn off_no_panic() -> bool { P::set_no_panic() })]
#[flux_rs::assoc(fn toggle_no_panic() -> bool { P::toggle_no_panic() })]
impl<P: gpio::Pin> Led for LedLow<'_, P> {
    #[flux_rs::sig(fn (&Self) -> ())]
    #[flux_rs::no_panic_if(Self::init_no_panic())]
    fn init(&self) {
        self.pin.make_output();
    }
    #[flux_rs::sig(fn (&Self) -> ())]
    #[flux_rs::no_panic_if(Self::on_no_panic())]
    fn on(&self) {
        self.pin.clear();
    }

    #[flux_rs::sig(fn (&Self) -> ())]
    #[flux_rs::no_panic_if(Self::off_no_panic())]
    fn off(&self) {
        self.pin.set();
    }

    #[flux_rs::sig(fn (&Self) -> ())]
    #[flux_rs::no_panic_if(Self::toggle_no_panic())]
    fn toggle(&self) {
        self.pin.toggle();
    }

    fn read(&self) -> bool {
        !self.pin.read()
    }
}
