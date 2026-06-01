# Session handoff — why are the 5 step-2 SILENT obligations' bodies skipped?

## ✅ RESOLVED 2026-06-01 — gate=PASS, vacuous=0 (all 5 SILENTs cleared)
Final census `tools/invariant2_report.json` (run `tools/inv2_run_2026-06-01_final.log`):
**232 obligations — discharged 77 / frontier 155 / vacuous 0; gate=pass.**
by_status: PROVEN 54, DEAD_PROVEN 23, FAILING 72, DEAD_FAILING 17, TRUSTED 66. **0**
SILENT/ICE/BLOCKED/NOT_RUN/TIMEOUT. The 5 formerly-SILENT sites:
#1 tickv read_region → **TRUSTED**, #2 nrf52840 service_interrupt → **TRUSTED**,
#3 board io.rs Writer::write → **TRUSTED** (dep-masked but statically trusted),
#4 kv_driver command → **PROVEN**, #5 kv_store_permissions new_from_buf → **FAILING**.
Tree changes (uncommitted): io.rs + tickv.rs + interrupt_service.rs (`#[flux_rs::trusted]`),
3 Cargo.toml (capsules-extra include `def:new_from_buf`+`span:`, nrf52840 & board `flux-rs`
dep), tools/deice_probe.py (board target plumbing), tools/check_invariant2.py (dep-mask
honors static trust). Full detail in the "FIXES APPLIED" / "#3 FINAL RESOLUTION" sections.


**Date opened:** 2026-06-01
**Predecessor context:** step 2 (`invariant 2`) is built and a clean full census exists.
This handoff is scoped to ONE question: *why does Flux not analyze the bodies of the
5 `SILENT` obligations, and how do we fix each so the no-vacuity gate goes green?*

The user's prior suspicion was a swallowed ICE. **Current evidence points away from an
ICE and toward trait-impl-method skipping + two genuine include gaps** — but ruling out a
format-evading ICE is step 1 below (it is the historical trap that makes checked bodies
read as SILENT; see `project_proven_count_soundness`).

## Where step 2 stands (so this doc is self-contained)

- Tool: `tools/check_invariant2.py` (spec `tools/invariant_two.md`). Reads
  `tools/invariant1_report.json`, probes every covered obligation, writes
  `tools/invariant2_report.json`, gates on no-vacuity.
- Final census (run 4, 2026-05-31): **238 obligations — 76 discharged / 157 frontier /
  5 vacuous; gate FAIL on the 5 SILENT. 0 ICE_MASKED, 0 BLOCKED_DEP_MASKED.**
