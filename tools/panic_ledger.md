# Panic-site ledger — master nrf52840dk

Authoritative per-site account of every panic call site in the **master**
nrf52840dk release ELF. Per-site machine-readable truth in `panic_ledger.csv`.

Built: 2026-05-22, against master commit `104a47788`.

## Final distribution — 343 panic sites, 4 plain-English buckets

```
A. Has annotation in source              297
   ↳ marked (FLUX-TODO / FLUX-OPT / BLOCKED)         291
   ↳ monomorph-at-caller (marker at user-caller)       4
   ↳ marker-at-caller (macro-def, marker at caller)    2

B. No user source to annotate             18
   ↳ singleton stdlib helper (in /rustc/)             12
   ↳ singleton compiler-gen wrapper (depth=1 deferred) 6

D. Addressed by refactor (panic removed)   4
   ↳ refactored away on branch                         4

C. Still needs annotation                 24
   ↳ no-line (file known, line murky)                 24

Σ                                        343 ✓
```

**Session arc**: started at A=249 B=18 C=76; after the 11-site apply pass A=284 B=18 C=41; after the content-match resolution of the 17 outstanding plus reclassifying 0xadac (which turned out to be marked at a refactored location, not removed), A=297 B=18 D=4 C=24.

## A. Has annotation (296)

**290 — marked**: branch source has a `// FLUX-TODO` / `// FLUX-OPT` /
`// FLUX-TODO-BLOCKED` covering the panic. Most are within ±6 lines of the
master-attributed panic line (high confidence); some are at the same panic
under line shifts up to +30 (medium confidence, resolved by content-match
this turn). The 10 newly-marked this turn are split as 6 line-shift-noise +
2 positional-match (sixlowpan_compression `"Unreachable case"`) + 2 freshly
added (button.rs:203, usbd.rs:2175).

**4 — monomorph-at-caller**: misattributed monomorph annotated at the user-code
caller(s) — UDPDriver::command, AdcDedicated::command, I2CMS::command_complete,
and the `assert_failed::<Option<BulkInState>>` pair in usbd.rs.

**2 — marker-at-caller (macro)**: usbd.rs:56 `internal_err!` macro; the
`panic!()` is inside `macro_rules!`, callers elsewhere in usbd.rs are
annotated at the expansion site.

## B. No user source to annotate (18)

### B1. Stdlib panic helpers (12) — lives in `/rustc/`

`panic_fmt`, `panic`, `panic_bounds_check`, `assert_failed_inner`,
`slice_start_index_len_fail`, `slice_end_index_len_fail`,
`slice_index_order_fail`, `unwrap_failed` (Option), `unwrap_failed` (Result),
`panic_const_div_by_zero`, `panic_const_rem_by_zero`, `rust_begin_unwind`.

Defense: we don't edit `/rustc/`. Every user-code call that *triggers* one of
these is annotated separately in category A.

### B2. Compiler-generated wrappers (6) — deferred to depth=1

| addr | wrapper | direct user-code callers |
|---|---|---|
| 0xb314 | `<[u8; 16] as Index<Range>>::index` | 3 |
| 0x1331e | `<[u8; 500] as Index<RangeTo>>::index` | 14 |
| 0xf5e4 | `<usize>::div_ceil` | 2 |
| 0x96cc | `<Range as SliceIndex<[u8]>>::index_mut` | 39 |
| 0x586e | `<Range as SliceIndex<[u8]>>::index_mut` (second monomorph) | 1 |
| 0x13302 | `<[u8]>::split_at_mut` | 2 |

Wrappers are autogen'd by the compiler (`arr[i]`, `/`, `.split_at_mut()`).
Current survey is **depth=0** (`bl <stdlib_panic_helper>`). Verifying these 6
is deferred to a **depth=1** scan: enumerate `bl <wrapper>` for each and
attribute to user source. That adds **61 new annotation targets**.

### B3. Removed on branch (5) — eliminated via refactor

These master-binary sites have been refactored out on this branch (typically
by switching to `unsafe { get_unchecked() }` which has no runtime bounds
check). The panic doesn't exist in the branch binary at all.

| addr | master file:line | branch disposition |
|---|---|---|
| 0x1f66c | gpio.rs:144 | replaced with `pins.get_unchecked()` (branch line 156-158); commented-out reference at line 158 |
| 0xadac  | sixlowpan_compression.rs:783 | `next_headers[2..2+len].copy_from_slice()` removed; not present in branch source |
| 0xbb7c  | stream.rs:269 | `buf[i] = *b` loop body removed/refactored; not present |
| 0xd02c  | udp/driver.rs:543 | `retcode.try_into().unwrap()` removed/refactored; not present |
| 0x18372 | tickv.rs:1120 | master had 2 `_ => unreachable!()` in this file; branch has only 1 (line 264) corresponding to the other master site |

## C. Still needs annotation (24)

All 24 are `recovered-no-line` rows where the survey couldn't pin a specific
source line. Each has the **file** and **enclosing function** known. The
`confidence` column carries quality:

- **HIGH (1)**: `0x724e` ieee802154/driver.rs — single bounds expression in fn body
- **MEDIUM (8)**: multi-candidate flavor matches in fn body; first match
  picked, alternatives in CSV `reason`
- **LOW (15)**: no flavor-matching expression in fn body; line set to fn entry
  as conservative fallback. Four of these have known tool bugs in fn-name
  resolution (`0xb280, 0xb4fc, 0x9aa4, 0x1d54`).

These are the **next-session interactive pass** — each needs a hand-read of
the function body to land the marker at an exact line.

## What this session left in branch source (committed)

- `tools/panic_ledger.csv` and `tools/panic_ledger.md` (this file)
- 22 new annotations across these files:
  - 4 FLUX-TODO-BLOCKED (process_standard, virtual_aes_ccm:176, sixlowpan_state:483, interrupt_service)
  - 2 FLUX-TODO-BLOCKED (ipv6.rs:504, ipv6.rs:573)
  - 1 FLUX-TODO-BLOCKED (usbd.rs:2175 — this turn)
  - 4 monomorph-at-caller FLUX-TODO (UDPDriver, AdcDedicated, I2CMS, usbd assert pair)
  - 1 FLUX-TODO (button.rs:203 — this turn)
  - 9 FLUX-TODO via apply (alarm, virtual_aes_ccm:238, sixlowpan_compression:739/817, sixlowpan_state:495/1072, chip:123, process_standard:289, take_cell:121)
  - 1 .gitignore exception for tools/panic_ledger.md

Build confirmed clean: `make release` from `boards/nordic/nrf52840dk`.
