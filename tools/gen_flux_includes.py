#!/usr/bin/env python3
"""
gen_flux_includes.py — derive per-function flux `include` lists from the panic
survey, so each crate checks EXACTLY the functions that own a panic site
(not whole files / whole crates).

For every site in panic_survey_branch.json, resolve the enclosing *named* fn
(walking up past closures), map the file to its crate, and emit the distinct
`def:<fn>` entries per crate. `def:` is an unanchored substring match in Flux,
so short/generic names over-match — those are flagged so they can be reviewed
(or pinned another way).

Run: tools/.venv/bin/python3 tools/gen_flux_includes.py [--survey ...]
"""
import argparse, json, re
from collections import defaultdict
from pathlib import Path

CRATES = {
    "kernel": "kernel", "libraries/tickv": "tickv",
    "capsules/core": "capsules-core", "capsules/extra": "capsules-extra",
    "libraries/tock-cells": "tock-cells",
    "chips/nrf52": "nrf52", "chips/nrf52840": "nrf52840", "chips/nrf5x": "nrf5x",
    "arch/cortex-m": "cortexm", "arch/cortex-v7m": "cortexv7m",
}
FN = re.compile(r"\bfn\s+([A-Za-z_]\w*)\s*[(<]")
_cache = {}


def crate_of(relpath):
    for cp in CRATES:
        if relpath.startswith(cp + "/"):
            return cp
    return None


def named_fn(f, line):
    if f not in _cache:
        _cache[f] = Path(f).read_text(errors="replace").splitlines() if Path(f).exists() else []
    L = _cache[f]
    for j in range(min(line, len(L)) - 1, -1, -1):
        m = FN.search(L[j])
        if m:
            return m.group(1)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--survey", default="tools/panic_survey_branch.json")
    args = ap.parse_args()
    sites = json.load(open(args.survey))["sites"]

    # crate -> {fn -> count of panic sites it owns}
    by_crate = defaultdict(lambda: defaultdict(int))
    for s in sites:
        ef = s.get("effective_frame") or {}
        f = (ef.get("file") or "").lstrip("./")
        if not f or "/rustc/" in f:
            continue
        cp = crate_of(f)
        if not cp:
            continue
        fn = named_fn(f, ef.get("line") or 0)
        if fn:
            by_crate[cp][fn] += 1

    for cp in CRATES:
        fns = by_crate.get(cp)
        if not fns:
            continue
        name = CRATES[cp]
        allfns = sorted(fns)
        # flag substring collisions: a name that is a substring of another fn
        # name in the SAME crate (so def: would over-match)
        print(f"\n===== {cp}  ({name}) — {len(allfns)} panic-bearing fns, "
              f"{sum(fns.values())} sites =====")
        print("[package.metadata.flux]")
        print("enabled = true")
        print('check_overflow = "lazy"')
        print("include = [")
        for fn in allfns:
            short = len(fn) <= 3
            collide = any(fn in other and fn != other for other in allfns)
            flag = ""
            if short:
                flag = "  # SHORT name — def: substring will over-match broadly"
            elif collide:
                flag = "  # substring of another included fn — over-matches"
            print(f'    "def:{fn}",{flag}   # {fns[fn]} site(s)')
        print("]")


if __name__ == "__main__":
    main()
