#!/usr/bin/env python3
"""
panic_proven_chart.py — Task 2: classify every panic-site annotation by whether
Flux is actually checking it.

Per the "locally proven" definition:
  an annotation is LOCALLY PROVEN iff its enclosing fn is
    (1) included in the crate's Cargo.toml `[package.metadata.flux] include`
        (by `def:<name>` substring, `span:`, or file path), AND
    (2) has no `#[flux_rs::trusted]` / `#[flux_rs::trusted_impl]` on it.

Buckets (per annotation):
  locally-proven : enclosing fn included AND not trusted
  not-checked    : enclosing fn not in any include  (Flux never looks at it)
  trusted:<cat>  : enclosing fn is trusted; bucketed by reason category

An "annotation" here = a `flux_support::assert(...)` call (live, not commented).
We also report commented-out asserts (blocked/deferred) separately.

Cross-check: if --errors-dir is given, flag any locally-proven annotation whose
file appears in that crate's Flux error log (i.e. claimed proven but actually
still erroring).

Run: tools/.venv/bin/python3 tools/panic_proven_chart.py [--png] [--errors-dir tools/flux_audit_logs_fresh]
"""

import argparse
import re
import tomllib
from collections import Counter, defaultdict
from pathlib import Path

CRATES = {
    "kernel": "kernel", "libraries/tickv": "tickv",
    "capsules/core": "capsules-core", "capsules/extra": "capsules-extra",
    "libraries/tock-cells": "tock-cells",
    "chips/nrf52": "nrf52", "chips/nrf52840": "nrf52840", "chips/nrf5x": "nrf5x",
    "arch/cortex-m": "cortexm", "arch/cortex-v7m": "cortexv7m",
}

FN_RE       = re.compile(r"\bfn\s+([A-Za-z_][A-Za-z0-9_]*)\s*[(<]")
TRUSTED_RE  = re.compile(r"#\[flux_rs::trusted(_impl)?\b")
REASON_RE   = re.compile(r'reason\s*=\s*"([^"]*)"')
ASSERT_RE   = re.compile(r"flux_support::assert\(")


def load_includes(crate_path):
    try:
        data = tomllib.load(open(Path(crate_path) / "Cargo.toml", "rb"))
        inc = data.get("package", {}).get("metadata", {}).get("flux", {}).get("include")
    except (OSError, KeyError):
        inc = None
    files, defs, spans = set(), set(), []
    if inc:
        for e in inc:
            if e.startswith("def:"):
                defs.add(e[4:])
            elif e.startswith("span:"):
                spans.append(e[5:])
            else:
                files.add(e)
    return {"all": inc is None, "files": files, "defs": defs, "spans": spans}


def reason_category(reason):
    r = (reason or "").lower()
    if not reason:
        return "trusted:no-reason"
    if "ice" in r or "infer.rs" in r or "checker.rs" in r or "fixpoint_encoding" in r or "join point" in r:
        return "trusted:blocked-ice"
    if "dyn" in r:
        return "trusted:blocked-dyn"
    if "cell" in r:
        return "trusted:blocked-cell"
    if "overflow" in r or "bitwise" in r or "arithmetic" in r:
        return "trusted:arithmetic/bitwise"
    if "extern-spec" in r or "extern spec" in r or "not specified" in r or "not specified in flux" in r:
        return "trusted:extern-spec-gap"
    if "cascade" in r or "caller" in r or "precondition" in r or "rangebounds" in r:
        return "trusted:caller-cascade"
    return "trusted:other"


