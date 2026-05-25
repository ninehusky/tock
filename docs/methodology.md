This document contains reproducibility information for understanding (1) how the panicking
instructions inside the Tock codebase are enumerated and annotated, and (2) how those
annotations are grouped into categories which capture where the overall no-panic proof
stands (as of May 24 2026).

The work targets the `nrf52840dk` release binary and the 10 Flux-enabled crates it pulls
in: `kernel`, `tock-cells`, `tickv`, `cortex-m`, `cortex-v7m`, `capsules-core`,
`capsules-extra`, `nrf52`, `nrf52840`, `nrf5x`.

# Phase 1 -- Annotating panicking instructions

## Part 1: Enumerating panicking instructions

A panicking *instruction* is a `bl` (branch-with-link) in the release binary that targets
one of Rust's panic entry points (`core::panicking::*`, slice-index/bounds, unwrap, etc.).
Goal #1 is to find every such instruction.
The canonical end-to-end tool is `tools/panic_survey.py`, which builds Tock on the board,
and emits a JSON of every panic call-site with its classification (slice-index, unwrap, etc.).

Run:
```bash
cd tools
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt # TODO(andrew): actually make this so people have pandas.

# Full run: builds the board, disassembles, scans, maps, classifies, emits JSON.
.venv/bin/python3 panic_survey.py
# If you already have the release ELF and don't want to rebuild:
.venv/bin/python3 panic_survey.py --skip-build --elf <path-to-release-elf>
```

The output of the above step is `panic_survey.json`, which lists 343 panicking
instructions in the release binary.

## Part 2: Mapping panic instructions to source code

LTO is strong in Tock's release build, so binary panic instructions are often
inlined far from their source panic site.

`panic_survey.py` resolves this with `addr2line -a -f -C -i` (the `-i` emits the full inline
frame stack, innermost-first) and then selects the frame that actually *owns* the panic. A
record looks like:

```jsonc
{
  "address": "0xfa48",                       // the bl instruction in the binary
  "sink": "core::panicking::panic_fmt",      // the panic entry point it branches to
  "sink_flavor": "explicit_panic",           // explicit_panic | slice_index | unwrap_option | bounds | ...
  "inline_chain": [ {"func","file","line"}, … ],  // addr2line -i stack, innermost-first
  "innermost_frame": { … },                  // chain[0] — often a stdlib helper
  "outermost_frame": { … },                  // top of the inline stack (the asm symbol)
  "effective_frame": {                       // <-- the attribution we use
      "func": "cortexm::unhandled_interrupt",
      "file": "./arch/cortex-m/src/lib.rs", "line": 155 },
  "effective_source": "panic!(\"Unhandled Interrupt…\");",  // the source text at that line
  "module_bucket": "arch", "origin_bucket": "local",
  "enclosing_asm_label": "_RNvCs…_cortexm19unhandled_interrupt"
}
```

The important thing to note from the above is that the area to actually annotate, in user code,
is the `effective_frame` (and `effective_source`) — not the innermost frame, which is often a
useless stdlib helper.

We cannot reliably annotate panics at the source when:
1. **stdlib helpers.** If the innermost frame is a stdlib helper (e.g. `slice_index_len_fail`), the actual panic site is
   the Tock caller that got inlined into it; the helper's source is useless for annotation.
2. **LTO line loss.** Without release-level debug info, LTO sometimes collapses the exact line,
  leaving only the enclosing function; those sites fall back to function-level attribution and are annotated with a
  `FLUX-TODO-FN-LEVEL` breadcrumb instead of a precise line.

The breakdown of the 349 panic sites:

The 349 is a count of panic *instructions* (`bl`s); this table reconciles it down to the
live obligation set. Every figure is independently checkable — instruction and source-site
counts come from `panic_survey.json`, the obligation count from grepping
`flux_support::assert` across the 10 crates.

