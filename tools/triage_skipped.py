#!/usr/bin/env python3
"""triage_skipped.py — sort the de-ICEd SILENT (Flux-skipped) asserts by SHAPE.

Input: tools/deice_probe.json. Considers every site whose deiced verdict is
SILENT (genuinely not checked, ICE-free) and also reports SKIPPED_SCAFFOLDING
rows (shape inferred from source; not flip-measured). Closures are the known bug;
this breaks out everything else.

For each row we record the full structural attributes (so the set can be re-sliced)
and assign ONE primary shape in priority order:
  CLOSURE        assert lexically inside a closure passed to .map/.and_then/...
                 (maintainer-confirmed: Flux doesn't descend the closure body)
  SIG_SUPPRESSED enclosing fn carries #[flux_rs::sig] (not a closure) — the
                 present-sig-verifies-body-vacuously shape (framer)
  TRAIT_IMPL     method of `impl Trait for T`, no closure/sig — the read_region
                 shape; sub-tagged generic / const-generic / plain
  INHERENT       inherent method `impl T { fn }`, no closure/sig
  FREE_FN        free fn, no closure/sig
"""
import json, re, pathlib, subprocess
from collections import Counter, defaultdict

# async_ops.rs was git-stashed (=HEAD) during the de-ICE run, so its measured line
# numbers are HEAD's; the working tree is +1-shifted. Read the measured source.
STASHED = {("tickv", "src/async_ops.rs")}
def measured_source(pkg, relfile, cd):
    if (pkg, relfile) in STASHED:
        return subprocess.run(["git", "show", f"HEAD:{cd}/{relfile}"],
                              capture_output=True, text=True).stdout
    return pathlib.Path(f"{cd}/{relfile}").read_text(errors="ignore")

CR = {"kernel":"kernel","tock-cells":"libraries/tock-cells","tickv":"libraries/tickv",
      "cortexm":"arch/cortex-m","cortexv7m":"arch/cortex-v7m","capsules-core":"capsules/core",
      "capsules-extra":"capsules/extra","nrf52":"chips/nrf52","nrf52840":"chips/nrf52840",
      "nrf5x":"chips/nrf5x"}
ASSERT = "flux_support::assert("
FN = re.compile(r"(?m)^[ \t]*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+|unsafe\s+|async\s+|extern\s+\"[^\"]*\"\s+)*fn\s+(\w+)")
IMPL = re.compile(r"(?m)^[ \t]*impl\b[^\{]*")
CLOSURE_HDR = re.compile(r"\|[^|\n]*\|\s*$")
CLOSURE_CALL = re.compile(r"\.\s*(map|map_or|map_or_else|and_then|unwrap_or_else|inspect|filter|for_each|map_err|each|enter|take|and_then)\s*\(\s*(?:move\s*)?\|")


def enclosing_fn(t, off):
    last = None
    for m in FN.finditer(t):
        if m.start() < off:
            last = m
        else:
            break
    return last


def _match_brace(t, ob):
    depth = 0; j = ob
    while j < len(t):
        if t[j] == '{':
            depth += 1
        elif t[j] == '}':
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return len(t)


def enclosing_impl(t, fn_start):
    """The innermost `impl ...` block that ACTUALLY contains fn_start (brace-aware).
    A bare nearest-`impl` scan wrongly attributes free fns to a long-closed impl."""
    best = None
    for m in IMPL.finditer(t):
        if m.start() >= fn_start:
            break
        ob = t.find('{', m.start())
        if ob == -1:
            continue
        if ob < fn_start < _match_brace(t, ob):
            best = re.sub(r"\s+", " ", m.group(0).strip())
    return best


BOUNDARY = re.compile(r"\n[ \t]*\}[ \t]*\n")  # a block-close on its own line


def _attr_text(t, fn_start):
    """The attribute/comment region directly above the fn decl: everything since the
    previous block-close. Robust to MULTI-LINE attrs (a `#[flux_rs::sig(\n fn(...)\n)]`
    spanning lines) and long `reason=` strings — both broke a line-by-line walk."""
    region = t[max(0, fn_start - 1500):fn_start]
    last = None
    for m in BOUNDARY.finditer(region):
        last = m
    return region[last.end():] if last else region


