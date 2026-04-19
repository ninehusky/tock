# Panic Elimination Strategy

Last updated: 2026-04-19. Binary: nrf52840dk release, analyzed with
`find_panics_flux.py` (use this — not `find_panics.py`; see Tooling section).

Total panic call sites in current binary: **341** (via find_panics_flux.py).

---

## Tooling

- **`find_panics.py`** (Tock upstream): uses DWARF, categorizes by crate,
  counts call sites. Good for totals; bad for pinpointing innermost frames.
- **`find_panics_flux.py`** (ours): scans `gobjdump` disassembly for `bl` to
  panic sinks, resolves with `addr2line`, shows full inline chain. The
  **innermost frame** (no `(inlined)` prefix) is the actual panic call site.
  Bug fixed 2026-04-19: was failing to match mangled symbols; now uses
  `rustfilt` to demangle before matching against `symbols.txt`.
- Workflow: rebuild → `gobjdump -d ... > nrf52840dk.dis` → `find_panics_flux.py`.

---

## Corrected key insight: capsules/core and binary impact

An earlier version of this doc claimed capsules/core verification has "zero
binary impact" because LLVM eliminates those bounds panics. **This is wrong.**

Empirically confirmed 2026-04-19: reverting the `get_unchecked` edit in
`button.rs` brings back `button.rs:252` as a real binary panic. LLVM cannot
prove safety when the index is a runtime value (e.g. `pin_num` from an
interrupt). The `get_unchecked` edits in `button.rs` and `gpio.rs` are
load-bearing.

The correct framing: **LLVM elides bounds checks only when the bound is
statically visible.** When the index is a runtime value with no visible
guard, the panic survives. Track 1 (capsules/core) work does have binary
impact for those sites.

---

## Actual innermost panic sites in capsules/ (current binary)

Most "capsules_core" sites in the call stack trace back to `kernel/src/grant.rs`
(the `all_enterable` pattern) — those are NOT capsule-internal. Filter to
non-inlined frames only.

### capsules_core — real capsule-internal panics

| Site | Type | Difficulty | Notes |
|------|------|------------|-------|
| `process_console.rs:998` | `panic!()` | trivial | Intentional — forces kernel panic on debug command. `#[trusted]`. |
| `virtual_aes_ccm.rs:479` | `panic!()` | trivial | Intentional — `crypt_buf` not present. `#[trusted]`. |
| `process_console.rs:1187` | bounds | easy | `read_buf[0]` in `rx_len == 1` arm; needs uart HIL precondition `rx_len <= rx_buffer.len()` |
| `process_console.rs:1016` | bounds | easy-ish | `command[0]` inside `MapCell` closure; const array, `len > 0` |
| `spi_controller.rs:139,169,175` | unwrap | blocked | `kernel_write.take().unwrap()` — TakeCell liveness, open problem |
| `virtual_aes_ccm.rs:831` | bounds | hard | `cbuf[auth_last + i]` — CCM arithmetic invariants |
| `process_console.rs:1044,1205,1220,1328,1380` | bounds | hard | Buffer sizes through MapCell closures + history state machine |

### capsules_extra — real binary panic sites (Track 2 targets)

#### Easy (~7 panics)

| File | Lines | Panic | Proof obligation |
|------|-------|-------|-----------------|
| `net/util.rs` | 46–47 | bounds ×2 | `full_bytes < buf.len()`: follows from existing guard + `full_bytes ≤ bytes` |
| `net/ipv6/ip_utils.rs` | 135–136 | bounds ×2 | Fixed-size `[u8; 16]` addr, loop `i ≤ 14 step 2`, so `i+1 ≤ 15` — trivial |
| `sip_hash.rs` | 169 | bounds | `start + len ≤ buf.len()` as precondition |
| `net/stream.rs` | 269 | bounds | `i < bs.len()` + `stream_len_cond!` gives `bs.len() ≤ buf.len()` |
| `net/network_capabilities.rs` | 146 | bounds | Fixed-size addr array — needs inspection |

#### Medium: `mx25r6435f.rs` (~5 panics)

Static SPI TX/RX buffers indexed with small constant offsets.

| Lines | Proof obligation |
|-------|-----------------|
| 267, 297–299, 591 | `txbuffer.len() ≥ 4`, `write_buffer.len() > 0` — statically allocated buffers |

#### Medium: `ieee802154/` stack (~10 panics)

`driver.rs` lines 316, 372, 566, 594, 671, 1062 — `num_neighbors`/`num_keys`
invariants. `mac.rs:160`, `framer.rs:421,695` — frame size invariants.

---

## Three-track plan

### Track 1: capsules/core (ongoing)
**Goal**: formally certify drivers as panic-free; some sites also have binary impact.
**Current state**: `alarm`, `button`, `led`, `console`, `spi_peripheral`, `stream` verified.
**Remaining binary panics**: `process_console` (8 sites), `virtual_aes_ccm` (2 sites).
**Next**: trivial `#[trusted]` for `:998` and `:479`; then uart HIL precondition for `:1187`.

### Track 2: capsules/extra bounds panics (untouched)
**Goal**: genuine binary size reduction via Flux-proven index invariants.
**Binary impact**: real.
**Next**: `net/util.rs` or `net/ipv6/ip_utils.rs` — smallest files, clearest proofs.

### Track 3: tock-cells/take_cell — NOT FEASIBLE
TakeCell uses interior mutability. Proving a cell is currently occupied requires
tracking heap ownership state — a separation logic property (Iris/RustBelt),
not a refinement type property. Flux cannot express "this cell is currently Some."
`spi_controller.rs:139/169/175` falls into this category. Dropped.

---

## Headline result

Two complementary claims:
1. **"We formally verified N capsule drivers as panic-free."** (Track 1)
2. **"We eliminated N panic call sites from the nrf52840dk binary."** (Track 2, target: 7–10)