| | count | reconciliation |
|---|--:|---|
| Panic instructions (`bl`s in the release binary) | **349** | one record per `bl` in `panic_survey.json` |
| − monomorphization duplicates | −92 | generic code is emitted per capsule, so many `bl`s share one source line — e.g. `Grant::enter`'s re-entrancy panic (`kernel/src/grant.rs:1442`) is emitted as **50** instructions |
| **= distinct source panic sites** | **257** | unique `effective_frame` `file:line` |
| ↳ precise Tock sites | 225 | a `file:line` we can annotate directly |
| ↳ stdlib-helper sites | 17 | panic inside an inlined stdlib helper — no Tock line; the obligation belongs to the inlined caller |
| ↳ function-level only | 15 | exact line lost to LTO → annotated on the enclosing fn (`FLUX-TODO-FN-LEVEL`) |
| **live `flux_support::assert` obligations** | **200** | one precondition authored per precise site that admits one; the remainder are explicit `panic!`/`unreachable!`/hardware-trust panics not expressible as a precondition |

Reviewer checks: `92 + 257 = 349`, and `225 + 17 + 15 = 257`. The biggest dedup fan-ins are
`grant.rs:1442` (50 instructions → 1 line), `leasable_buffer.rs:372` (8),
`optional_cell.rs:202` (5), `take_cell.rs:127` (4).

The flavor / crate cuts below are at the **instruction** level (they sum to 349).

## Finding every panic: the `FLUX-` breadcrumbs

Every triaged panic site carries a `// FLUX-…` comment at (or just above) its source line, so
the whole set is greppable. **These are per-panic-site status markers** (keyed to the binary
`addr=`, which ties each back to its `panic_survey.json` record) — *not* the 200 obligations;
their counts track panics, and a *different marker is used per situation*:

```bash
grep -rn 'FLUX-' --include='*.rs' kernel libraries arch capsules chips
```

