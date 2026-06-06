# Trust reduction — issue #12 (use `trusted` extremely sparingly)

Branch: ninehusky-move-trusted-to-unproven. Date: 2026-06-05.

Goal (issue #12): ditch every `trusted` except (1) ICE-dodges, (2) genuine
proof trust, (3) existing non-ninehusky (vtock) markers. Authorship decided by
`git blame` of the attribute line.

## Census of 216 `#[flux_rs::trusted]` attributes (excludes 8 `trusted_impl`)

| bucket | count | disposition |
|---|---|---|
| (3) non-ninehusky author (vrindisbacher 93 / e5johnso 11 / nico 10 / jhala+rjhala+samir 3) | 117 | KEEP |
| (1) ninehusky + ICE-dodge (incl. **2 latent ICEs** found at run time, see below) | 34 | KEEP |
| (2) ninehusky + genuine (asm hardware handlers, bitwise-theorem lemmas, the `assume` primitive) | 6 | KEEP |
| ninehusky + "stuck/TODO/missing-spec/blocked-cell/real-bug" | 59 | **REMOVE → expect FAILING** |

Total kept: 157. Total removed: 59.

## Two latent ICEs discovered (the surprise)

First pass removed all 61. The probe then showed **ICE_MASKED jumping to 133**,
not the expected TRUSTED→FAILING. Two of the 61 I removed were actually
ICE-dodges with *wrong/empty reasons* — un-trusting them crashes Flux, and
because capsules-extra → capsules-core, a single crash poisons whole crates:

1. `capsules/core/src/virtualizers/virtual_aes_ccm.rs` trait-impl `crypt_done`
   (old reason `blocked-cell`) — fixpoint crash `fixpoint_encoding.rs:702:
   Cannot unify Adt0 with bool`.
2. `capsules/extra/src/mx25r6435f.rs` trait-impl `read_sector` (old reason was an
   **empty** `#[flux_rs::trusted()]`) — `infer.rs:1033: assertion left==right`
   on `Client<MX25R6435F<..>>` monomorphization (same ICE family its explicitly
   ICE-trusted siblings in that file dodge).

Both restored with corrected ICE reasons. After restoring, `cargo flux clean &&
cargo flux` on capsules-core and capsules-extra are both ICE-free. These are
genuine category (1); their metadata was simply mislabeled.

**Lesson:** "use trusted sparingly" can't be done purely from `reason` text — a
couple of ICE-dodges were hiding under non-ICE reasons and only the actual Flux
run reveals them.

## (2) Genuine-trust KEEPs (ninehusky-authored, 6)
| `flux_support/src/lib.rs:30` | assume() is the canonical Flux escape hatch: sig `ensures b` is the verification interface; the |
| `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1071` | bitwise theorem: (x & 0x30) & 0x33 ∈ {0, 0x10, 0x20, 0x30} |
| `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1085` | bitwise theorem: (x & 0x03) & 0x33 ∈ {0, 0x01, 0x02, 0x03} |
| `capsules/extra/src/net/sixlowpan/sixlowpan_compression.rs:1101` | bitwise theorem: x & 0x33 ∈ {0, 0x01, 0x02, 0x03, 0x10, 0x20, 0x30} |
| `arch/cortex-v7m/src/lib.rs:290` | hardware-invoked asm hard-fault handler (arm-only cfg, never returns); Flux cannot analyze the  |
| `arch/cortex-m/src/lib.rs:134` | hardware-invoked asm interrupt handler (reads IPSR via asm!, then panics); Flux cannot analyze  |

## REMOVED (61) — ninehusky-authored, not ICE, not genuine
| site | reason (was) |
|---|---|
| `arch/cortex-m/src/syscall.rs:72` | This 4 represents `USIZE_SZ` on cortex-M |
| `arch/cortex-m/src/syscall.rs:97` | copy_from_slice explodes: `dest` has length 4, but `to_le_bytes` returns `[u8; 8]` on my machin |
| `arch/cortex-m/src/systick.rs:111` | Body's `self.hertz != 0` branch returns `self.hertz`, which is provably > 0 from the path condi |
| `boards/nordic/nrf52840dk/src/io.rs:41` | panic-path debug writer; the bounds obligation at io.rs:79 is checkable in isolation but the bo |
| `capsules/core/src/process_console.rs:287` | Will come back later, Flux errors don't correspond to codegened panics |
| `capsules/core/src/process_console.rs:297` | Will come back later, Flux errors don't correspond to codegened panics |
| `capsules/core/src/process_console.rs:309` | Will come back later, Flux errors don't correspond to codegened panics |
| `capsules/core/src/process_console.rs:363` | Will come back later, Flux errors don't correspond to codegened panics |
| `capsules/core/src/process_console.rs:375` | Will come back later, Flux errors don't correspond to codegened panics |
| `capsules/core/src/process_console.rs:383` | Will come back later, Flux errors don't correspond to codegened panics |
| `capsules/core/src/process_console.rs:424` | TODO: copy_from_slice precondition fails. need to refine str::as_bytes |
| `capsules/core/src/process_console.rs:434` | TODO: copy_from_slice. probably easy, but may need refinement on usize::min. |
| `capsules/core/src/process_console.rs:761` | blocked_cell: bounds inside MapCell closure require Cell-state invariants |
| `capsules/core/src/process_console.rs:1055` | blocked_cell: bounds inside MapCell/TakeCell closures require Cell-state invariants |
| `capsules/core/src/process_console.rs:1075` | TODO: discharge copy_from_slice precondition; cascade from new extern spec |
| `capsules/core/src/process_console.rs:1102` | TODO: copy_from_slice -- cmp::min is tricky; may be blocked on cell stuff |
| `capsules/core/src/virtualizers/virtual_aes_ccm.rs:338` | TODO: copy_from_slice -- may need simple refinement on range syntax `i..j` |
| `capsules/core/src/virtualizers/virtual_aes_ccm.rs:458` | extern-spec gap: IndexMut<I> for [T] not specified in flux_support; iv[0] = 1 is provably safe  |
| `capsules/core/src/virtualizers/virtual_aes_ccm.rs:515` | TODO: discharge copy_from_slice precondition; cascade from new extern spec |
| `capsules/core/src/virtualizers/virtual_aes_ccm.rs:605` | blocked-cell |
| `capsules/core/src/virtualizers/virtual_aes_ccm.rs:622` | blocked-cell |
| `capsules/core/src/virtualizers/virtual_aes_ccm.rs:708` | Real bug: guard `key.len() < AES128_KEY_SIZE` allows longer keys; downstream `copy_from_slice`  |
| `capsules/core/src/virtualizers/virtual_aes_ccm.rs:720` | Real bug: guard `nonce.len() < CCM_NONCE_LENGTH` allows longer nonces; downstream `copy_from_sl |
| `capsules/core/src/virtualizers/virtual_aes_ccm.rs:855` | blocked-cell |
| `capsules/extra/src/ieee802154/driver.rs:158` | blocked_flux_stream_combinator: SResult offset<=len invariant not Flux-tracked |
| `capsules/extra/src/ieee802154/framer.rs:143` | missing spec: copy_from_slice |
| `capsules/extra/src/ieee802154/framer.rs:157` | missing spec: copy_from_slice |
| `capsules/extra/src/ieee802154/framer.rs:239` | Body uses `encode_bytes(buf, &device_addr[..])` which works in principle, but the chain of `enc |
| `capsules/extra/src/ieee802154/framer.rs:558` | need to prove precondition about cell so that ccm_encrypt_ranges won't panic |
| `capsules/extra/src/ieee802154/framer.rs:633` | missing spec: copy_from_slice; ccm_encrypt_ranges precondition is on cell |
| `capsules/extra/src/ieee802154/framer.rs:929` | missing: needs a way to generically specify precondition on top of `incoming_frame_security` |
| `capsules/extra/src/mx25r6435f.rs:290` | (empty) |
| `capsules/extra/src/net/ipv6/ip_utils.rs:110` | missing spec: copy_from_slice. flux_support::assert ensures the next assert fine. |
| `capsules/extra/src/net/ipv6/ipv6.rs:163` | Sig captures Done iff buf.len() >= 40 (IPv6 header is fixed 40 bytes). Body is `stream_len_cond |
| `capsules/extra/src/net/ipv6/ipv6.rs:271` | Pre-existing flux errors on copy_from_slice + UDPHeader::decode + checksum-compute calls; not i |
| `capsules/extra/src/net/ipv6/ipv6.rs:376` | Gap (2): match arms do `udp_header.set_len(length)` on a destructured `mut` binding from a `Cop |
| `capsules/extra/src/net/ipv6/ipv6.rs:515` | Pre-existing flux errors on compute_udp_checksum/compute_icmp_checksum preconditions (lines 484 |
| `capsules/extra/src/net/sixlowpan/sixlowpan_state.rs:296` | Body verifies with `output_pred`/`in_bounds` once caller(s) can supply `packet.len() >= 5`. Unt |
| `capsules/extra/src/net/sixlowpan/sixlowpan_state.rs:309` | Body verifies with `in_bounds` once caller(s) supply `packet.len() >= 1`. Stays trusted until ` |
| `capsules/extra/src/net/sixlowpan/sixlowpan_state.rs:600` | IP6Packet refinement (kind != 1) for `get_total_hdr_size`/`encode` calls |
| `capsules/extra/src/net/sixlowpan/sixlowpan_state.rs:626` | cascade from set_frag_hdr precondition `hdr.len() >= 5` |
| `capsules/extra/src/net/sixlowpan/sixlowpan_state.rs:860` | TODO: to verify `slice_view`, we need assoc reft on `RxClient::receive` |
| `capsules/extra/src/net/sixlowpan/sixlowpan_state.rs:975` | Two panicking rows. (1) is the cell unwrap. (2) is the slice op on `payload`, which needs some  |
| `capsules/extra/src/net/stream.rs:314` | missing spec: copy_from_slice |
| `capsules/extra/src/net/stream.rs:323` | missing spec: needs Iterator extern_specs for `iter().rev().enumerate()` to bound the yielded i |
| `capsules/extra/src/net/udp/udp.rs:56` | Stores in big-endian byte order; `u16::to_be` is not modeled by Flux extern specs. Sig records  |
| `capsules/extra/src/net/udp/udp.rs:74` | Stores in big-endian byte order; `u16::from_be` is not modeled by Flux extern specs. Sig record |
| `capsules/extra/src/sip_hash.rs:150` | Not in panic sites; need refinement on 0..8 length. |
| `capsules/extra/src/sip_hash.rs:159` | not in panic sites, need to prove precondition about mem::size_of::u16 |
| `capsules/extra/src/sip_hash.rs:169` | Missing extern spec for slice-output length on `Index<RangeFrom<usize>>`: panicking row in pani |
| `capsules/extra/src/sip_hash.rs:201` | u8to64_le precondition needs to be resolved here |
| `capsules/extra/src/sip_hash.rs:258` | u8to64_le precondition needs to be resolved here |
| `capsules/extra/src/tickv.rs:241` | impls external trait tickv::FlashController, whose def is not on Flux's include surface, so the |
| `capsules/extra/src/tickv.rs:267` | temporarily adding this |
| `chips/nrf52/src/usbd.rs:1895` | To prove `slice[..size]` safe, we need to prove that `self.descriptors[endpoint].slice_in.is_so |
| `chips/nrf52840/src/interrupt_service.rs:43` | struct Nrf52840DefaultPeripherals embeds external nrf52::chip::Nrf52DefaultPeripherals, whose d |
| `kernel/src/utilities/leasable_buffer.rs:335` | Generic over `RangeBounds<usize>`, so the new (start, end) can't be statically bounded without  |
| `kernel/src/utilities/leasable_buffer.rs:380` | Pending SubSliceMut refinement: this impl chains two refined index ops and we don't yet expose  |
| `libraries/tickv/src/async_ops.rs:356` | TODO: hash comes from `self.key.get().unwrap()` (Cell). Need cell-state refinement to discharge |
| `libraries/tickv/src/tickv.rs:421` | Real bug: Caller-checked precondition fails; need to update assert. |
| `libraries/tock-cells/src/optional_cell.rs:195` | blocked-cell |

## RESULT — obligation movement (check_invariant2.py, addr-keyed)

Baseline = HEAD clean tree (`/tmp/inv2_before.json`).
Final = after removing 59 trusteds, keeping 2 latent ICE-dodges (`/tmp/inv2_final.json`).

| status        | before | final | Δ |
|---|---|---|---|
| TRUSTED       | 79  | 49  | **−30** |
| FAILING       | 71  | 88  | **+17** |
| PROVEN        | 55  | 56  | +1 |
| SILENT        | 0   | 19  | +19 |
| DEAD_FAILING  | 17  | 16  | −1 |
| DEAD_PROVEN   | 10  | 9   | −1 |
| BLOCKED_DEP_MASKED | 0 | 1 | +1 |
| ICE_MASKED    | 0   | 0   | 0 |
| **total**     | 232 | 238 | +6 |
| gate          | pass | fail | (vacuous=20: 19 SILENT + 1 dep-masked) |

### What the ex-TRUSTED obligations became (addr-keyed transitions)
- **TRUSTED → FAILING: 20** — the genuine frontier; these now honestly report as failing.
- **TRUSTED → SILENT: 11** — structural Flux skips (external-trait impl bodies /
  fn-level not analyzed): `tickv.rs:248/270 read_region`, `interrupt_service
  service_interrupt`, `sip_hash u8to64_le`, etc. Un-trusting doesn't make Flux
  check them — it just stops *claiming* they're trusted; they surface as
  honestly-unchecked. (These are the issue's grey-zone: arguably category-2
  "genuine" carve-outs. Currently REMOVED → SILENT; re-mark if you prefer.)
- **TRUSTED → PROVEN: 6** — trust was unnecessary; verify outright once un-trusted:
  `ip_utils IPAddr` (123/125), `sip_hash read_le_u16`, `ipv6 IP6Packet:572`,
  `virtual_aes_ccm:661`, `tickv.rs:720`.
- 2 latent ICEs kept (crypt_done, read_sector); a few second-order reshuffles
  (PROVEN↔FAILING/SILENT) from the prober's dependency-unmask set changing now
  that more fns error.

Total obligations rose 232→238 because un-trusting some bodies exposed
additional precise/fn-level obligation sites that were previously hidden.

## SILENT triage + the external-trait-include investigation (2026-06-05)

The post-removal run showed 19 SILENT. Decomposed:
- **16 = line-shift artifacts.** Deleting 61 attribute lines shifted source lines,
  but the prober's input `invariant1_report.json` (built on clean HEAD) kept stale
  `assert_line`s → "no paired assert found below marker" → false SILENT. All 16 are
  in files I edited. Verified e.g. syscall.rs: recorded assert at L106/108, real
  asserts now at L105/107. Cleared by regenerating `invariant1_report.json` on the
  edited tree (re-anchor) — NOT a real finding.
- **3 = genuine external-trait/struct impl skips** (Flux emits E0999 "use of … not
  included when checking external crate" and skips the body):
  1. `FlashController::read_region` (capsules-extra/tickv.rs)
  2. `FlashController::write` (capsules-extra/tickv.rs)
  3. `InterruptService::service_interrupt` (nrf52840/interrupt_service.rs)

Minimal cross-crate repro built as a standalone `flux_external_trait_repro` (shared separately)
(reproduces the E0999 faithfully; inherent-method control checks fine, external-trait
impl body is skipped). It is **a documented Flux limitation, not a bug** — the error
text even says "include the file or module where the excluded item is defined."

### Remedy = include the trait/struct's defining file. Difficulty is NOT uniform:
- **tickv `FlashController` → CHEAP. APPLIED.** Added `src/flash_controller.rs` to
  `libraries/tickv/Cargo.toml` flux `include`. The file is a pure trait decl (no
  bodies, no external deps) → 0 new obligations; the 3 E0999s vanish; `read_region`
  & `write` become checked → FAILING (blocked-cell wall). 2 of 3 genuine SILENTs
  resolved → honest frontier.
- **nrf52 `service_interrupt` → INFEASIBLE. TRUSTED instead.** Tried adding nrf52's
  `src/chip.rs` (where the embedded `Nrf52DefaultPeripherals` lives). It cascaded:
  `chip.rs` itself uses external `cortexm4` items (`mpu::MPU`, `syscall::SysCall`,
  `nvic::next/has_pending`, `support::wfi/atomic`) that aren't included → **7 new
  E0999s one level down**, and the prober's unmask couldn't clear them → the whole
  nrf52840 crate went BLOCKED_DEP_MASKED (vacuity 16→29). The include limitation is
  **transitive/viral** down the chip→arch chain. Reverted; `service_interrupt` is now
  `#[flux_rs::trusted]` with a reason documenting this (a genuine include-limitation
  carve-out, not a stuck proof).

### Net SILENT outcome
genuine SILENT: 3 → **0** (2 → FAILING via tickv include; 1 → documented TRUSTED).
16 artifacts remain until `invariant1_report.json` is regenerated on the edited tree.

## FINAL clean run (2026-06-06, re-anchored — artifacts cleared)

After regenerating `invariant1_report.json` on the edited tree (re-anchor: the
committed `panic_survey.json` stays — a fresh build drifts 284/352 addresses, so
the survey is NOT regenerated; only the assert *lines* refresh from current
source), and applying the tickv include + service_interrupt/io.rs carve-outs:

| status | baseline | final | Δ |
|---|---|---|---|
| TRUSTED      | 79  | 53  | **−26** |
| FAILING      | 71  | 95  | **+24** |
| PROVEN       | 55  | 58  | +3 |
| DEAD_FAILING | 17  | 21  | +4 |
| DEAD_PROVEN  | 10  | 11  | +1 |
| total        | 232 | 238 | +6 |
| **vacuous**  | 0   | **0** | — |
| **gate**     | pass | **pass** | — |

SILENT = 0, ICE_MASKED = 0, BLOCKED_DEP_MASKED = 0. The headline trust-reduction
movement is **TRUSTED −26 → FAILING +24 / PROVEN +3** (trusted obligations became
honest frontier or verified outright). Trusted *attributes* 216 → 158 (58 removed
net, 3 re-added: 2 latent-ICE + service_interrupt; plus io.rs board writer).