- How SILENT is computed:
  - **precise** obligation (has a paired `flux_support::assert`): flip `assert(cond)` →
    `assert(false)`; if Flux stays silent at the site, the body was not analyzed → SILENT.
  - **fn-level** obligation (NO paired assert): the probe injects
    `flux_support::assert(false);` immediately after the function's opening `{` (the
    *entry control*), runs Flux, and if no error appears within the function's line span,
    the body was not analyzed → SILENT. (`check_invariant2.py`, "fn-level obligations
    (entry control)" block.)
- Full reproduce of one verdict: see the probe's per-crate logs in
  `tools/invariant2_logs/<crate>.baseline.log`.

## The 5 SILENT sites (the entire gate-blocking set)

| # | site | kind | enclosing construct | in `include`? | leading hypothesis |
|---|------|------|---------------------|---------------|--------------------|
| 1 | `capsules/extra/src/tickv.rs:248` `read_region` (assert: `flash_read_buffer.is_some()`) | precise | `impl tickv::FlashController for TickFSFlashCtrl` — **external trait** | yes (`src/tickv.rs`) | external-trait method body skipped. Log: `read_region … not included when checking external crate`. Marker also notes `blocked-cell` (TakeCell invariant). |
| 2 | `chips/nrf52840/src/interrupt_service.rs:43` `service_interrupt` | fn-level | `impl kernel::platform::chip::InterruptService for Nrf52840DefaultPeripherals` | yes (`def:service_interrupt`) | trait-impl method body skipped despite `def:` include |
| 3 | `boards/nordic/nrf52840dk/src/io.rs:79` (assert: `write_position < buffer.len()`) | precise | `impl IoWrite for Writer`, inside a `match` arm of `fn write` | yes (`src/io.rs`) | trait-impl method body skipped |
| 4 | `capsules/extra/src/kv_driver.rs:471` `command` | fn-level | `impl …SyscallDriver for KVStoreDriver` | **NO** (`def:command` deliberately excluded) | out-of-include. NB: re-adding `def:command` matches ~89 `command*` fns crate-wide and trips the dyn-predicate ICE (`infer.rs:1034`) — see the long comment in `capsules/extra/Cargo.toml`. |
| 5 | `capsules/extra/src/kv_store_permissions.rs:59` `new_from_buf` | fn-level | `impl KeyHeader` — **inherent** method | **NO** | out-of-include. Simplest case — likely flips to FAILING/PROVEN just by including it. |

**Pattern:** #1–#3 are trait-impl methods that *are* in include yet still SILENT → the
prime suspect is Flux skipping trait-impl method bodies (consistent with the
`triage_skipped.py` "TRAIT_IMPL" bucket and `project_proven_count_soundness`). #4–#5 are
genuinely out-of-include.

## DEAD_PROVEN AUDIT (2026-06-01) — entry-control added; 3 false proofs found
User asked "can we actually trust the DEAD_PROVEN?" Answer: **NO, not as a group.** Added
entry-control confirmation to `check_invariant2.py` (sentinel `assert(false)` that's silent
at baseline → inject `assert(false)` at the enclosing fn's body open + re-run; error in fn
span ⇒ body analyzed ⇒ genuine DEAD_PROVEN; still silent ⇒ unchecked ⇒ new **DEAD_SILENT**
(VACUOUS, gated)). A sentinel inside a `#[flux_rs::trusted]` fn → reclassified TRUSTED.

**The 23 original DEAD_PROVEN split: 10 confirmed DEAD_PROVEN + 10 TRUSTED (sentinel in a
trusted fn — "silent" only because trusted, not a proof) + 3 DEAD_SILENT (vacuous).**
The 3 vacuous: `cortexm::unhandled_interrupt` (arch/cortex-m/src/lib.rs) and
`cortexv7m::hard_fault_handler_arm_v7m_kernel` (×2, arch/cortex-v7m/src/lib.rs). **Cause:
both are `#[cfg(all(target_arch="arm",target_os="none"))]` inline-asm/extern-"C" fault
handlers — the host-target probe cfg's them out (no body to check) AND the asm is
unanalyzable. `unhandled_interrupt` even had `#[flux_rs::sig(fn() requires false)]`, a
declared-dead contract that's vacuous for a hardware-invoked handler (no Rust caller to
discharge `false`).** Fix (user's "trust the function"): `#[flux_rs::trusted]` on both fns
→ 3 sentinels → TRUSTED. Re-run `tools/inv2_run_2026-06-01_final2.log`.

## RESOLVED DIAGNOSIS (2026-06-01) — ICE ruled out; 4 distinct named causes

**ICE hypothesis is dead.** `FLUXFLAGS=-Fcatch-bugs cargo flux --keep-going` on
`capsules-extra` and `nrf52840` → **0** ICE markers (`tools/ice_probe_*.log`). Both also
confirm `kernel` itself is ICE-clean under catch-bugs (the runs stop at kernel's 16
step-0/1 FAILING asserts — dependency masking, expected; the census unmasks topologically).
Every SILENT has a *loudly-emitted* cause below, not a swallowed panic.

| # | site | confirmed cause | log evidence |
|---|------|-----------------|--------------|
| 1 | tickv.rs:248 `read_region` (precise) | **external-trait skip.** Method impls the external trait `tickv::FlashController` (lib `libraries/tickv`); Flux won't check the body unless the trait's defining file is on the extern-spec/include surface. Underlying assert is the `blocked-cell` TakeCell invariant → even if checked, FAILING. Sibling `write` is already `#[flux_rs::trusted]`. | `capsules-extra.baseline.log:458` `E0999 … read_region … not included when checking external crate` → help points at `libraries/tickv/src/flash_controller.rs:73` |
| 2 | interrupt_service.rs:43 `service_interrupt` (fn-level) | **external-struct skip.** `Nrf52840DefaultPeripherals` has field `nrf52: nrf52::chip::Nrf52DefaultPeripherals` (external crate `nrf52`); the struct can't be resolved → all its impls (incl. `service_interrupt`) skipped. | `nrf52840.baseline.log:211` `E0999 … Nrf52DefaultPeripherals … not included when checking external crate` → help points at `chips/nrf52/src/chip.rs:31` |
| 3 | io.rs:79 `Writer::write` (precise) | **board crate never flux-builds on host.** `cargo flux` fails at the `cargo metadata` stage: board `.cargo/config.toml` sets `target = thumbv7em-none-eabi` + `cargo/tock_flags.toml` forces `linker-flavor=ld.lld`, which is rejected by the host (aarch64-apple-darwin) toolchain metadata probe. The whole crate is unchecked — not a per-method skip. Needs `--target thumbv7em-none-eabi` plumbing to flux-check at all. | `nrf52840dk.baseline.log:1` `Failed to run cargo-flux … cargo metadata exited with an error` / `:91` `error: linker flavor ld.lld is incompatible with the current target` |
| 4 | kv_driver.rs:471 `command` (fn-level) | **out-of-include; re-include trips a KNOWN ICE.** `def:command` is excluded (see long comment in `capsules/extra/Cargo.toml`) because the unanchored substring matches ~89 `command*` fns, ~30 of which hit the dyn-predicate ICE `infer.rs:1034`. This ICE is documented, not swallowed. | `capsules/extra/Cargo.toml` exclusion comment |
| 5 | kv_store_permissions.rs:59 `new_from_buf` (fn-level) | **pure out-of-include.** `src/kv_store_permissions.rs` is not in capsules/extra's include list at all; inherent method, no external trait. Adding the file/def → body checked. Assert is `slice_end` (`buf[1..5]`/`buf[5..9]` need `buf.len()>=9`) → likely FAILING (honest frontier), which clears the gate. | not present in `capsules/extra/Cargo.toml` include |

**So the gate-blockers are: 2 external-item-skips (#1,#2), 1 host-untestable board crate
(#3), and 2 include-scope gaps (#4 ICE-guarded, #5 clean).** None is vacuity-by-hidden-ICE.

Resolution per site is a user policy decision (extern-spec investment vs `#[flux_rs::trusted]`
carve-out vs document-accepted) — recorded after the user chooses; see end of file.

## FIXES APPLIED (2026-06-01) — user-chosen resolutions

User decisions: #1/#2 → trusted carve-out; #3 → plumb embedded target; #4 →
span-anchored include; #5 → add include.

- **#5** `capsules/extra/Cargo.toml`: added `"def:new_from_buf"` (unique substring → only
  KeyHeader::new_from_buf). Expect SILENT → FAILING (slice_end needs `buf.len()>=9`).
- **#1** `capsules/extra/src/tickv.rs`: `#[flux_rs::trusted(reason=…external-trait skip…)]`
  on `read_region` (capsules-extra already deps flux-rs; matches sibling `write`). → TRUSTED.
- **#2** `chips/nrf52840/src/interrupt_service.rs`: `#[flux_rs::trusted(reason=…external-struct
  skip…)]` on `service_interrupt`. **Required adding `flux-rs` dep to
  `chips/nrf52840/Cargo.toml`** — the crate had only `flux_support`, so the attr would be
  unresolved (E0433) when nrf52840 is built as a *dependency* (the board). flux-rs is
  already in the board's dep tree, so benign. → TRUSTED (direct probe).
- **#3** `tools/deice_probe.py`: board crates (`crate_dir.startswith("boards/")`) now run
  `cargo flux --target thumbv7em-none-eabi` with `RUSTFLAGS` that strip the `ld.lld`
  linker flags (keeping `cfg_tock_buildflagssentinel`). Confirmed this gets the board past
  the `cargo metadata` failure and into the flux pipeline (`tools/board_spike2.log`).
- **#4** `capsules/extra/Cargo.toml`: added `"span:src/kv_driver.rs:472:5"` (pins ONLY
  KVStoreDriver::command, avoiding the 89-sibling `def:command` ICE).

**`span:` VALIDATED working** (memory's "span: buggy" is outdated for this flux build).
Standalone test on `/Users/andrew/research/flux_sig_repro`: `include=["span:src/lib.rs:29:5"]`
→ "2 checked; 58 trusted", error fires at exactly lib.rs:29 (the one targeted fn), all
other fns stay trusted. So span matching is per-fn and precise.

**ICE ruled out for real** (above): `-Fcatch-bugs` on capsules-extra + nrf52840 → 0 markers.

### RESULT OF FULL RUN (2026-06-01) — 4 of 5 fixed; board hit a real wall

Full pipeline (`tools/inv2_run_2026-06-01.log`, report `tools/invariant2_report.json`):
**discharged=77, frontier=154, vacuous=1, gate=fail.** The 4 non-board SILENTs are all
fixed and gate-clearing:
- **#1 read_region → TRUSTED**, **#2 service_interrupt → TRUSTED**,
  **#4 command → PROVEN** (span worked, the lone fn did NOT ICE),
  **#5 new_from_buf → FAILING** (body now checked).

**#3 board io.rs:79 → BLOCKED_DEP_MASKED (the last gate-blocker).** The embedded-target
plumbing SUCCEEDED — the board now compiles (build-std + flux-rs) and flux-checks. But the
board sits atop the ENTIRE verified stack, and `cargo flux` checks every flux-enabled
*dependency*. Those deps mask the board with errors that the unmask machinery **cannot**
clear:
1. **external-crate E0999s** — capsules-extra's `tickv::FlashController` impl methods
   (`read_region`/`erase_region`/`write` "not included when checking external crate") and
   nrf52840's `nrf52::Nrf52DefaultPeripherals` struct ref. Per-fn / whole-file
   `#[flux_rs::trusted]` does NOT clear these (the E0999 fires before/independent of body
   trust — confirmed: read_region is trusted yet still E0999 in the board log).
