# Panic-site ledger — master nrf52840dk

Authoritative per-site account of every panic call site in the **master**
nrf52840dk release ELF. Per-site machine-readable truth in `panic_ledger.csv`.

Built: 2026-05-22, against master commit `104a47788`.

## Canonical roundup — 343 sites, by marker form + by flavor

After this session's precondition-conversion pass and the
fn-level-marker conversion for panic-in-callee cases, every panic site
has a flavor + a comment describing it. Roughly half also carry an
actual assertion (real precondition, intentional `assert(false)`
"prove-unreachable", or upstream `flux_support::assume` providing
coverage).

### By marker form (sums to 343)

```
A. Has marker + real precondition       142
   `flux_support::assert(<actual condition>)` — Flux can discharge directly

B. Has marker + intentional assert(false) 12
   `{ flux_support::assert(false); panic!() }` — "prove unreachable" sentinel

C. Has marker + upstream assume/cell-context  5
   Coverage via flux_support::assume() above the panic-emitting op,
   not via a per-site assert below the marker

D. Comment-only marker (no assert)       148
   `// FLUX-TODO addr=... flavor=...` documenting the panic without an
   assertion. Includes:
      ↳ FLUX-TODO-FN-LEVEL  22  (panic-in-callee, fn-level only)
      ↳ FLUX-TODO-BLOCKED    8  (insertion-point hazards)
      ↳ plain FLUX-TODO    118  (apply-script comments documenting the
                                  panic without a per-site precondition)

E. No user source to annotate (defensible) 18
   stdlib helpers (12) + compiler-generated wrappers (6, depth=1 deferred)

F. Removed by branch refactor               4

G. Marker-at-caller (macro / dyn dispatched) 2
   panic line is the macro definition; markers live at expansion sites

H. Monomorph-at-caller                      4
   misattributed monomorph; marker at user-callable entry point

Σ                                         343  ✓
```

Sites with an actual assertion: A + B + C = **159**.
Sites documented without an assertion: D + E + F + G + H = **184**.

### By panic flavor (sums to 343)

```
explicit_panic          124   panic!/unreachable!/unimplemented!
unwrap_option            75   Option::unwrap, OptionalCell
bounds                   65   arr[i] panic_bounds_check
slice_end                37   arr[..hi] where hi > len
slice_order              21   arr[lo..hi] where lo > hi
optional_cell_unwrap      7   OptionalCell::unwrap_or_panic
assert                    4   assert_eq!/assert_ne!
div_by_zero               3
rem_by_zero               3
unwrap_result             2   Result::unwrap
slice_start               1
unwind                    1   rust_begin_unwind

Σ                       343  ✓
```

## Final distribution — 343 panic sites, 3 active buckets + 0 outstanding

```
A. Has annotation in source              321
   ↳ marked (FLUX-TODO / FLUX-OPT / BLOCKED)         315
   ↳ monomorph-at-caller (marker at user-caller)       4
   ↳ marker-at-caller (macro-def, marker at caller)    2

B. No user source to annotate             18
   ↳ singleton stdlib helper (in /rustc/)             12
   ↳ singleton compiler-gen wrapper (depth=1 deferred) 6

D. Addressed by refactor (panic removed)   4
   ↳ refactored away on branch                         4

C. Still needs annotation                  0   ← DONE
   ↳ all 24 no-line sites recovered this turn

