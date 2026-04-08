# Type 1: annotations that already existed (15 / 45)
## `kernel/src/grant.rs`
- [**L360**](https://github.com/ninehusky/tock/blob/ninehusky-non-trivial-verif/kernel/src/grant.rs#L360) `calc_padding` (inner fn) — bitwise arithmetic causes Flux overflow check to fire
- [**L1886**](https://github.com/ninehusky/tock/blob/ninehusky-non-trivial-verif/kernel/src/grant.rs#L1886) `Grant::iter` — ICE: `assertion left == right failed` in `infer.rs:869`

## `kernel/src/processbuffer.rs`
- [**L803**](https://github.com/ninehusky/tock/blob/ninehusky-non-trivial-verif/kernel/src/processbuffer.rs#L803) `ReadableProcessSlice::copy_to_slice_or_err` — possible out-of-bounds access; no Flux spec for `Enumerate` index
- [**L840**](https://github.com/ninehusky/tock/blob/ninehusky-non-trivial-verif/kernel/src/processbuffer.rs#L840) `ReadableProcessSlice::chunks` — ICE: unexpected escaping regions
- [**L922**](https://github.com/ninehusky/tock/blob/ninehusky-non-trivial-verif/kernel/src/processbuffer.rs#L922) `Index<usize> for ReadableProcessSlice` — `trusted_impl`; needs associated refinement on `Index` trait
- [**L1095**](https://github.com/ninehusky/tock/blob/ninehusky-non-trivial-verif/kernel/src/processbuffer.rs#L1095) `WriteableProcessSlice::chunks` — ICE: unexpected escaping regions
- [**L1176**](https://github.com/ninehusky/tock/blob/ninehusky-non-trivial-verif/kernel/src/processbuffer.rs#L1176) `Index<usize> for WriteableProcessSlice` — `trusted_impl`; needs associated refinement on `Index` trait

## `kernel/src/debug.rs`
- [**L284**](https://github.com/ninehusky/tock/blob/ninehusky-non-trivial-verif/kernel/src/debug.rs#L284) `assign_gpios` — ICE: Invalid deref of `*mut` (`place_ty.rs:481`)

## `kernel/src/process_loading.rs`
- [**L187**](https://github.com/ninehusky/tock/blob/ninehusky-non-trivial-verif/kernel/src/process_loading.rs#L187) `load_processes_from_flash` — ICE: expected array or slice type (`checker.rs:1188`)
- [**L355**](https://github.com/ninehusky/tock/blob/ninehusky-non-trivial-verif/kernel/src/process_loading.rs#L355) `load_process` — error jumping to join point
- [**L668**](https://github.com/ninehusky/tock/blob/ninehusky-non-trivial-verif/kernel/src/process_loading.rs#L668) `load_process_objects` — ICE: expected array or slice type (`checker.rs:1188`)
- [**L930**](https://github.com/ninehusky/tock/blob/ninehusky-non-trivial-verif/kernel/src/process_loading.rs#L930) `SequentialProcessLoaderMachine::done` — ICE: expected array or slice type (`checker.rs:1188`)

## `kernel/src/platform/mpu.rs`
- [**L71**](https://github.com/ninehusky/tock/blob/ninehusky-non-trivial-verif/kernel/src/platform/mpu.rs#L71) `DefaultGhost` impl — opaque wrapper (ghost state struct; Flux can't verify trivial constructor)

## `kernel/src/utilities/math.rs`
- [**L15**](https://github.com/ninehusky/tock/blob/ninehusky-non-trivial-verif/kernel/src/utilities/math.rs#L15) `closest_power_of_two` — bitwise arithmetic; supplementary Z3 proof pending
- [**L28**](https://github.com/ninehusky/tock/blob/ninehusky-non-trivial-verif/kernel/src/utilities/math.rs#L28) `closest_power_of_two_usize` — bitwise arithmetic; same as above

# Type 2: annotations representing Flux limitations (11 / 45)
## pointer arithmetic:
### `kernel/src/grant.rs`
- **L418** `get_counter_offset` — `self.counters_ptr.read()` on `*mut usize`; no extern spec for `*mut T::read`
- **L402** `offset_of_grant_data_t` — non-null proof requires memory layout invariants; `NonNull::new_unchecked` also has `assert_unsafe_precondition` that panics on null in debug builds — no sound `no_panic` extern spec possible
- **L481** `get_resource_slices` — calls `get_counter_offset` (trusted above) and `slice::from_raw_parts`; blocked transitively
- **L54** `from_raw_parts` helper (`ReadableProcessSlice`) — `from_raw_parts`/`transmute` have no Flux specs
- **L119** `from_raw_parts` helper (`WriteableProcessSlice`) — same as above

### `libraries/tock-cells/src/map_cell.rs`
- **L267** `maybe_uninit_replace` — `replace` requires aligned non-null pointer; no sound `no_panic` extern spec; pointer arithmetic UB check fires in debug builds
- **L120** `do_drop_in_place` — panic condition is conditional on `T: Drop`; can't express this in Flux today

## `kernel/src/process_standard.rs`
- **L268** `enqueue_task` — `MapCell::map` panic analysis blocked by `DerefMut` extern spec limitation

## `dyn` calls:
### `kernel/src/process_standard.rs`
- **L1398** `get_processid` — `&dyn Process` call; `CannotResolve` on dynamic dispatch
- **L1407** `get_grant_ptr` — `&dyn Process` call; `CannotResolve` on dynamic dispatch
- **L1057** `ProcessGrant::new` (inner block) — blockers: `bswap` intrinsic, unresolvable `dyn Process` method calls, `from_residual` unresolved

# Type 3: missing preconditions (8 / 45)
## `capsules/core/src/stream.rs`
- **L259** `encode_bytes` — `Index<RangeTo>` loses slice length refinement through `Index` trait
- **L267** `encode_bytes_be` — `enumerate()` index not tracked as `i < buf.len()`
- **L292** `decode_bytes` — same `Index<RangeTo>` length-loss as `encode_bytes`
- **L305** `decode_bytes_be` — same `enumerate()` tracking issue as `encode_bytes_be`

## `capsules/core/src/spi_peripheral.rs`
- **L38** `copy_buf_to_process_slice` — `enumerate()` index `i` not tracked as `i < dest_area.len()`
- **L65** `copy_process_slice_to_buf` — Flux can't see `kwbuf.len() >= subslice.len()` through `usize_min`

## `capsules/core/src/alarm.rs`
- **L234** — Flux cannot push `enter_grant_returns_ok` refinement through `FilterMap`

## `kernel/src/process_standard.rs`
- **L1337** `create` — constructor; unsafe block

# Type 4: stuff we're likely OK trusting (11 / 45)
## `kernel/src/debug.rs`
- **L427** `try_get_debug_writer` — `as_deref_mut` on static mut; `DerefMut::deref_mut` unresolvable (same as upcall)
- **L433** `get_debug_writer` — `unwrap()` on result of above; valid by board-init invariant

## `kernel/src/upcall.rs`
- **L181** `do_println` — `debug!()` macro calls `as_deref_mut`; Flux can't resolve `DerefMut::deref_mut`

## `kernel/src/grant.rs`
- **L1862** `Grant::each` — cannot thread `ProcessGrant` refinement through `Option`
- **L1949** `Iter::next` — `find_map` doesn't propagate `all_enterable` refinement from `new_if_allocated`

## `kernel/src/processbuffer.rs`
- **L886, 898, 910** `Index<Range>` impls on `ReadableProcessSlice` — needs associated refinement on `Index` trait
- **L1142, 1153, 1164** `Index` impls on `WriteableProcessSlice` — same as above

## `kernel/src/process_standard.rs`
- **L753** `grant_is_allocated` — `trusted_impl`; don't want to annotate `no_panic_if` on `Process` trait
- **L861** `get_grant_memory_if_allocated` — `trusted_impl`; don't want to annotate `no_panic_if` on `Process::enter_grant`