2. **genuine FAILING obligations** in every dep (the frontier).

**Disabling flux on the deps does NOT work either** (tested, reverted): a spec-bearing crate
(tickv, tock-cells, kernel, …) compiled under `cargo flux` with `enabled=false` hits
**E0433 "unresolved crate `flux_tool`"** — its native `#[flux_rs::sig]`/`#[flux_rs::trusted]`
macros expand under the flux cfg but the flux *driver* only registers `flux_tool` for crates
it actually checks. So `disabled_crate` is only viable for attr-free crates (nrf5x).

**Conclusion:** clearing #3 by making the board fully flux-compile requires resolving every
external-crate E0999 in its dependency closure (extern-spec investment across
capsules-extra↔tickv and nrf52840↔nrf52) — declined as a rabbit hole.

### #3 FINAL RESOLUTION (user choice) — trust Writer::write + honor static trust
Minimal, reproducible path chosen by the user ("just trust the function in io.rs"):
1. **`boards/nordic/nrf52840dk/src/io.rs`**: `#[flux_rs::trusted(reason=…)]` on
   `Writer::write`. Declared carve-out for the panic-path debug writer.
2. **`boards/nordic/nrf52840dk/Cargo.toml`**: added `flux-rs` dep so the attr resolves in
   normal (non-flux) builds (board had only flux_support — same fix as nrf52840).
