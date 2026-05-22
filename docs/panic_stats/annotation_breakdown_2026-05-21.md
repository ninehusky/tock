# Annotation breakdown — 2026-05-21 end of session

Snapshot of where every `panic_survey` row maps in source after the
mechanical annotation pass + audit on `panic-annotations-pass`.

## Source-side counts

| Kind | Count |
|---|---:|
| `flux_support::assert(false);` standalone | 194 |
| `flux_support::assert(false)` inline in match arm | 29 |
| `crate::assert(false)` (flux_support self-ref) | 1 |
| `flux_support::assert(<real condition>)` | 33 |
| **Total `assert(...)` calls in source** | **257** |

| Marker kind | Count |
|---|---:|
| Single-addr markers (`addr=0xNNN`) | 220 |
| Multi-addr marker blocks (`addrs=[...]`) | 18 |
| Total addresses referenced across all markers (with dup refs) | 364 |
| **Unique addresses referenced in markers** | **290** |
| Duplicate references (same addr cited in 2+ markers) | 74 |

The 74 duplicate references are mostly an artifact of the multi-pass
annotation history (an address may be referenced both in a per-site
single-addr marker AND in an enclosing multi-addr block). They're
harmless for the verification check (every annotatable address is
mapped) but worth a deduping pass later.

## Survey-side counts (against the PAP build with chain-walk patch)

| Bucket | Count |
|---|---:|
| Total panic-survey rows | 355 |
| Pure stdlib helpers (cannot directly annotate) | 14 |
| Misattributed user-crate monomorphs (depth=0 lives at user-caller) | 8 |
| No-line attribution (no source line anywhere in the inline chain) | 25 |
| Annotatable (effective_frame in user code, has source line) | 308 |

Note: the committed `tools/panic_survey.json` is older (April-21 build,
342 sites). The numbers above are from the May-21 PAP build via the
chain-walk-patched `panic_survey.py` (see commit `bee46b42d`).

## How counts reconcile (user-facing)

> "I see ~205 `assert(false)` + ~16 real asserts ≈ 221. Where are the
> remaining ones?"

The 257 asserts in source cover the 308 annotatable addresses because
**markers deduplicate by `(file, line)`**:

- `grant.rs:1443` (the reentrancy panic) — **50 binary addresses share
  one marker block + one `assert(false)`**.
- Various multi-line bitwise-OR expressions in `sixlowpan_compression.rs`
  — multiple bounds-check addresses share one marker above the `let`.
- `usbd.rs:1010` — 2 `internal_err!` macro expansions share one marker.
- Several other 2-to-1 / 3-to-1 dedups in `tickv.rs`, `virtual_kv.rs`,
  `aes.rs`, etc. where a method takes multiple `.unwrap()` arguments.

Each marker block has one `assert(false)` (or `assert(real_cond)` for
the 16 inherited FLUX-OPT sites + 17 other historical asserts in
`process_standard.rs`, `allocator.rs`, etc.). 257 asserts cover ~290
unique source-line marker positions, which collectively reference 290
unique panic-survey addresses (with another 65–70 dedup-duplicate refs
on top).

## Coverage status

- **308 annotatable** addresses (PAP, chain-walk).
- **290 unique addresses referenced in markers** in source.
- **18 annotatable addresses not yet in markers** — these are the
  chain-walk-recovered sites from the `bee46b42d` patch whose
  apply-script run broke compilation; we reverted them and deferred
  driving them into source cleanly to a smarter apply pass.
- **25 truly un-annotatable** (no source line anywhere in chain).
- **14 pure stdlib helpers** (panic plumbing every panic flows
  through — leave as-is).
- **8 misattributed stdlib monomorphs** (their depth=0 site is the
  user-crate caller; the caller-scan in this session's
  `/tmp/find_stdlib_callers.py` enumerates them, but no source
  markers were emitted — recorded in
  `memory/project_stdlib_misattribution.md`).

## Next session's discharge work

For each of the ~223 `assert(false)` markers, the FLUX-TODO comment's
`flavor=` field names the verification target. Replace `false` with
the discharge predicate:

| flavor | discharge pattern |
|---|---|
| `unwrap_option` | `x.is_some()` |
| `unwrap_result` | `x.is_ok()` |
| `bounds` | `i < arr.len()` |
| `slice_end` | `end <= slice.len()` |
| `slice_start` | `start <= slice.len()` |
| `slice_order` | `start <= end` |
| `div_by_zero` | `divisor != 0` |
| `rem_by_zero` | `divisor != 0` |
| `explicit_panic` | case-by-case (often requires lifting a struct invariant or a path-condition) |
| `assert` | the asserted predicate (mirror the surrounding code) |
| `unwind` / `optional_cell_unwrap` | depends on call site |