def has_sig(t, fn_start):
    return "flux_rs::sig" in _attr_text(t, fn_start)


def trusted(t, fn_start):
    if "flux_rs::trusted" in _attr_text(t, fn_start):
        return True
    # enclosing impl-block #[trusted]?
    impl = None
    for m in IMPL.finditer(t):
        if m.start() < fn_start:
            impl = m
        else:
            break
    return bool(impl and "flux_rs::trusted" in t[max(0, impl.start() - 400):impl.start()])


def closure_method(t, off):
    """walk enclosing blocks; if any is a closure, return the call method (or 'bare')."""
    depth = 0; i = off
    while i > 0:
        c = t[i]
        if c == '}':
            depth += 1
        elif c == '{':
            if depth == 0:
                pre = t[max(0, i - 120):i].rstrip()
                m = CLOSURE_CALL.search(pre)
                if m:
                    return m.group(1)
                if CLOSURE_HDR.search(pre):
                    return "bare"
                i -= 1; continue
            depth -= 1
        i -= 1
    return None


def main():
    probe = json.load(open("tools/deice_probe.json"))
    rows = []
    for pkg, r in probe.items():
        cd = CR[pkg]
        for s in r["sites"]:
            if s["deiced"] not in ("SILENT", "SKIPPED_SCAFFOLDING"):
                continue
            t = measured_source(pkg, s["file"], cd)
            off = None
            for m in re.finditer(re.escape(ASSERT), t):
                if t.count("\n", 0, m.start()) + 1 == s["line"]:
                    off = m.start(); break
            fn = enclosing_fn(t, off) if off is not None else None
            fname = fn.group(1) if fn else "?"
            impl = enclosing_impl(t, fn.start()) if fn else None
            sig = has_sig(t, fn.start()) if fn else False
            is_trusted = trusted(t, fn.start()) if fn else False
            clo = closure_method(t, off) if off is not None else None
            is_trait = bool(impl and " for " in impl)
            generic = bool(impl and re.search(r"impl\s*<", impl))
            const_gen = bool(impl and "const " in (impl.split(" for ")[0] if is_trait else impl))

            if is_trusted:
                shape = "TRUSTED(mislabeled)"
            elif clo:
                shape = "CLOSURE"
            elif sig:
                shape = "SIG_SUPPRESSED"
            elif is_trait:
                shape = "TRAIT_IMPL"
            elif impl:
                shape = "INHERENT"
            else:
                shape = "FREE_FN"

            rows.append({
                "shape": shape, "pkg": pkg, "loc": f"{cd}/{s['file']}:{s['line']}",
                "fn": fname, "cond": s["inner"][:46], "impl": (impl or "")[:70],
                "trait": is_trait, "generic": generic, "const_gen": const_gen,
                "closure": clo or "", "sig": sig, "trusted": is_trusted,
                "scaffold": s["deiced"] == "SKIPPED_SCAFFOLDING",
            })

    by_shape = Counter(r["shape"] for r in rows)
    print(f"=== {len(rows)} skipped rows (SILENT + scaffold), by SHAPE ===")
    for sh, n in by_shape.most_common():
        print(f"  {sh:<16} {n}")
    print()
    for sh, _ in by_shape.most_common():
        grp = [r for r in rows if r["shape"] == sh]
        print(f"--- {sh} ({len(grp)}) ---")
        for r in grp:
            extra = []
            if r["closure"]:
                extra.append(f"in .{r['closure']}()")
            if r["trait"]:
                extra.append("trait-impl" + (" generic" if r["generic"] else "") + (" CONST-GEN" if r["const_gen"] else ""))
                extra.append(r["impl"])
            if r["sig"]:
                extra.append("has #[sig]")
            if r["scaffold"]:
                extra.append("[scaffold]")
            print(f"  {r['loc']:<58} fn {r['fn']:<28} {' | '.join(extra)}")
        print()

    cols = ["shape","pkg","loc","fn","cond","trait","generic","const_gen","closure","sig","trusted","scaffold","impl"]
    out = "\t".join(cols) + "\n"
    for r in rows:
        out += "\t".join(str(r[k]) for k in cols) + "\n"
    pathlib.Path("tools/skipped_triage.tsv").write_text(out)
    print("wrote tools/skipped_triage.tsv")


if __name__ == "__main__":
    main()
