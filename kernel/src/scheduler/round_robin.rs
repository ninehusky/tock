// Licensed under the Apache License, Version 2.0 or the MIT License.
// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright Tock Contributors 2022.

//! Round Robin Scheduler for Tock
//!
//! This scheduler is specifically a Round Robin Scheduler with Interrupts.
//!
//! See: <https://www.eecs.umich.edu/courses/eecs461/lecture/SWArchitecture.pdf>
//! for details.
//!
//! When hardware interrupts occur while a userspace process is executing, this
//! scheduler executes the top half of the interrupt, and then stops executing
//! the userspace process immediately and handles the bottom half of the
//! interrupt. This design decision was made to mimic the behavior of the
//! original Tock scheduler. In order to ensure fair use of timeslices, when
//! userspace processes are interrupted the scheduler timer is paused, and the
//! same process is resumed with the same scheduler timer value from when it was
//! interrupted.

use core::cell::Cell;

use crate::collections::list::{List, ListLink, ListNode};
use crate::platform::chip::Chip;
use crate::process::Process;
use crate::process::StoppedExecutingReason;
use crate::scheduler::{Scheduler, SchedulingDecision};

/// A node in the linked list the scheduler uses to track processes
/// Each node holds a pointer to a slot in the processes array
pub struct RoundRobinProcessNode<'a> {
    proc: &'static Option<&'static dyn Process>,
    next: ListLink<'a, RoundRobinProcessNode<'a>>,
}

impl<'a> RoundRobinProcessNode<'a> {
    pub fn new(proc: &'static Option<&'static dyn Process>) -> RoundRobinProcessNode<'a> {
        RoundRobinProcessNode {
            proc,
            next: ListLink::empty(),
        }
    }
}

impl<'a> ListNode<'a, RoundRobinProcessNode<'a>> for RoundRobinProcessNode<'a> {
    fn next(&'a self) -> &'a ListLink<'a, RoundRobinProcessNode<'a>> {
        &self.next
    }
}

/// Round Robin Scheduler
pub struct RoundRobinSched<'a> {
    time_remaining: Cell<u32>,
    timeslice_length: u32,
    pub processes: List<'a, RoundRobinProcessNode<'a>>,
    last_rescheduled: Cell<bool>,
}

impl<'a> RoundRobinSched<'a> {
    /// How long a process can run before being pre-empted
    const DEFAULT_TIMESLICE_US: u32 = 10000;
    pub const fn new() -> RoundRobinSched<'a> {
        Self::new_with_time(Self::DEFAULT_TIMESLICE_US)
    }

    pub const fn new_with_time(time_us: u32) -> RoundRobinSched<'a> {
        RoundRobinSched {
            time_remaining: Cell::new(time_us),
            timeslice_length: time_us,
            processes: List::new(),
            last_rescheduled: Cell::new(false),
        }
    }
}

impl<'a, C: Chip> Scheduler<C> for RoundRobinSched<'a> {
    // FLUX-TODO-FN-LEVEL covers=[0x1d88] flavor=explicit_panic
    // panic somewhere in this fn body; addr2line lost the line
    // (LTO + generic monomorphization). See breadcrumb comments in body.
    fn next(&self) -> SchedulingDecision {
        let mut first_head = None;
        let mut next = None;

        // Find the first ready process in the queue. Place any *empty* process slots,
        // or not-ready processes, at the back of the queue.
        while let Some(node) = self.processes.head() {
            // Ensure we do not loop forever if all processes are not ready
            match first_head {
                None => first_head = Some(node),
                Some(first_head) => {
                    // We made a full iteration and nothing was ready. Try to sleep instead
                    if core::ptr::eq(first_head, node) {
                        return SchedulingDecision::TrySleep;
                    }
                }
            }
            match node.proc {
                Some(proc) => {
                    if proc.ready() {
                        next = Some(proc.processid());
                        break;
                    }
                    // FLUX-TODO addr=0x1dcc line=100 flavor=unwrap_option
                    let head_opt = self.processes.pop_head();
                    flux_support::assert(head_opt.is_some());
                    self.processes.push_tail(head_opt.unwrap());
                }
                None => {
                    self.processes.push_tail(self.processes.pop_head().unwrap());
                }
            }
        }

        let next = match next {
            Some(p) => p,
            None => {
                // No processes on the system
                return SchedulingDecision::TrySleep;
            }
        };

        let timeslice = if self.last_rescheduled.get() {
            self.time_remaining.get()
        } else {
            // grant a fresh timeslice
            self.time_remaining.set(self.timeslice_length);
            // FLUX-TODO addr=0x1d88 line=123 flavor=explicit_panic
            self.timeslice_length
        };
        // FLUX-TODO addr=0x1d88 line=123 flavor=explicit_panic
        flux_support::assert(timeslice != 0);
        assert!(timeslice != 0);

        SchedulingDecision::RunProcess((next, Some(timeslice)))
    }

    #[flux_rs::trusted_impl(reason = "TODO: we would need to push a refinement up to the `Scheduler` trait to discharge the precondition, which elicits more proofs about callers.")]
    #[flux_rs::sig(fn (&Self, StoppedExecutingReason, execution_time_us: Option<u32>[true]) -> ())]
    fn result(&self, result: StoppedExecutingReason, execution_time_us: Option<u32>) {
        flux_support::assert(execution_time_us.is_some());
        // FLUX-OPT addr=0x1d7e line=132 flavor=unwrap_option
        let execution_time_us = execution_time_us.unwrap(); // should never fail
        let reschedule = match result {
            StoppedExecutingReason::KernelPreemption => {
                let t = self.time_remaining.get();
                if t > execution_time_us {
                    self.time_remaining.set(t - execution_time_us);
                    true
                } else {
                    false
                }
            }
            _ => false,
        };
        self.last_rescheduled.set(reschedule);
        if !reschedule {
            // FLUX-TODO addr=0x1e7a line=147 flavor=unwrap_option
            let head_opt = self.processes.pop_head();
            flux_support::assert(head_opt.is_some());
            self.processes.push_tail(head_opt.unwrap());
        }
    }
}
