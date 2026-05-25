#!/usr/bin/env python3
"""deice_probe.py — fresh, ICE-aware negation probe over the CURRENT tree.

Re-derives the live obligation set from source (the prior negation_probe.json was
stale: many asserts have since been commented out) and classifies every live
`flux_support::assert(...)` with an ICE-aware method, producing the de-ICEd table.

Per crate:
  1. `cargo flux clean`, then a baseline run from INSIDE the crate dir. If a
     *dependency* fails to compile -> the crate is BLOCKED_DEP_MASKED (flux never
     reached its bodies). If the baseline ICEs, retry up to 3x with a clean each
     time; if it still ICEs, the whole crate is ICE_MASKED.
  2. For each live assert:
       sentinel `assert(false)`     -> DEAD_PROVEN / DEAD_FAILING (baseline; ICE-proof)
       errors at site in baseline   -> FAILING                    (ICE-proof)
       enclosing fn #[trusted]      -> TRUSTED_BLOCKED
       else (passes baseline)       -> flip cond->false and re-check:
           warm flip; if it ICEs, `cargo flux clean` + retry up to 3x.
             error at site -> PROVEN
             silent        -> SILENT
             all retries ICE -> ICE_MASKED   <-- the de-ICE payoff
  Warm flips (after a clean baseline) keep the common case in budget; clean is
  applied at the baseline and before every ICE-retry, per the de-ICE protocol.

Hard rules:
  * Never modify in-progress scaffolding still in the tree (framer.rs, ble_radio.rs);
    asserts there are SKIPPED_SCAFFOLDING. (async_ops.rs is reverted out-of-band
    for this run, so it is fair game.)
  * Per-crate wall budget (default 600s). On exceed, remaining -> NOT_RUN, crate
    marked stopped with where/what completed.
Checkpoints after every site; restores every flip; asserts a clean tree (modulo
the untouched scaffolding) at the end.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import negation_probe as NP

ROOT = Path(__file__).resolve().parent.parent
CRATES = NP.CRATES
SCAFFOLD = ("framer.rs", "ble_radio.rs", "ieee802154/driver.rs")  # async_ops.rs stashed; driver.rs excluded from include (ICE source) + carries ICE-dodge trusts not to revert
MAX_RETRIES = 3
DEP_RE = re.compile(r"could not compile `([^`]+)`")


def _env(target: Path):
    e = os.environ.copy()
    e["CARGO_TARGET_DIR"] = str(target)
    return e


def clean_in_dir(crate_dir: str, target: Path):
    subprocess.run(["cargo", "flux", "clean"], cwd=str(ROOT / crate_dir),
                   capture_output=True, text=True, env=_env(target))


def run_flux_in_dir(crate_dir: str, target: Path, timeout: int) -> str:
    try:
        r = subprocess.run(["cargo", "flux", "--keep-going"], cwd=str(ROOT / crate_dir),
                           capture_output=True, text=True, timeout=timeout, env=_env(target))
        return r.stdout + "\n" + r.stderr
    except subprocess.TimeoutExpired:
        return "TIMEOUT"


def dep_mask(log: str, pkg: str):
    own = pkg.replace("-", "_")
    for m in DEP_RE.finditer(log):
        name = m.group(1).split()[0]
        if name.replace("-", "_") != own:
            return name
    return None


def baseline_run(crate_dir: str, target: Path, timeout: int, deadline: float):
    """clean + run, retry-with-clean on ICE. Returns (log, attempts, verdict)."""
    log = None
    for attempt in range(MAX_RETRIES + 1):
        if time.time() > deadline:
            return log, attempt, "BUDGET"
        clean_in_dir(crate_dir, target)
        log = run_flux_in_dir(crate_dir, target, timeout)
        if log == "TIMEOUT":
            return log, attempt + 1, "TIMEOUT"
        if not NP.has_ice(log):
            return log, attempt + 1, "OK"
    return log, MAX_RETRIES + 1, "ICE"


def flip_run(crate_dir: str, target: Path, timeout: int, deadline: float):
    """warm run; on ICE, clean + retry up to MAX_RETRIES. Returns (log, attempts, verdict)."""
    log = run_flux_in_dir(crate_dir, target, timeout)   # warm
    if log == "TIMEOUT":
        return log, 1, "TIMEOUT"
    if not NP.has_ice(log):
        return log, 1, "OK"
    for r in range(MAX_RETRIES):
        if time.time() > deadline:
            return log, 1 + r, "BUDGET"
        clean_in_dir(crate_dir, target)
        log = run_flux_in_dir(crate_dir, target, timeout)
        if log == "TIMEOUT":
            return log, 2 + r, "TIMEOUT"
        if not NP.has_ice(log):
            return log, 2 + r, "OK"
    return log, 1 + MAX_RETRIES, "ICE"


def live_assert_files(crate_dir: str):
    root = ROOT / crate_dir / "src"
    return sorted(p for p in root.rglob("*.rs")
                  if NP.ASSERT_CALL in p.read_text(errors="ignore"))


def probe_crate(pkg: str, log_dir: Path, timeout: int, budget: int) -> dict:
    crate_dir = CRATES[pkg]
    target = (log_dir / "target" / pkg).resolve()
    target.mkdir(parents=True, exist_ok=True)
    started = time.time()
    deadline = started + budget
    rec = {"pkg": pkg, "stopped": None, "sites": []}

    print(f"=== {pkg}: baseline clean run ===", flush=True)
    base_log, b_att, b_verdict = baseline_run(crate_dir, target, timeout, deadline)
    (log_dir / f"{pkg}.baseline.log").write_text(base_log or "")
    rec["baseline_attempts"], rec["baseline_verdict"] = b_att, b_verdict
    files = live_assert_files(crate_dir)
    n_sites = sum(len(NP.find_assert_sites(f.read_text())) for f in files)

    if b_verdict in ("BUDGET", "TIMEOUT"):
        rec["stopped"] = f"baseline {b_verdict.lower()}"
        return rec
    if b_verdict == "ICE":
        rec["stopped"] = f"baseline ICE x{b_att}"
        for f in files:
            for s in NP.find_assert_sites(f.read_text()):
                rec["sites"].append(_mk(f, crate_dir, s, "ICE_MASKED"))
        print(f"  baseline ICE x{b_att} -> {n_sites} sites ICE_MASKED", flush=True)
        return rec
    dep = dep_mask(base_log, pkg)
    if dep:
        rec["stopped"] = f"dep-masked by `{dep}`"
        for f in files:
            for s in NP.find_assert_sites(f.read_text()):
                r = _mk(f, crate_dir, s, "BLOCKED_DEP_MASKED"); r["dep"] = dep
                rec["sites"].append(r)
        print(f"  dep-masked by `{dep}` -> {n_sites} sites BLOCKED_DEP_MASKED", flush=True)
        return rec

    base_errs = NP.error_lines(base_log, crate_dir)
    print(f"  baseline OK ({n_sites} live asserts across {len(files)} files)", flush=True)

    for f in files:
        relfile = str(f.relative_to(ROOT / crate_dir))
        orig = f.read_text()
        for site in NP.find_assert_sites(orig):
            if time.time() > deadline:
                rec["sites"].append(_mk(f, crate_dir, site, "NOT_RUN"))
                continue
            r = _mk(f, crate_dir, site, None)
            is_sentinel = site["inner"] == "false"
            base_hit = NP.err_at(base_errs, relfile, site)
            if is_sentinel:
                r["deiced"] = "DEAD_FAILING" if base_hit else "DEAD_PROVEN"
            elif base_hit:
                r["deiced"] = "FAILING"
            elif any(sf in relfile for sf in SCAFFOLD):
                r["deiced"] = "SKIPPED_SCAFFOLDING"
            elif NP.enclosing_fn_trusted(orig, site["call_off"]):
                r["deiced"] = "TRUSTED_BLOCKED"
            else:
                try:
                    f.write_text(NP.flip_text(orig, site))
                    flog, att, verdict = flip_run(crate_dir, target, timeout, deadline)
                finally:
                    f.write_text(orig)
                r["attempts"] = att
                if verdict == "BUDGET":
                    r["deiced"] = "NOT_RUN"
                elif verdict == "TIMEOUT":
                    r["deiced"] = "TIMEOUT"
                elif verdict == "ICE":
                    r["deiced"] = "ICE_MASKED"
                else:
                    ferrs = NP.error_lines(flog, crate_dir)
                    r["deiced"] = "PROVEN" if NP.err_at(ferrs, relfile, site) else "SILENT"
            rec["sites"].append(r)
            tag = "" if r["deiced"] in ("DEAD_PROVEN", "FAILING", "TRUSTED_BLOCKED",
                                        "SKIPPED_SCAFFOLDING", "DEAD_FAILING") else \
                  f" (x{r.get('attempts','?')})"
            print(f"    {relfile}:{site['start_line']:<5} {r['deiced']:<18}{tag}", flush=True)

    if any(x["deiced"] == "NOT_RUN" for x in rec["sites"]):
        done = sum(1 for x in rec["sites"] if x["deiced"] != "NOT_RUN")
        rec["stopped"] = f"budget {budget}s exceeded; {done}/{len(rec['sites'])} sites done"
    return rec


def _mk(f, crate_dir, site, deiced):
    return {"file": str(Path(f).relative_to(ROOT / crate_dir)),
            "line": site["start_line"], "inner": site["inner"][:80], "deiced": deiced}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--crates", nargs="+", required=True)
    ap.add_argument("--out", type=Path, default=Path("tools/deice_probe.json"))
    ap.add_argument("--log-dir", type=Path, default=Path("tools/deice_logs"))
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--budget", type=int, default=600)
    args = ap.parse_args()
    args.log_dir.mkdir(parents=True, exist_ok=True)
    out = json.loads(args.out.read_text()) if args.out.exists() else {}
    for pkg in args.crates:
        t0 = time.time()
        out[pkg] = probe_crate(pkg, args.log_dir, args.timeout, args.budget)
        out[pkg]["wall_s"] = round(time.time() - t0, 1)
        args.out.write_text(json.dumps(out, indent=2))
        print(f"  [{pkg}] done in {out[pkg]['wall_s']}s -> checkpointed\n", flush=True)

    dirty = subprocess.run(["git", "status", "--porcelain", "--", "*.rs"],
                           capture_output=True, text=True, cwd=str(ROOT)).stdout
    unexpected = [l for l in dirty.splitlines()
                  if l.strip().endswith(".rs") and not any(sf in l for sf in SCAFFOLD)]
    if unexpected:
        # NEVER auto git-checkout: the tree legitimately holds uncommitted de-ICE
        # trusts. Per-flip in-memory restore already keeps files correct; just warn.
        print("\n!!! NOTE: dirty .rs present (NOT reverting — likely your uncommitted work):",
              *unexpected, sep="\n   ")
    else:
        print("\n✓ tree clean (flips restored)", flush=True)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