Σ                                        343 ✓
```

**Session arc**: started at A=249 B=18 C=76; the 11-site apply pass → A=284 B=18 C=41; the 17-row content-match diff → A=297 B=18 D=4 C=24; the 24-row no-line interactive pass (this turn) → **A=321 B=18 D=4 C=0**.

## Confidence distribution within A (321)

Of the 315 `marked` rows: 
- 233 high confidence (marker within ±6 lines, exact match)
- 67 medium confidence (marker at +6 to +30 lines due to inserted annotations above, OR pinpointed via content-match across refactor)
- **15 low confidence** (fn-entry markers for sites where LTO inlined the panic and we couldn't pin a specific line within the fn)

### Hand-read audit of the 15 LOW rows — none tightenable

Walked through each of the 15 LOW rows looking for a specific
panic-emitting expression in the fn body. **Result: 0 of 15 could be
cleanly tightened.** Patterns:

1. **No panic-likely expression in fn body** (most common):
   adc.rs sample_ready, samples_ready; ble_advertising_driver command;
   kv_driver command; spi_controller SyscallDriver command. The
   explicit_panic flavor must come from a helper inlined via LTO from
   kernel/grant/process code that the dispatcher fns touch.

2. **Many candidates, no way to disambiguate**: sixlowpan_compression
   mass-refactor (decompress + decompress_ext_hdr); mx25r6435f
   read_write_done (state-machine with 18 candidate arr[i] ops);
   i2c_master_slave_driver command (7 candidate index ops).

3. **Visible candidates are flavor-mismatched**: button.rs command has
   `pins[data]` (bounds) but the missing panic is explicit_panic; aes.rs
   crypt's bounds address sits in a different code region than the
   visible bounds ops in copy_plaintext (which already have their own
   addrs).

The fn-entry markers are honest as "panic somewhere in this fn body."
Tightening these would require either: DWARF `.debug_line` parsing with
finer granularity, disassembly walking from each address with register
analysis, or rebuilding with `[profile.release] debug=true` and
re-running addr2line.

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

## D. Addressed by refactor — panic removed (4)

These master-binary sites have been refactored out on this branch. The panic
doesn't exist in the branch binary at all. Distinction from B: B is "no user
source ever existed to annotate" (compiler code); D is "user source existed
in master, branch eliminated it." Different defenses.

| addr | master file:line | what the panic was | branch disposition |
|---|---|---|---|
| 0x1f66c | gpio.rs:144 (bounds) | `pins[pin_num]` bounds check in `GPIO::fired()` callback | replaced with `unsafe { pins.get_unchecked(pin_num as usize) }` (branch line 156-158); runtime bounds check eliminated, precondition taken on faith |
| 0xbb7c  | stream.rs:269 (bounds) | `buf[i] = *b` inside `encode_bytes_be` reverse-iter loop | branch rewrote the body as a while-loop (to dodge Iterator extern_spec conflicts). NOTE: the while-loop introduces new bounds checks (`buf[i]`, `bs[bs.len()-1-i]`) that are unannotated; a fresh branch survey would surface those as new sites |
| 0xd02c  | udp/driver.rs:543 (unwrap_result) | `retcode.try_into().unwrap()` in UDP `command()` ErrorCode conversion | branch redesigned UDP port-binding logic end-to-end; this match arm doesn't exist |
| 0x18372 | tickv.rs:1120 (explicit_panic) | `_ => unreachable!()` in `RubbishState` garbage-collection state machine | branch consolidated state-machine logic; master had 7 `unreachable!()` in this file, branch has 1 (line 264) corresponding to a different master site |

(Note: 0xadac was originally classified here but turned out to be marked at a
refactored location — operation moved to `decompress_ext_hdr` at branch line
661 with a real precondition assertion at line 660.)

## C. Still needs annotation (0)

All 24 previously-no-line sites are now marked. Distribution of how they
landed:

- **1 HIGH**: `0x724e` ieee802154/driver.rs:934 — pinpointed at the single
  bounds expression (`cfg[0].get()`).
- **1 MEDIUM-pinpointed**: `0x1608c` tickv.rs:224 — covers both `assert_ne!`
  macros (my earlier heuristic missed `assert_ne!` because it only checked for
  `assert!`).
- **8 MEDIUM**: marker at the fn-entry (or both fn-entries where ambiguous).
  These had multi-candidate flavor matches in the fn body; user chose fn-entry
  precision for each.
- **15 LOW**: fn-entry markers for sites where no panic expression is visible
  in the fn body (LTO inlined the helper). 4 of these had additional tool-bug
  recovery work (survey misattributed inner_file or fn-name parser broke):
  - `0xb280` decompress_iid_context (sixlowpan_compression.rs:1390)
  - `0xb4fc` decompress_udp_ports (sixlowpan_compression.rs:1470)
  - `0x9aa4` Console::transmitted_buffer (console.rs:330)
  - `0x1d54` Kernel::kernel_loop generic (kernel.rs:432)

Plus 3 sites for the master-`decompress` mass-refactor (covered by 2 fn-entry
markers at branch decompress + decompress_ext_hdr): `0xadf8`, `0xae28`, `0xae86`.

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