def fn_included(fn_name, relpath, inc):
    if inc["all"]:
        return True
    if relpath in inc["files"]:
        return True
    # `def:` is an unanchored substring match (mirrors Flux's filter)
    for d in inc["defs"]:
        if d in fn_name:
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--png", action="store_true")
    ap.add_argument("--errors-dir", default=None,
                    help="dir of <crate>.log flux logs for the proven-but-erroring cross-check")
    args = ap.parse_args()

    ann_bucket = Counter()          # annotation -> bucket
    fns_touched = set()             # (crate, file, fn)
    commented = 0
    err_fns = set()                 # (relpath_or_full, fn) that Flux rejects

    def enclosing_fn(lines, i):
        for j in range(min(i, len(lines)) - 1, -1, -1):
            m = FN_RE.search(lines[j])
            if m:
                return m.group(1)
        return "<none>"

    if args.errors_dir:
        for cp, name in CRATES.items():
            log = Path(args.errors_dir) / f"{name}.log"
            if not log.exists():
                continue
            for ln in log.read_text(errors="replace").splitlines():
                m = re.match(r"([^\s:]+\.rs):(\d+):", ln)
                if m and "error[E0999]" in ln:
                    f = m.group(1).lstrip("./")
                    if Path(f).exists():
                        fn = enclosing_fn(Path(f).read_text(errors="replace").splitlines(),
                                          int(m.group(2)))
                        err_fns.add((f, fn))

    for crate_path, name in CRATES.items():
        inc = load_includes(crate_path)
        for p in Path(crate_path).rglob("*.rs"):
            relpath = str(p.relative_to(crate_path))
            lines = p.read_text(errors="replace").splitlines()
            # Precompute fn def lines and their trusted reason (scan attrs above).
            for i, line in enumerate(lines):
                if not ASSERT_RE.search(line):
                    continue
                # An OBLIGATION is an assert tied to a real panic, i.e. one that
                # sits under a FLUX-* marker. Markerless asserts are auxiliary
                # proof scaffolding (intermediate lemmas, S!=0 divisor guards) —
                # not obligations about a panic, so they're tallied separately.
                has_marker = any(re.search(r"FLUX-(OPT|TODO)", lines[j])
                                 for j in range(max(i - 5, 0), i + 1))
                if not has_marker:
                    ann_bucket["(auxiliary — not an obligation)"] += 1
                    continue
                if line.lstrip().startswith("//"):
                    # Commented-out assert = a dispositioned-away annotation
                    # (blocked / actionable). Classify by the nearby `// Notes:`.
                    commented += 1
                    note = None
                    for j in range(i, max(i - 8, -1), -1):
                        nm = re.search(r"Notes:\s*([a-z][a-z0-9-]*)", lines[j])
                        if nm:
                            note = nm.group(1)
                            break
                    if note == "actionable":
                        ann_bucket["deferred:actionable"] += 1
                    elif note and note.startswith("blocked"):
                        ann_bucket[f"blocked:{note}"] += 1
                    else:
                        ann_bucket["blocked:untagged"] += 1
                    continue
                # find nearest enclosing fn above
                fn_name, fn_line = None, None
                for j in range(i, -1, -1):
                    fm = FN_RE.search(lines[j])
                    if fm:
                        fn_name, fn_line = fm.group(1), j
                        break
                if fn_name is None:
                    fn_name = "<none>"
                    fn_line = i
                fns_touched.add((name, relpath, fn_name))
                # trusted? scan up to ~8 attr/comment lines above the fn
                reason, trusted = None, False
                for j in range(fn_line, max(fn_line - 10, -1), -1):
                    if TRUSTED_RE.search(lines[j]):
                        trusted = True
                        rm = REASON_RE.search(lines[j])
                        # reason may be on the same or next line(s)
                        blob = " ".join(lines[j:j + 3])
                        rm = REASON_RE.search(blob)
                        reason = rm.group(1) if rm else None
                        break
                    if lines[j].strip() and not lines[j].lstrip().startswith(("#[", "//", "///", "#!")) and "fn " not in lines[j]:
                        # hit real code above the fn's attr block; stop
                        if j < fn_line:
                            break
                full = f"{crate_path}/{relpath}"
                fn_errors = args.errors_dir and ((full, fn_name) in err_fns or (relpath, fn_name) in err_fns)
                if trusted:
                    ann_bucket[reason_category(reason)] += 1
                elif not fn_included(fn_name, relpath, inc):
                    ann_bucket["not-checked"] += 1
                elif fn_errors:
                    # included + not trusted, but Flux rejects the enclosing fn
                    # (unmasked once kernel reached a true 0 — see the masking note)
                    ann_bucket["included-but-FAILING (unmasked)"] += 1
                else:
                    ann_bucket["locally-proven"] += 1

    total = sum(ann_bucket.values())
    print(f"=== Task 2: {total} total annotations across {len(fns_touched)} functions ===")
    print(f"    ({total - commented} live + {commented} commented-out/blocked)\n")
    for k in sorted(ann_bucket, key=lambda x: (-ann_bucket[x], x)):
        print(f"  {ann_bucket[k]:>4}  {k}")
    print(f"  ----")
    print(f"  {total:>4}  total")

    if args.errors_dir:
        print(f"\n  Flux-verdict cross-check ON ({args.errors_dir}): "
              f"{len(err_fns)} distinct functions currently rejected by Flux.")
        print("  'included-but-FAILING' = annotations whose enclosing fn is included")
        print("  and not trusted, yet Flux rejects it (mostly capsules/chips unmasked")
        print("  once kernel hit a true 0). Without --errors-dir these count as locally-proven.")

    if args.png:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        labels = sorted(ann_bucket, key=lambda x: -ann_bucket[x])
        vals = [ann_bucket[k] for k in labels]
        def color(k):
            if k == "locally-proven": return "#2ca02c"
            if k == "not-checked":    return "#bdbdbd"
            return "#d62728" if "ice" in k or "dyn" in k else "#ff7f0e"
        fig, ax = plt.subplots(figsize=(10, 5))
        bars = ax.barh(range(len(labels)), vals, color=[color(k) for k in labels])
        ax.set_yticks(range(len(labels))); ax.set_yticklabels(labels); ax.invert_yaxis()
        ax.set_title(f"Panic-site annotations by checking status ({total} live)")
        for b, n in zip(bars, vals):
            ax.text(b.get_width() + 0.5, b.get_y() + b.get_height()/2, str(n), va="center")
        fig.tight_layout()
        out = Path("tools/panic_proven_chart.png"); fig.savefig(out, dpi=120)
        print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
