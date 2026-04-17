# Panic Elimination Strategy

Sourced from `panic_summary.txt` against the vanilla nrf52840dk binary.
Total binary panic sites: 288 (86 in `core` ignored; 44 unattributed).

---

## Key insight: capsules/core ≠ binary savings

`capsules/core` verification proves panic-freedom formally, but LLVM already
eliminates those bounds panics via visible guards. Zero binary impact. The
value of capsules/core work is a correctness/safety argument, not a size
argument.

Worse: the vanilla capsules/core code used safe Rust with no `unsafe`. To make
it Flux-checkable we introduced `unsafe { get_unchecked(...) }` blocks, then
used Flux to justify them. This is a net loss in simplicity for zero runtime
benefit. We traded readable safe code for verified unsafe code that behaves
identically.

The stronger value proposition would be verifying existing safe code as
panic-free without modifications — but Flux currently ICEs on the vanilla
patterns, requiring restructuring just to unblock the checker.

**Conclusion**: capsules/core is methodology demonstration only. The scientific
contribution requires Track 2/3, where Flux does something the compiler cannot:
enables removal of panics that genuinely survive into the binary because LLVM
lacks the proof.

Binary savings require working on code where LLVM cannot prove safety on its
own — protocol-level invariants, cell liveness, non-trivial index relationships.

---

## Panic sites by origin

| Source | Sites | Notes |
|--------|-------|-------|
| `capsules/extra` | 127 | Primary target for binary savings |
| `kernel` | 54 | Largely out of scope |
| `libraries/tock-cells` | 46 | **Highest leverage** — cascades everywhere |
| `libraries/tickv` | 17 | Unwrap-heavy state machine |
| chips (nrf52/nrf52840/nrf5x) | ~30 | Out of scope |
| `capsules/system` | 6 | `write_str` — skip |

Top individual files:
1. `libraries/tock-cells/src/take_cell.rs` — **35 sites**
2. `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs` — 30 sites
3. `capsules/extra/src/tickv.rs` — 25 sites
4. `capsules/extra/src/net/sixlowpan/sixlowpan_state.rs` — 17 sites
5. `kernel/src/processbuffer.rs` — 17 sites
6. `capsules/extra/src/mx25r6435f.rs` — 13 sites

---

## Three-track plan

### Track 1: capsules/core verification (ongoing)
**Goal**: proof-of-concept that Flux can certify a driver as panic-free.
**Binary impact**: none.
**Value**: formal safety guarantee; POC for the methodology.

### Track 2: capsules/extra bounds panics (~23 addressable)

`write_str` panics (fmt machinery) and `unwrap_failed` panics (TakeCell liveness,
open problem) are excluded. All entries below are `panic_bounds_check`,
`slice_end_index_len_fail`, or `slice_index_order_fail` — all provable with
index/length refinements.

#### Easy (~7 panics)

| File | Line | Panic | Operation | Proof obligation |
|------|------|-------|-----------|-----------------|
| `net/util.rs` | 46–47 | bounds ×2 | `buf1[full_bytes]`, `buf2[full_bytes]` | `full_bytes < buf1.len()`: follows from guard `bytes > buf.len()` + `full_bytes ≤ bytes` |
| `net/ipv6/ip_utils.rs` | 135–136 | bounds ×2 | `src_addr.0[i]`, `dst_addr.0[i+1]` in loop `i ≤ 14, step 2` | addr is `[u8; 16]`, `i+1 ≤ 15` — fixed-size array, trivially provable |
| `sip_hash.rs` | 169 | bounds | `buf[start + i..]` | `start + i ≤ buf.len()`: needs `start + len ≤ buf.len()` as precondition |
| `net/stream.rs` | 269 | bounds | `buf[i] = *b` in enumerate of `bs.iter().rev()` | `i < bs.len()` + `stream_len_cond!` gives `bs.len() ≤ buf.len()` |
| `net/network_capabilities.rs` | 146 | bounds | inside `AddrRange::is_addr_valid` | need to inspect — likely fixed-size addr array |

#### Medium: `mx25r6435f.rs` (~5 panics)

All are indexing into static SPI TX/RX buffers with small constant indices.

| Line | Panic | Operation | Proof obligation |
|------|-------|-----------|-----------------|
| 267 | bounds | `txbuffer[0]` | `txbuffer.len() > 0` — buffer is statically allocated |
| 297–299 | bounds ×3 | `txbuffer[0..3]` | `txbuffer.len() ≥ 4` — same |
| 591 | bounds | `write_buffer[0]` | `write_buffer.len() > 0` |

#### Medium: `ble_advertising_driver.rs` (~1 panic)

| Line | Panic | Operation | Proof obligation |
|------|-------|-----------|-----------------|
| 503–504 | slice_end | `buf[0..len as usize]` | `len as usize ≤ buf.len()` |

#### Medium: `ieee802154/` stack (~10 panics)

`driver.rs` (6):

| Line | Panic | Operation | Proof obligation |
|------|-------|-----------|-----------------|
| 316 | slice_end | `neighbors[..num_neighbors]` | `num_neighbors ≤ neighbors.len()` — invariant on `num_neighbors` field |
| 372 | slice_end | `keys[..num_keys]` | `num_keys ≤ keys.len()` — same |
| 566 | slice_end | `neighbors[..self.num_neighbors.get()]` | same as 316 |
| 594 | slice_end | `keys[..self.num_keys.get()]` | same as 372 |
| 671 | bounds | inside `command()` | TBD — need further inspection |
| 1062 | slice_end | `rbuf[offset..offset+frame_len+META]` | `offset + frame_len + META ≤ rbuf.len()` |

`mac.rs` (1):

| Line | Panic | Operation | Proof obligation |
|------|-------|-----------|-----------------|
| 160 | slice_end | `full_mac_frame.copy_within(0..frame_len, PSDU_OFFSET)` | `PSDU_OFFSET + frame_len ≤ full_mac_frame.len()` |

`framer.rs` (3):

| Line | Panic | Operation | Proof obligation |
|------|-------|-----------|-----------------|
| 421 | slice_end + slice_order | `buf[PSDU_OFFSET..buf.len()-LQI_SIZE]` | `buf.len() ≥ PSDU_OFFSET + LQI_SIZE` |
| 695 | slice_end | `buf[PSDU_OFFSET..PSDU_OFFSET+MAX_FRAME_SIZE]` | `buf.len() ≥ PSDU_OFFSET + MAX_FRAME_SIZE` |

### Track 3: tock-cells/take_cell — NOT FEASIBLE with Flux
`TakeCell` uses interior mutability. Proving a cell is currently occupied at a
call site requires tracking heap ownership state across shared references — a
separation logic property (Iris/RustBelt-style), not a refinement type property.
Flux can express `index < len` but cannot express "this cell is currently Some."
This is an open problem; drop this track.

---

## Headline result

The paper claim is: **"We eliminated N panic call sites from the nrf52840dk
binary."** Target numbers:
- Track 2: ~7–10 (modest but genuinely earned; Track 3 is not feasible)

Track 1 (capsules/core) supports a separate claim: **"We formally verified
[N] capsule drivers as panic-free."** These are complementary, not competing.

---

## What to do next

1. Commit the current gpio.rs Flux ICE fixes (no binary impact, but unblocks
   verification progress).
2. Start Track 2 with `net/util.rs` or `sip_hash.rs` — small files, quick wins,
   validates the binary-savings workflow end-to-end.
3. Investigate Track 3: read `take_cell.rs` and prototype a Flux refinement for
   cell liveness before committing to it.
