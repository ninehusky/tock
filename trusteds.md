# Trusted Annotations Added by Andrew

## `kernel/src/grant.rs`

- **L360** `calc_padding` (inner fn) — bitwise arithmetic causes Flux overflow check to fire
- **L402** `offset_of_grant_data_t` — non-null proof requires memory layout invariants (grant region in valid RAM, no pointer wrap-to-zero); `NonNull::new_unchecked` also has `assert_unsafe_precondition` that panics on null in debug builds — no sound `no_panic` extern spec possible
- **L422** `get_counter_offset` — `self.counters_ptr.read()` on `*mut usize`; no extern spec for `*mut T::read`
- **L485** `get_resource_slices` — calls `get_counter_offset` (trusted above) and `slice::from_raw_parts`; blocked transitively
- **L1061** `ProcessGrant::new` (inner block) — blockers: `bswap` intrinsic, unresolvable `dyn Process` method calls, `from_residual` unresolved
- **L1402** `get_processid` — `&dyn Process` call; `CannotResolve` on dynamic dispatch
- **L1411** `get_grant_ptr` — `&dyn Process` call; `CannotResolve` on dynamic dispatch
- **L1866** `Grant::each` — cannot thread `ProcessGrant` refinement through `Option`
- **L1890** `Grant::iter` — ICE: `assertion left == right failed` in `infer.rs:869`
- **L1953** `Iter::next` — `find_map` doesn't propagate `all_enterable` refinement from `new_if_allocated`

## `kernel/src/process_standard.rs`

- **L268** `enqueue_task` — `MapCell::map` panic analysis blocked by `DerefMut` extern spec limitation
- **L753** `grant_is_allocated` — `trusted_impl`; don't want to annotate `no_panic_if` on `Process` trait
- **L861** `get_grant_memory_if_allocated` — `trusted_impl`; don't want to annotate `no_panic_if` on `Process::enter_grant`
- **L1337** `create` — unsafe constructor; needs proper spec

## `kernel/src/processbuffer.rs`

- **L54** `from_raw_parts` helper (ReadableProcessSlice) — `from_raw_parts`/`transmute` have no Flux specs
- **L119** `from_raw_parts` helper (WriteableProcessSlice) — same as above
- **L803** — needs investigation
- **L840** — ICE: unexpected escaping regions
- **L886, 898, 910, 922** `Index<Range>` impls on `ReadableProcessSlice` — needs associated refinement on `Index` trait
- **L1095** — ICE: unexpected escaping regions
- **L1142, 1153, 1164, 1176** `Index` impls on `WriteableProcessSlice` — same as above

## `kernel/src/upcall.rs`

- **L181** `do_println` — `debug!()` macro calls `as_deref_mut` which Flux can't resolve `DerefMut::deref_mut` for

## `kernel/src/debug.rs`

- **L284** `assign_gpios` — ICE: Invalid deref of `*mut` (`place_ty.rs:481`)
- **L427** `try_get_debug_writer` — `as_deref_mut` on static mut; same `DerefMut` issue
- **L433** `get_debug_writer` — `unwrap()` on result of above; board-init invariant

## `kernel/src/syscall_driver.rs` (DONE)

- ~~**L87** — `unwrap()` on `ErrorCode::try_from(rc)` in `Err` arm~~ **DONE** — restructured `match` to `Err(e) => CommandReturn::failure(e)`, eliminating the `unwrap()` entirely

## `libraries/tock-cells/src/map_cell.rs`

- **~L93 (now `do_drop_in_place` helper, ~L120)** — `drop_in_place` panic condition is conditional on `T: Drop`; can't express this with current Flux tools
- **`maybe_uninit_replace` helper (~L267)** — `*mut T::replace` requires aligned, non-null pointer; no sound `no_panic` extern spec; pointer arithmetic UB check fires in debug builds
- ~~**`get`**~~ **DONE** — restructured from `bool::then` to `match`; Flux can verify signature and no_panic directly
- ~~**`take`**~~ **DONE** — same match restructuring; unsafe body extracted into trusted `maybe_uninit_replace` helper
- ~~**`put`**~~ **DONE** — removed trusted
- **`replace`** — deferred
- **`map`** — deferred (UnsafeCell interior mutation)

## `capsules/core/src/alarm.rs`

- **L234** — Flux cannot push `enter_grant_returns_ok` refinement through `FilterMap`

## `capsules/core/src/button.rs` (DONE)

- ~~**L238** `ClientWithValue::fired`~~ **DONE** — trait `requires` clause shape doesn't unify with impl; resolved

## `capsules/core/src/spi_peripheral.rs`

- **L38** `copy_buf_to_process_slice` — `enumerate()` index `i` not tracked as `i < dest_area.len()`
- **L65** `copy_process_slice_to_buf` — Flux can't see `kwbuf.len() >= subslice.len()` through `usize_min`

## `capsules/core/src/stream.rs`

- **L259** `encode_bytes` — `Index<RangeTo>` loses slice length refinement through `Index` trait
- **L267** `encode_bytes_be` — `enumerate()` index not tracked as `i < buf.len()`
- **L292** `decode_bytes` — same `Index<RangeTo>` length-loss as `encode_bytes`
- **L305** `decode_bytes_be` — same `enumerate()` tracking issue as `encode_bytes_be`