| marker | situation | count |
|---|---|--:|
| `// FLUX-TODO addr=… line=… flavor=…` | a panic flagged for verification, not yet proven-and-optimizable — most carry an authored `flux_support::assert`, but ~100 do not yet (`reason=…` records the blocker when one can't be authored) | 262 |
| `// FLUX-OPT addr=… line=… flavor=…` | precondition is in place and goes through; the runtime `assert!` can become `assert_unchecked` / `get_unchecked` (provable **and** removable from the binary) | 18 |
| `// FLUX-TODO-FN-LEVEL covers=[addr…] flavor=…` | exact line lost to LTO — one marker on the enclosing fn, covering the listed addresses | 19 |
| `// FLUX-TODO-BLOCKED …` | cannot be proven yet — blocked on a cell invariant, `dyn`-in-sig ICE, re-entrancy, etc. | 7 |

**Markers ≠ obligations** (why 306 markers but 200 obligations): these comments count
*panics*, not authored preconditions, and the two diverge by design — ~100 of the 262
`FLUX-TODO` mark panics with **no** `flux_support::assert` yet (blocked, explicit
`panic!`/`unreachable!`, or not-yet-worked), one precondition can cover several monomorphized
panics, a `FN-LEVEL` marker covers multiple addresses, and stdlib-helper sites have no Tock
line to mark at all (so the marker counts also don't sum to 349). The *reason* a `BLOCKED`
site is stuck is recorded in the ledger `tools/panic_sites.md` with a `blocked_*` tag —
`blocked_cell`, `blocked_reentrancy`, `blocked_dyn`, `blocked_ice`, `blocked_stdlib`,
`blocked_hw_trust`.


## From panic site to obligation (annotation)

Each panic-bearing function is annotated, at the site, with a precondition assertion that
*would* make the panic statically impossible:

```rust
flux_support::assert(<precondition>);   // e.g. flux_support::assert(i < buf.len());
```

tagged with a `FLUX-*` breadcrumb comment. This `flux_support::assert(...)` call **is** the
obligation Flux must discharge. `assert` is declared

```rust
#[flux_rs::sig(fn(x: bool[true]))]
pub const fn assert(_x: bool) {}
```

so its argument must be *statically provable to be true*. After de-duplicating
commented-out and auxiliary asserts, the live obligation set is ~200
`flux_support::assert(...)` sites across the 10 crates.

# Phase 2 — Categorizing: where the no-panic proof stands

The question Phase 2 answers is **not** "does the crate compile under Flux?" — it is, per
obligation, *does Flux genuinely discharge this precondition?* A function passing Flux tells
you nothing on its own: Flux may have skipped the body, trusted it, or had its checking
aborted by a swallowed internal error.

## The negation probe

Because `assert(cond)` emits the obligation "prove `cond`," the obligation
`assert(false)` emits "prove `false`" — which is unprovable. Therefore **`assert(false)`
errors if and only if Flux actually analyzed the body.** This gives a decisive test:

> For an obligation that *passes* in the baseline, flip `assert(cond)` → `assert(false)` and
> re-run Flux. If an error now appears **at that site**, the body was genuinely checked
> (PROVEN). If it stays silent, the body was not checked (SILENT — the original pass was
> vacuous).

This is implemented by `tools/deice_probe.py` (with helpers in `tools/negation_probe.py`).
Per crate it runs `cargo flux clean`, takes a baseline, then classifies each obligation:

| category | meaning |
|---|---|
| **PROVEN** | passes baseline, and flipping to `assert(false)` errors at the site → genuinely discharged (won't panic) |
| **FAILING** | the original `assert(cond)` already errors in baseline → Flux checks it but cannot prove the precondition |
| **DEAD_PROVEN** | an `assert(false)` *sentinel* (a "prove this line is unreachable" obligation) that passes → Flux accepts the site as dead |
| **DEAD_FAILING** | such a sentinel that errors → Flux cannot prove the site dead |
| **TRUSTED_BLOCKED** | the enclosing fn is `#[flux_rs::trusted]` → body intentionally skipped (cell-invariant blocker, ICE-dodge, etc.); not a proof |
| **SILENT** | passes baseline *and* stays silent when flipped to `false` → the body was not analyzed (vacuous pass) |
| **ICE_MASKED** | the run hit an internal compiler error (see below) → verdict untrustworthy |
| **BLOCKED_DEP_MASKED** | a dependency failed to Flux-compile, so this crate's bodies were never checked |

"Discharged" = PROVEN + DEAD_PROVEN. "Genuinely checked" = PROVEN + FAILING + DEAD_*.

### Method details and pitfalls

- **One flip at a time.** Flipping two asserts in one function poisons the result —
  `assert(false)` turns everything after it into dead code, so a second flip reads as silent.
- **The error must be at the site.** A flip can cascade errors elsewhere; only an error
  within the flipped line's range counts.
- **SILENT is ambiguous** between "not checked" and "the line is already proven dead." To
  disambiguate, use the *entry control*: inject `assert(false)` at the function's entry. If
  that errors, the body is checked (so the site's silence means it is dead); if it too is
  silent, the body is genuinely skipped.
- **Trusted detection must scan the whole attribute block**, not a fixed-size window — a long
  `#[flux_rs::trusted(reason = "…")]` string otherwise pushes the attribute out of view and
  the obligation gets mislabeled SILENT.

## ICE-robustness (the critical correctness gate)

A swallowed internal compiler error (ICE) is the trap that makes this measurement lie: when
Flux panics mid-crate, the panic is caught per-definition but **error emission for the run
becomes unreliable**, so genuinely-checked obligations read as SILENT. Every run is therefore
gated on an ICE detector (`has_ice`); a run that ICE'd is marked `ICE_MASKED`, never trusted.

The detector must match the *current* panic format — `thread 'rustc' (12345) panicked at …`
(a thread-id sits between the name and "panicked at") plus the diagnostic
`UnsolvedEvar` — otherwise the ICE slips through and silently inflates the SILENT count. Two
ICE families are live in this codebase:

- **`flux-infer/src/infer.rs:427` `UnsolvedEvar`** — array indexing inside a cell closure over
  refined data, e.g. `self.keys.map(|arr| arr[i])`.
- **`flux-infer/src/infer.rs:1034` dyn-predicate `assert_eq`** — `dyn Trait<…>` constructs
  (HIL clients stored as `&dyn …` in an `OptionalCell`/`TakeCell`).

To name the exact function that ICEs (so it can be `#[flux_rs::trusted]`-dodged), run with
Flux's bug-catcher, which prints the offending `def_id` + span:

```bash
FLUXFLAGS="-Fcatch-bugs" cargo flux --keep-going 2>&1 | grep "uncaught panic"
```

A swallowed ICE caused the prior (May 23) report of "39 proven / 102 vacuous": once the
detector was fixed and the ICEing functions trusted so the crate checks cleanly, the
"vacuous/silent" population was found to be ~entirely an ICE artifact, not real verification
gaps.

## State of the proof (as of May 24 2026)

Measured ICE-free over 200 live obligations:

| crate | PROVEN | FAILING | DEAD✓ | DEAD✗ | TRUSTED | dep-masked |
|---|--:|--:|--:|--:|--:|--:|
| capsules-extra | 36 | 13 | 9 | 5 | 34 | 0 |
| kernel | 14 | 0 | 3 | 0 | 1 | 0 |
| tickv | 8 | 0 | 2 | 0 | 0 | 0 |
| capsules-core | 3 | 0 | 3 | 0 | 14 | 0 |
| cortex-m | 1 | 0 | 1 | 0 | 1 | 0 |
| nrf52 | 1 | 10 | 2 | 4 | 2 | 0 |
| nrf52840 | 0 | 0 | 0 | 0 | 0 | 11 |
| tock-cells / cortex-v7m / nrf5x | 0 | 0 | 2 | 0 | 2 | 0 |
| **TOTAL** | **63** | **23** | **22** | **9** | **65** | **11** |

- **85 obligations discharged** (63 won't-panic + 22 proven-unreachable).
- **32 checked-but-FAILING** — the genuine verification frontier (need stronger sigs /
  invariants); concentrated in `nrf52` usbd (10) and `capsules-extra` (13).
- **65 TRUSTED_BLOCKED** — predominantly the two ICE families' perp functions plus
  cell-invariant blockers; unblocking them requires the Flux ICE fixes, not more proof work.
  (Note: several of these *hide* real bounds obligations the ICE-dodge currently suppresses.)
- **0 genuine SILENT, 0 ICE_MASKED** in the final measurement.
- **11 dep-masked** (`nrf52840`), blocked by `nrf52`'s outstanding refinement errors.

## Reproducing Phase 2

```bash
# per-crate census (run from the repo root; deice_probe cds into each crate dir itself)
tools/.venv/bin/python3 tools/deice_probe.py --crates capsules-extra kernel tickv … \
    --out tools/census.json
# skip-shape triage (closure / sig / trait-impl / free-fn / trusted)
tools/.venv/bin/python3 tools/triage_skipped.py
```

When a crate must be temporarily modified to be measurable (e.g. reverting an in-progress
annotation that blocks compilation, or trusting an ICEing function), do **not** use
`git stash push/pop` — it is a global stack and races across runs. Either revert a single
file with `git checkout HEAD -- <file>` plus a saved `git diff` patch, or (preferred) run the
probe inside an isolated `git worktree` created at a `git stash create` snapshot, so the live
checkout is never touched.

# Appendix

## What does `panic_survey.py` do?

The pipeline is:

1. `make release` in `boards/nordic/nrf52840dk` (skippable with `--skip-build`), producing
   the ELF at `…/target/thumbv7em-none-eabihf/release/nrf52840dk`.
2. `gobjdump -d <ELF>` into a `.dis` file (`--objdump` selects the disassembler; on this
   machine it is `gobjdump`, not `arm-none-eabi-objdump`).
3. Scan the disassembly for every `bl` whose target is a panic-sink symbol listed in
   `tools/symbols.txt`.
4. Batch `addr2line -a -f -C -i` over every call-site address. The `-i` is load-bearing:
   LTO inlines almost every panic call, so the *enclosing source function* is only
   recoverable from the inlined frames.
5. Classify each site (sink flavor, module/origin bucket, grant re-entrancy, Flux-blocker
   hint) and emit `tools/panic_survey.json`.