3. **`check_invariant2.py`**: the dep-mask branch no longer blindly stamps
   `BLOCKED_DEP_MASKED`. New `emit_dep_masked` / `_statically_trusted`: a dep-masked
   obligation whose enclosing fn carries `#[flux_rs::trusted]` in source is reclassified
   **TRUSTED** (a declared frontier carve-out, sound without running flux). General
   improvement, not a board hack.

Board-only re-probe confirmed: **io.rs:79 → TRUSTED** (`probe: {baseline: dep_masked,
enclosing_fn: trusted, dep: tickv}`), board gate=pass. Full re-run:
`tools/inv2_run_2026-06-01_final.log`.

`deice_probe.py` board-target plumbing KEPT (correct + valuable). The disable-deps
experiment was reverted (E0433: flux-disabled spec-bearing deps can't resolve `flux_tool`).

### (superseded) earlier risk note — the board (#3) needs the whole dep tree to compile as deps
nrf52840 has a *struct-level* E0999 (`Nrf52DefaultPeripherals not included`, line 13). The
unmask's per-fn/whole-file trust only trusts erroring **fns**, so it may not clear a
struct-level error → nrf52840 "could not compile" as a board dep → board obligation
`BLOCKED_DEP_MASKED` (still gate-failing). Adding flux-rs flipped nrf52840 from the
`disabled_crate` unmask path to `per_fn`. **If the run shows the board BLOCKED_DEP_MASKED,
the fix is to force nrf52840 onto `disabled_crate` (now SAFE because flux-rs is a real dep,
so the permanent `#[flux_rs::trusted]` attr still resolves while flux skips the crate).**
Full run: `tools/inv2_run_2026-06-01.log`, report `tools/invariant2_report.json`.

## Probe plan for next session (in order)

1. **Rule out a format-evading ICE first** (cheap, decisive). For each of
   `capsules-extra`, `nrf52840`:
   ```
   cd <crate> && FLUXFLAGS="-Fcatch-bugs" cargo flux --keep-going 2>&1 | grep -i "uncaught panic\|internal compiler error\|UnsolvedEvar"
   ```
   Also confirm `negation_probe.ICE_MARKERS` regexes match the *current* rustc/flux panic
   banner (the May-2024 bug was a thread-id between `'rustc'` and `panicked at`). Baseline
   logs currently show **0** ICE markers, so if `-Fcatch-bugs` is also clean, the ICE
   hypothesis is dead and the cause is skipping/scoping.

2. **Cheapest confirmation that it's scope, not depth — site #5.** Add
   `def:new_from_buf` (or `src/kv_store_permissions.rs`) to `capsules/extra`'s `include`,
   re-run step 0→1→2 (or just re-probe capsules-extra). If `new_from_buf` flips
   SILENT → FAILING/PROVEN, that proves the SILENT was pure include scope. Expected easy win.

3. **Test the trait-impl-skip hypothesis directly — sites #1–#3.** Inject
   `flux_support::assert(false)` as the first statement of `read_region` /
   `service_interrupt` / `Writer::write` and run Flux scoped to the crate. The census
   already did this (→ SILENT), so re-confirm by hand and then test the fix hypotheses:
   - Does Flux check the body if the method is moved out of the trait impl into an inherent
     `impl` (temporary spike)? If yes → trait-impl-skip confirmed.
   - For #1 (external trait `tickv::FlashController`), the log message
     `… not included when checking external crate` is a specific, nameable cause: the
     external trait's method isn't in Flux's extern-spec/include surface. Investigate
     `flux_support/extern_specs/` and whether `tickv::FlashController`'s methods need an
     extern spec entry. There is a prior repro at `tools/flux_trait_impl_repro/`
     (FlashController-shaped) — reuse it.

4. **Site #4 is the hard one** — re-including `command` reintroduces the dyn-predicate ICE.
   Options to evaluate: (a) a `span:`-anchored include for just `KVStoreDriver::command`
   instead of the unanchored `def:command`; (b) dodge the ICE'ing sibling `command*` fns
   with `#[flux_rs::trusted]` and include only the one we need. Confirm `span:` actually
   works first — memory `feedback_flux_include_filter_quirks` says `span:` was buggy.

## Decisions for the user when resolving each SILENT (per `invariant_two.md`)

Each SILENT is resolved by making the body genuinely analyzed (add to `include` / fix the
extern spec / refactor), OR by an explicit `#[flux_rs::trusted]` with a reason (which
reclassifies it `TRUSTED`, a declared carve-out — not vacuity), OR documenting it as
accepted. Do NOT weaken/assume to silence it.

## Key references

- Tool + spec: `tools/check_invariant2.py`, `tools/invariant_two.md`.
- Report (joins step 1 on `addr`): `tools/invariant2_report.json`; per-crate logs:
  `tools/invariant2_logs/`.
- Include lists: `capsules/extra/Cargo.toml` (note the long `def:command` exclusion
  comment), `chips/nrf52840/Cargo.toml`, `boards/nordic/nrf52840dk/Cargo.toml`.
- Relevant memory: `project_proven_count_soundness` (ICE-vs-skip history, trait-impl-skip
  is real-on-clean-crates), `feedback_flux_include_filter_quirks` (`def:` is unanchored
  substring, `span:` buggy), `feedback_trusted_impl_vs_trusted`,
  `feedback_flux_no_strengthen_trait_precondition` (Index trait extern-spec gap),
  `feedback_assume_is_runtime_panic` (don't silence with assume).
- Prior trait-impl repro: `tools/flux_trait_impl_repro/`.

## Caveats carried in

- ~~The 23 `DEAD_PROVEN` are not yet entry-control-confirmed~~ **DONE 2026-06-01.**
  `check_invariant2.py` now entry-control-confirms every DEAD_PROVEN candidate: for a
  sentinel `assert(false)` that is silent at baseline, it injects `assert(false)` at the
  enclosing fn's body open and re-runs — error in fn span ⇒ body analyzed ⇒ genuine
  `DEAD_PROVEN`; still silent ⇒ body unchecked ⇒ new **`DEAD_SILENT`** status (added to the
  VACUOUS set, so it now FAILS the gate). A sentinel inside a `#[flux_rs::trusted]` fn is
  reclassified `TRUSTED` (declared carve-out), not DEAD_PROVEN. See the `site["inner"]==
  "false"` block in probe_crate's precise loop. Re-run: `tools/inv2_run_2026-06-01_deadcheck.log`.
- `check_invariant2.py` and `invariant2_report.json` are untracked; `invariant_two.md`
  appears gitignored (commit with `git add -f` if desired). Nothing is committed yet.
- The tool does NOT port deice's `SKIPPED_SCAFFOLDING` carve-out; framer/ble_radio/driver
  obligations are probed normally.
