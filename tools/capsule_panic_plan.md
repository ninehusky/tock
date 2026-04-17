# Capsule Panic Verification Plan

Panics sourced from `panic_summary.txt` against the vanilla nrf52840dk binary (184,322 bytes text).
Counts reflect binary-level panic sites; some are inlined duplicates of the same source line.

Panic types:
- `bounds` — `panic_bounds_check` (index out of bounds)
- `slice_end` — `slice_end_index_len_fail` (slice range end too large)
- `slice_order` — `slice_index_order_fail` (range start > end)
- `slice_len` — `len_mismatch_fail` / `copy_from_slice` length mismatch
- `unwrap` — `unwrap_failed` (Option::unwrap / unwrap_or_panic on None)
- `panic` — explicit `panic!()` call
- `write_str` — panic inside fmt::Write (not eliminatable without removing fmt machinery)

---

## capsules/core

These appear as unmatched linkage-name entries in the panic tool (source attribution failed).

| File | Panics | Type | Notes |
|------|--------|------|-------|
| `button.rs` | 1 | bounds | `fired`: already proven in current branch |
| `gpio.rs` | 1 | bounds | `ClientWithValue::fired`: same shape as button |

**Verdict**: button nearly done; gpio is one proof of the same shape. Both tractable.

---

## capsules/extra — tractable

Low panic counts, no networking complexity.

| File | Total | Breakdown | Notes |
|------|-------|-----------|-------|
| `net/util.rs` | 2 | bounds: 2 | Simple byte utility functions |
| `net/ip_utils.rs` | 2 | bounds: 2 | IP address utilities |
| `sip_hash.rs` | 1 | bounds: 1 | Hash over byte slice |
| `net/stream.rs` | 1 | bounds: 1 | Stream encode helper |
| `net/network_capabilities.rs` | 1 | bounds: 1 | Capability check |
| `ble_advertising_driver.rs` | 1 | slice_end: 1 | Single slice range check |
| `net/udp/udp_port_table.rs` | 1 | unwrap: 1 | Port table lookup |

**Verdict**: All single-panic files; straightforward bounds/unwrap proofs. Good early targets.

---

## capsules/extra — medium difficulty

Multiple panics per file; mostly bounds or unwrap, no deep protocol parsing.

| File | Total | Breakdown | Notes |
|------|-------|-----------|-------|
| `mx25r6435f.rs` | 7 | bounds: 6, unwrap: 1 | SPI flash driver; buffer indexing |
| `virtual_kv.rs` | 5 | unwrap: 5 | KV layer; all `unwrap_or_panic` on callbacks |
| `net/udp/driver.rs` | 1 | unwrap: 1 | UDP syscall driver |
| `net/udp/udp_send.rs` | 1 | unwrap: 1 | UDP send path |
| `net/ipv6/ipv6_send.rs` | 1 | panic: 1 | Explicit unreachable-style panic |
| `ieee802154/mac.rs` | 1 | slice_end: 1 | MAC frame building |
| `ieee802154/framer.rs` | 2 | slice_end: 1, slice_order: 1 | Frame parsing |

**Verdict**: Achievable with moderate effort. `mx25r6435f` is the largest single target here.

---

## capsules/extra — hard

High panic counts; complex protocol parsing or heavy `unwrap` chaining.

| File | Total | Breakdown | Notes |
|------|-------|-----------|-------|
| `tickv.rs` | 28 | unwrap: 25, panic: 3 | Extremely unwrap-heavy; every state transition |
| `net/sixlowpan/sixlowpan_compression.rs` | 19 | bounds: 9, slice_end: 5, slice_order: 3, panic: 2 | Dense byte-level packet parsing |
| `net/sixlowpan/sixlowpan_state.rs` | 10 | bounds: 3, slice_end: 5, slice_order: 2, unwrap: 2 | 6LoWPAN state machine |
| `net/ipv6/ipv6.rs` | 6 | unwrap: 3, slice_end: 1, bounds: 1, panic: 1 | IPv6 packet handling |
| `ieee802154/driver.rs` | 8 | slice_end: 6, bounds: 1, unwrap: 1 | 802.15.4 syscall driver |

**Verdict**: `tickv` is unwrap-heavy throughout its state machine — likely requires typestate
proofs across the whole module. `sixlowpan_compression` involves complex byte-offset
arithmetic that would need deep refinement types on buffer positions. Deprioritize.

---

## capsules/system

| File | Total | Breakdown | Notes |
|------|-------|-----------|-------|
| `process_printer.rs` | 6 | write_str: 6 | Panic inside `fmt::Write`; not eliminatable without removing fmt |

**Verdict**: Skip. These are inside the debug printing infrastructure.

---

## Summary

| Category | Files | Total panics | Recommendation |
|----------|-------|--------------|----------------|
| Done / nearly done | button, gpio | ~2 | Finish gpio |
| Tractable (single panic) | util, ip_utils, sip_hash, stream, net_caps, ble, udp_port | ~9 | Do next |
| Medium | mx25r6435f, virtual_kv, udp, ieee802154/framer | ~16 | After tractable |
| Hard | tickv, sixlowpan, ipv6, ieee802154/driver | ~61 | Deprioritize |
| Skip | process_printer | 6 | Skip |

Total capsule panics addressable without kernel/grant: ~27 (tractable + medium).
