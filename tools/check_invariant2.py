#!/usr/bin/env python3
"""check_invariant2.py — Step 2 / Invariant 2: every covered obligation is
genuinely discharged (not vacuous).

Spec: tools/invariant_two.md. Consumes step 1's invariant1_report.json, probes
each covered obligation with the ICE-aware negation probe, and emits
invariant2_report.json + a no-vacuity gate.

The probe engine is reused wholesale from negation_probe.py (NP) and
deice_probe.py (DP): assert-site parsing, flip, ICE detection, and the per-crate
clean+baseline+warm-flip+ICE-retry protocol are unchanged. What's new here:

  (a) obligations come from invariant1_report.json, not a tree re-scan:
        comment_precise -> the flux_support::assert(...) step 0 paired below the
                           marker (dedup: stacked markers / monomorphs share one
                           assert, keyed (file, assert_line)).
        comment_fn_level -> the enclosing fn (no paired assert); vacuity tested by
                           the ENTRY CONTROL (inject assert(false) at fn entry).
  (b) FN-LEVEL entry-control path.
  (c) crates probed in topological dependency order, with DEPENDENCY UNMASKING:
      after a dependency crate is probed, its erroring fns are temporarily
      #[flux_rs::trusted] (per-fn, iterated; whole-file fallback) so dependents
      are genuinely checked instead of dep-masked. All trusts restored at end.
  (d) FULL ERROR INVENTORY: every baseline Flux diagnostic (code/span/message),
      classified assert_obligation vs other_obligation. Informational, not gating.

Gate (no-vacuity): exit 0 iff zero obligations are SILENT / ICE_MASKED /
BLOCKED_DEP_MASKED / NOT_RUN / TIMEOUT. NOT_PROVEN / DEAD_NOT_PROVEN / TRUSTED are
the reported frontier and do NOT fail the gate.

Usage:
    tools/.venv/bin/python3 tools/check_invariant2.py
    tools/.venv/bin/python3 tools/check_invariant2.py --crates kernel tickv
    tools/.venv/bin/python3 tools/check_invariant2.py --out /tmp/inv2.json
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
import deice_probe as DP

ROOT = Path(__file__).resolve().parent.parent

# pkg-name -> crate dir (NP.CRATES + the two single-obligation crates).
PKGS = dict(NP.CRATES)
PKGS["flux_support"] = "flux_support"
PKGS["nrf52840dk"] = "boards/nordic/nrf52840dk"

# longest-dir-prefix first, so capsules/core beats capsules.
_DIRS = sorted(PKGS.items(), key=lambda kv: -len(kv[1]))

# verdicts that mean "never genuinely analyzed / untrustworthy" -> fail the gate.
VACUOUS = {"SILENT", "ICE_MASKED", "BLOCKED_DEP_MASKED", "NOT_RUN", "TIMEOUT", "DEAD_SILENT"}
DISCHARGED = {"PROVEN", "DEAD_PROVEN"}
FRONTIER = {"NOT_PROVEN", "DEAD_NOT_PROVEN", "TRUSTED"}

ERR_FULL_RE = re.compile(r"error\[(E0999|FLUX[^\]]*)\]:\s*(.*)")
TRUST_ATTR = "#[flux_rs::trusted]"


# --------------------------------------------------------------------------
# crate / file mapping + topological order
# --------------------------------------------------------------------------
def pkg_of_file(relfile: str):
    f = relfile.lstrip("./")
    for pkg, d in _DIRS:
        if f == d or f.startswith(d + "/"):
            return pkg
    return None


def crate_deps_in_set(pkg: str) -> set:
    """Dependency pkgs (within PKGS) declared in this crate's Cargo.toml."""
    toml = ROOT / PKGS[pkg] / "Cargo.toml"
    if not toml.exists():
        return set()
    text = toml.read_text(errors="ignore")
    deps = set()
    in_deps = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            inner = s[1:-1]
            # table-header dep form: [dependencies.NAME] / [target.'cfg'.dependencies.NAME]
            hm = re.match(r'(?:.*\.)?dependencies\.([A-Za-z0-9_\-]+)$', inner)
            if hm:
                if hm.group(1) in PKGS and hm.group(1) != pkg:
                    deps.add(hm.group(1))
                in_deps = False
            else:
                # plain [dependencies] / [dev-dependencies] / [target.*.dependencies]
                in_deps = inner == "dependencies" or inner.endswith(".dependencies") \
                    or inner.endswith("dev-dependencies")
            continue
        if not in_deps:
            continue
        m = re.match(r'([A-Za-z0-9_\-]+)\s*=', s)
        if m and m.group(1) in PKGS and m.group(1) != pkg:
            deps.add(m.group(1))
    return deps


def topo_order(pkgs: list) -> list:
    """Kahn topological sort: dependencies before dependents."""
    pkgs = list(pkgs)
    deps = {p: crate_deps_in_set(p) & set(pkgs) for p in pkgs}
    order, ready = [], [p for p in pkgs if not deps[p]]
    ready.sort()
    seen = set()
    while ready:
        p = ready.pop(0)
        if p in seen:
            continue
        seen.add(p)
        order.append(p)
        for q in pkgs:
            if q not in seen and p in deps[q]:
                deps[q].discard(p)
                if not deps[q]:
                    ready.append(q)
        ready.sort()
    # any leftover (dependency cycle) appended deterministically
    for p in pkgs:
        if p not in seen:
            order.append(p)
    return order


def transitive_dependents(pkg: str, pkgs: list) -> set:
    """All pkgs in `pkgs` that depend (transitively) on `pkg`."""
    rev = {p: set() for p in pkgs}
    for p in pkgs:
        for d in crate_deps_in_set(p) & set(pkgs):
            rev[d].add(p)
    out, stack = set(), [pkg]
    while stack:
        cur = stack.pop()
        for dep in rev.get(cur, ()):
            if dep not in out:
                out.add(dep)
                stack.append(dep)
    return out


# --------------------------------------------------------------------------
# obligation derivation from invariant1_report.json
# --------------------------------------------------------------------------
def assert_sites(relfile: str, text: str):
    """find_assert_sites, but inside flux_support/ the canonical call is
    `crate::assert(` (a crate cannot name itself); match that there. Offsets stay
    valid against the real text, so flips remain correct (step 0 §7)."""
    pat = "crate::assert(" if relfile.lstrip("./").startswith("flux_support/") \
        else NP.ASSERT_CALL
    saved = NP.ASSERT_CALL
    NP.ASSERT_CALL = pat
    try:
        return NP.find_assert_sites(text)
    finally:
        NP.ASSERT_CALL = saved


def paired_assert_line(file_text_sites, marker_line: int):
    """The flux_support::assert site step 0 paired below a precise marker:
    the closest assert whose call starts strictly below the marker line."""
    cand = [s for s in file_text_sites if s["start_line"] > marker_line]
    return min(cand, key=lambda s: s["start_line"]) if cand else None


def derive_obligations(report: dict):
    """Return (precise, fn_level) obligation dicts keyed for probing."""
    # cache assert sites per file
    site_cache = {}

    def sites_for(relfile):
        if relfile not in site_cache:
            p = ROOT / relfile
            site_cache[relfile] = assert_sites(relfile, p.read_text(errors="ignore")) \
                if p.exists() else []
        return site_cache[relfile]

    precise, fn_level = {}, {}
    for o in report["obligations"]:
        st = o["status"]
        ann = o.get("annotation") or {}
        if st == "comment_precise":
            mfile, mline = ann["file"], ann["line"]
            site = paired_assert_line(sites_for(mfile), mline)
            if site is None:
                # step 0 guarantees a pairing; if missing, surface as its own bucket
                key = (mfile, -mline)
                rec = precise.setdefault(key, {"file": mfile, "assert_line": None,
                    "predicate": None, "addrs": [], "markers": [], "sources": [],
                    "no_assert": True})
            else:
                key = (mfile, site["start_line"])
                rec = precise.setdefault(key, {"file": mfile,
                    "assert_line": site["start_line"], "predicate": site["inner"],
                    "addrs": [], "markers": [], "sources": []})
            rec["addrs"].append(o["addr"])
            mk = {"file": mfile, "line": mline, "raw": ann.get("raw", "").strip()}
            if mk not in rec["markers"]:
                rec["markers"].append(mk)
            rec["sources"].append(o["source"])
        elif st == "comment_fn_level":
            mfile, mline = ann["file"], ann["line"]
            key = (mfile, mline)
            rec = fn_level.setdefault(key, {"file": mfile, "marker_line": mline,
                "func": o["source"]["func"], "addrs": [], "markers": [], "sources": []})
            rec["addrs"].append(o["addr"])
            mk = {"file": mfile, "line": mline, "raw": ann.get("raw", "").strip()}
            if mk not in rec["markers"]:
                rec["markers"].append(mk)
            rec["sources"].append(o["source"])
    return list(precise.values()), list(fn_level.values())


# --------------------------------------------------------------------------
# full error inventory
# --------------------------------------------------------------------------
def parse_full_errors(log: str, crate_dir: str):
    """All long-format flux diagnostics: {code,file,line,col,message}."""
    out, lines = [], log.splitlines()
    for n, line in enumerate(lines):
        m = ERR_FULL_RE.search(line)
        if not m:
            continue
        code, msg = m.group(1), m.group(2).strip()
        loc = None
        for k in range(n, min(n + 5, len(lines))):
            lm = NP.LOC_RE.search(lines[k])
            if lm:
                loc = lm
                break
        if not loc:
            continue
        f = loc.group(1).lstrip("./")
        out.append({"code": code, "file": f, "line": int(loc.group(2)),
                    "col": int(loc.group(3)), "message": msg})
    return out


def fn_span(text: str, fn_start_off: int):
    """(start_line, end_line) of the fn whose decl begins at fn_start_off, by
    balancing the body braces. Returns (line, None) if no body found."""
    start_line = text.count("\n", 0, fn_start_off) + 1
    # find param list, then the body '{'
    p = text.find("(", fn_start_off)
    if p == -1:
        return start_line, None, None
    depth, j = 0, p
    while j < len(text):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    b = text.find("{", j)
    if b == -1:
        return start_line, None, None
    depth, k = 0, b
    while k < len(text):
        c = text[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    return start_line, text.count("\n", 0, k) + 1, b


def fn_decl_after(text: str, marker_line: int):
    """First fn declaration at or below `marker_line`. Returns the regex match."""
    for m in NP.FN_DECL_RE.finditer(text):
        if text.count("\n", 0, m.start()) + 1 >= marker_line:
            return m
    return None


def _statically_trusted(ob, kind: str) -> bool:
    """True iff the obligation's enclosing fn carries `#[flux_rs::trusted]` in the
    current source — a declared carve-out we can read without running flux. Used to
    rescue dep-masked obligations that are explicitly trusted."""
    p = ROOT / ob["file"]
    if not p.exists():
        return False
    orig = p.read_text(errors="ignore")
    if kind == "precise":
        if ob.get("no_assert") or not ob.get("assert_line"):
            return False
        site = {s["start_line"]: s for s in assert_sites(ob["file"], orig)}.get(ob["assert_line"])
        return bool(site) and NP.enclosing_fn_trusted(orig, site["call_off"])
    decl = fn_decl_after(orig, ob["marker_line"])
    return bool(decl) and NP.enclosing_fn_trusted(orig, decl.start())


def emit_dep_masked(precise, fn_level, dep, crate_dir, out):
    """Classify a dep-masked crate's obligations: TRUSTED if the enclosing fn is
    statically `#[flux_rs::trusted]`, else BLOCKED_DEP_MASKED."""
    for ob, kind, pub in ([(o, "precise", _pub_precise) for o in precise]
                          + [(o, "fn_level", _pub_fn) for o in fn_level]):
        r = {"kind": kind, **pub(ob)}
        if _statically_trusted(ob, kind):
            r["status"] = "TRUSTED"
            r["probe"] = {"baseline": "dep_masked", "enclosing_fn": "trusted", "dep": dep}
        else:
            r["status"] = "BLOCKED_DEP_MASKED"
            r["dep"] = dep
        out["obligations"].append(r)


# --------------------------------------------------------------------------
# dependency unmasking
# --------------------------------------------------------------------------
def fn_decls_covering(text: str, err_offsets: list):
    """For each error offset, the start offset of its enclosing fn decl."""
    decls = [m.start() for m in NP.FN_DECL_RE.finditer(text)]
    out = set()
    for off in err_offsets:
        prev = [d for d in decls if d <= off]
        if prev:
            out.add(max(prev))
    return out


_TRUSTED_RE = re.compile(r"flux_rs::trusted\b")  # \b excludes `trusted_impl`


def _zone_before(text: str, pos: int) -> str:
    """The attribute/doc zone immediately above `pos`: text from the previous
    block-close-on-its-own-line up to `pos`. Captures multi-line attributes whole
    (a line-by-line scan breaks on an attribute's `)]` continuation line)."""
    region = text[:pos]
    last = None
    for mm in NP._TRUST_BOUNDARY.finditer(region):
        last = mm
    return region[last.end():] if last else region


def _already_trusted(text: str, off: int) -> bool:
    """True iff the fn at `off` already carries an effective `#[flux_rs::trusted]`
    — either in its own attribute zone (incl. multi-line attrs) or via an
    enclosing `impl` that is trusted. Injecting another would be a `duplicated
    attribute Trusted` error. `trusted_impl` does NOT count (it doesn't suppress
    bodies, so such fns still need trusting)."""
    if _TRUSTED_RE.search(_zone_before(text, off)):
        return True
    # nearest enclosing impl whose braces contain `off`
    for mm in reversed(list(NP._IMPL_RE.finditer(text))):
        if mm.start() >= off:
            continue
        b = text.find("{", mm.end())
        if b == -1 or b > off:
            continue
        depth, k = 0, b
        while k < len(text):
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        if b <= off <= k:  # off is inside this impl block
            return bool(_TRUSTED_RE.search(_zone_before(text, mm.start())))
    return False


def inject_trusts(text: str, decl_offsets) -> str:
    """Insert #[flux_rs::trusted] above each fn decl. FN_DECL_RE anchors at the
    line start (col 0), so each offset is a line start; insert an indented attr
    line above it. Descending order keeps earlier offsets valid. A fn that
    ALREADY carries an adjacent trusted attribute is skipped — a second
    #[flux_rs::trusted] is a `duplicated attribute` error (nrf52's ICE-dodge
    trusts hit this)."""
    targets = [off for off in set(decl_offsets)
               if not _already_trusted(text, off)]
    for off in sorted(targets, reverse=True):
        j = off
        while j < len(text) and text[j] in " \t":
            j += 1
        indent = text[off:j]
        text = text[:off] + indent + TRUST_ATTR + "\n" + text[off:]
    return text


def off_of(text: str, relfile_line):
    """Byte offset of the start of (1-based) line."""
    parts = text.split("\n")
    return sum(len(p) + 1 for p in parts[:relfile_line - 1])


def crate_has_flux_rs(pkg: str) -> bool:
    """True if the crate declares a flux-rs dependency. If not, its source
    cannot carry `#[flux_rs::trusted]` (the attr is unresolved when the crate is
    built as a *dependency* — cargo flux only injects flux_rs for the target
    crate), and it has no native flux specs to preserve."""
    toml = ROOT / PKGS[pkg] / "Cargo.toml"
    return toml.exists() and bool(re.search(r'\bflux[-_]rs\s*=', toml.read_text(errors="ignore")))


def disable_flux_cargo(pkg: str, saved_originals: dict):
    """Flip [package.metadata.flux] enabled=true -> false (saved for restore).
    Whole-crate unmask for crates that can't carry a trusted attribute."""
    p = ROOT / PKGS[pkg] / "Cargo.toml"
    text = p.read_text()
    if str(p) not in saved_originals:
        saved_originals[str(p)] = text
    p.write_text(re.sub(r'(\[package\.metadata\.flux\][^\[]*?enabled\s*=\s*)true',
                        r'\1false', text, count=1, flags=re.S))


def unmask_crate(pkg, target, timeout, deadline, base_errs_full, saved_originals, log):
    """Trust the crate's erroring fns so dependents compile clean. Iterates
    per-fn; falls back to trusting every fn in the erroring files. Saved
    originals recorded for end-of-run restore. Returns the mechanism used."""
    crate_dir = PKGS[pkg]
    # group baseline errors by file (only this crate's files)
    by_file = {}
    for e in base_errs_full:
        f = e["file"]
        if f.startswith(crate_dir + "/") or (ROOT / f).exists() and pkg_of_file(f) == pkg:
            by_file.setdefault(f, []).append(e["line"])
    if not by_file:
        return "none"

    # crates without a flux-rs dep can't carry #[flux_rs::trusted] as a dependency
    # -> disable flux for the whole crate instead (no native specs to lose).
    if not crate_has_flux_rs(pkg):
        disable_flux_cargo(pkg, saved_originals)
        DP.clean_in_dir(crate_dir, target)
        log(f"    unmask {pkg}: no flux-rs dep -> disabled flux in Cargo.toml")
        return "disabled_crate"

    def save(relfile):
        p = ROOT / relfile
        if str(p) not in saved_originals:
            saved_originals[str(p)] = p.read_text()

    # per-fn rounds
    for rnd in range(5):
        cur_errs = base_errs_full if rnd == 0 else parse_full_errors(
            DP.run_flux_in_dir(crate_dir, target, timeout), crate_dir)
        cur_by_file = {}
        for e in cur_errs:
            if e["file"].startswith(crate_dir + "/"):
                cur_by_file.setdefault(e["file"], []).append(e["line"])
        if not cur_by_file:
            log(f"    unmask {pkg}: clean after {rnd} per-fn round(s)")
            return "per_fn"
        for relfile, elines in cur_by_file.items():
            p = ROOT / relfile
            if not p.exists():
                continue
            save(relfile)
            text = p.read_text()
            offs = [off_of(text, ln) for ln in elines]
            decls = fn_decls_covering(text, offs)
            p.write_text(inject_trusts(text, decls))
        DP.clean_in_dir(crate_dir, target)
    # fallback: trust every fn in every erroring file. Restore each file to its
    # ORIGINAL first (undo the per-fn injections) so whole-file trusting does not
    # stack a second #[flux_rs::trusted] on the fns per-fn already did — that
    # stacking is what produced the `duplicated attribute` errors.
    log(f"    unmask {pkg}: per-fn did not converge -> whole-file fallback")
    for relfile in by_file:
        p = ROOT / relfile
        if not p.exists():
            continue
        save(relfile)
        text = saved_originals[str(p)]          # original, pre-injection
        decls = {m.start() for m in NP.FN_DECL_RE.finditer(text)}
        p.write_text(inject_trusts(text, decls))
    DP.clean_in_dir(crate_dir, target)
    return "whole_file"


# --------------------------------------------------------------------------
# per-crate probing
# --------------------------------------------------------------------------
def probe_crate(pkg, precise, fn_level, log_dir, timeout, budget, will_unmask,
                saved_originals, log):
    crate_dir = PKGS[pkg]
    target = (log_dir / "target" / pkg).resolve()
    target.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + budget
    out = {"pkg": pkg, "stopped": None, "obligations": [], "flux_errors": [],
           "unmask": None}

    log(f"=== {pkg}: baseline ({len(precise)} precise, {len(fn_level)} fn-level) ===")
    base_log, b_att, b_verdict = DP.baseline_run(crate_dir, target, timeout, deadline)
    (log_dir / f"{pkg}.baseline.log").write_text(base_log or "")
    out["baseline_verdict"], out["baseline_attempts"] = b_verdict, b_att

    def emit_all(status, extra=None):
        for ob in precise:
            r = {"kind": "precise", **_pub_precise(ob), "status": status}
            if extra:
                r.update(extra)
            out["obligations"].append(r)
        for ob in fn_level:
            r = {"kind": "fn_level", **_pub_fn(ob), "status": status}
            if extra:
                r.update(extra)
            out["obligations"].append(r)

    if b_verdict in ("BUDGET", "TIMEOUT"):
        out["stopped"] = f"baseline {b_verdict.lower()}"
        emit_all("TIMEOUT" if b_verdict == "TIMEOUT" else "NOT_RUN")
        return out
    if b_verdict == "ICE":
        out["stopped"] = f"baseline ICE x{b_att}"
        emit_all("ICE_MASKED")
        return out
    dep = DP.dep_mask(base_log, pkg)
    if dep:
        out["stopped"] = f"dep-masked by `{dep}`"
        # A dep failing to compile blocks flux from reaching THIS crate's bodies.
        # But a `#[flux_rs::trusted]` written on the enclosing fn is a *static*,
        # declared carve-out (frontier, not vacuity) — its meaning doesn't depend
        # on flux having run. So honor it: a dep-masked obligation whose enclosing
        # fn is statically trusted is TRUSTED, not BLOCKED_DEP_MASKED.
        emit_dep_masked(precise, fn_level, dep, crate_dir, out)
        return out

    base_errs = NP.error_lines(base_log, crate_dir)
    full = parse_full_errors(base_log, crate_dir)

    # ---- precise obligations ----
    by_file = {}
    for ob in precise:
        by_file.setdefault(ob["file"], []).append(ob)
    for relfile, obs in by_file.items():
        p = ROOT / relfile
        orig = p.read_text()
        sites = {s["start_line"]: s for s in assert_sites(relfile, orig)}
        for ob in obs:
            r = {"kind": "precise", **_pub_precise(ob)}
            if time.time() > deadline:
                r["status"] = "NOT_RUN"
                out["obligations"].append(r)
                continue
            if ob.get("no_assert"):
                r["status"] = "SILENT"   # step-0 invariant says this can't happen
                r["probe"] = {"note": "no paired assert found below marker"}
                out["obligations"].append(r)
                continue
            site = sites.get(ob["assert_line"])
            if site is None:
                r["status"] = "SILENT"
                r["probe"] = {"note": "assert site not found at recorded line"}
                out["obligations"].append(r)
                continue
            rel = str(p.relative_to(ROOT / crate_dir))
            base_hit = NP.err_at(base_errs, rel, site)
            if site["inner"] == "false":
                # Dead-code sentinel `assert(false)`. base_hit => Flux flagged it =>
                # reachable => DEAD_NOT_PROVEN. Otherwise it's a DEAD_PROVEN *candidate*,
                # but silence at the sentinel is ambiguous: the code is genuinely dead
                # OR the body was never analyzed (vacuity, same failure mode as SILENT).
                # Confirm by ENTRY CONTROL: inject assert(false) at the enclosing fn's
                # body open (always reachable if the body is checked) and re-run.
                #   error in fn span  => body analyzed => DEAD_PROVEN (genuine dead code)
                #   still silent       => body unchecked => DEAD_SILENT (vacuous, gated)
                if base_hit:
                    r["status"] = "DEAD_NOT_PROVEN"
                    r["probe"] = {"baseline": "error_at_site"}
                elif NP.enclosing_fn_trusted(orig, site["call_off"]):
                    # silence is due to trust, not a dead-code proof -> declared carve-out
                    r["status"] = "TRUSTED"
                    r["probe"] = {"baseline": "pass", "enclosing_fn": "trusted", "sentinel": True}
                else:
                    encl = fn_decls_covering(orig, [site["call_off"]])
                    sl, el, body_open = fn_span(orig, max(encl)) if encl else (None, None, None)
                    if body_open is None:
                        r["status"] = "DEAD_SILENT"
                        r["probe"] = {"baseline": "pass", "entry_control": "no_enclosing_fn"}
                    else:
                        call = "crate::assert(false);" \
                            if relfile.lstrip("./").startswith("flux_support/") \
                            else "flux_support::assert(false);"
                        try:
                            p.write_text(orig[:body_open + 1] + " " + call + orig[body_open + 1:])
                            flog, att, verdict = DP.flip_run(crate_dir, target, timeout, deadline)
                        finally:
                            p.write_text(orig)
                        if verdict == "BUDGET":
                            r["status"] = "NOT_RUN"
                        elif verdict == "TIMEOUT":
                            r["status"] = "TIMEOUT"
                        elif verdict == "ICE":
                            r["status"] = "ICE_MASKED"
                        else:
                            errs = NP.error_lines(flog, crate_dir)
                            checked = any(rf.endswith(rel) and (el is None or sl <= ln <= el)
                                          for (rf, ln) in errs)
                            r["status"] = "DEAD_PROVEN" if checked else "DEAD_SILENT"
                            r["probe"] = {"baseline": "pass",
                                          "entry_control": "error_at_entry" if checked else "silent"}
            elif base_hit:
                r["status"] = "NOT_PROVEN"
                r["probe"] = {"baseline": "error_at_site"}
            elif NP.enclosing_fn_trusted(orig, site["call_off"]):
                r["status"] = "TRUSTED"
                r["probe"] = {"baseline": "pass", "enclosing_fn": "trusted"}
            else:
                try:
                    p.write_text(NP.flip_text(orig, site))
                    flog, att, verdict = DP.flip_run(crate_dir, target, timeout, deadline)
                finally:
                    p.write_text(orig)
                if verdict == "BUDGET":
                    r["status"] = "NOT_RUN"
                elif verdict == "TIMEOUT":
                    r["status"] = "TIMEOUT"
                elif verdict == "ICE":
                    r["status"] = "ICE_MASKED"
                else:
                    ferrs = NP.error_lines(flog, crate_dir)
                    hit = NP.err_at(ferrs, rel, site)
                    r["status"] = "PROVEN" if hit else "SILENT"
                r["probe"] = {"baseline": "pass",
                              "flip": "error_at_site" if r["status"] == "PROVEN" else verdict.lower(),
                              "attempts": att}
            out["obligations"].append(r)
            log(f"    {rel}:{ob['assert_line']:<5} {r['status']:<16} precise")

    # ---- fn-level obligations (entry control) ----
    for ob in fn_level:
        r = {"kind": "fn_level", **_pub_fn(ob)}
        if time.time() > deadline:
            r["status"] = "NOT_RUN"
            out["obligations"].append(r)
            continue
        p = ROOT / ob["file"]
        orig = p.read_text()
        rel = str(p.relative_to(ROOT / crate_dir))
        decl = fn_decl_after(orig, ob["marker_line"])
        if decl is None:
            r["status"] = "SILENT"
            r["probe"] = {"note": "no fn decl found below marker"}
            out["obligations"].append(r)
            continue
        sl, el, body_open = fn_span(orig, decl.start())
        in_fn = lambda errs: any(rf.endswith(rel) and (el is None or sl <= ln <= el)
                                 for (rf, ln) in errs)
        if NP.enclosing_fn_trusted(orig, decl.start()):
            r["status"] = "TRUSTED"
            r["probe"] = {"enclosing_fn": "trusted"}
        elif in_fn(base_errs):
            r["status"] = "NOT_PROVEN"
            r["probe"] = {"baseline": "error_in_fn"}
        elif body_open is None:
            r["status"] = "SILENT"
            r["probe"] = {"note": "could not locate fn body"}
        else:
            inject = orig[:body_open + 1] + " flux_support::assert(false);" + orig[body_open + 1:]
            try:
                p.write_text(inject)
                flog, att, verdict = DP.flip_run(crate_dir, target, timeout, deadline)
            finally:
                p.write_text(orig)
            if verdict == "BUDGET":
                r["status"] = "NOT_RUN"
            elif verdict == "TIMEOUT":
                r["status"] = "TIMEOUT"
            elif verdict == "ICE":
                r["status"] = "ICE_MASKED"
            else:
                checked = in_fn(NP.error_lines(flog, crate_dir))
                r["status"] = "PROVEN" if checked else "SILENT"
            r["probe"] = {"baseline": "pass",
                          "entry_control": "error_at_entry" if r["status"] == "PROVEN" else "silent"}
        out["obligations"].append(r)
        log(f"    {rel}:{ob['marker_line']:<5} {r['status']:<16} fn-level")

    # ---- full error inventory (classify) ----
    assert_lines = set()
    for ob in precise:
        if ob.get("assert_line"):
            assert_lines.add((ob["file"].split("/")[-1], ob["assert_line"]))
    for e in full:
        is_assert = any(e["file"].endswith(f) and abs(e["line"] - ln) <= 1
                        for (f, ln) in assert_lines)
        out["flux_errors"].append({
            "crate": pkg, "code": e["code"], "file": e["file"], "line": e["line"],
            "col": e["col"], "message": e["message"],
            "category": "assert_obligation" if is_assert else "other_obligation"})

    if any(o["status"] == "NOT_RUN" for o in out["obligations"]):
        out["stopped"] = f"budget {budget}s exceeded"

    # ---- dependency unmasking (leave trusts applied for dependents) ----
    if will_unmask:
        out["unmask"] = unmask_crate(pkg, target, timeout, deadline, full,
                                     saved_originals, log)
    return out


def _pub_precise(ob):
    return {"obligation": {"file": ob["file"], "assert_line": ob.get("assert_line"),
                           "predicate": ob.get("predicate")},
            "covers": {"addrs": ob["addrs"], "markers": ob["markers"],
                       "source": ob["sources"][0] if ob["sources"] else None}}


def _pub_fn(ob):
    return {"obligation": {"file": ob["file"], "func": ob["func"], "predicate": None},
            "covers": {"addrs": ob["addrs"], "markers": ob["markers"],
                       "source": ob["sources"][0] if ob["sources"] else None}}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", type=Path, default=ROOT / "tools/invariant1_report.json")
    ap.add_argument("--out", type=Path, default=ROOT / "tools/invariant2_report.json")
    ap.add_argument("--log-dir", type=Path, default=ROOT / "tools/invariant2_logs")
    ap.add_argument("--crates", nargs="+", help="subset of pkgs (default: all w/ obligations)")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--budget", type=int, default=900)
    args = ap.parse_args()
    args.log_dir.mkdir(parents=True, exist_ok=True)

    if not args.report.exists():
        print(f"error: {args.report} missing — run step 1 first", file=sys.stderr)
        return 2
    report = json.loads(args.report.read_text())
    if report.get("summary", {}).get("by_status", {}).get("missing_comment", 0):
        print("error: step 1 has missing_comment entries — not exit-0; fix step 1 first",
              file=sys.stderr)
        return 2

    precise, fn_level = derive_obligations(report)

    # bucket obligations by pkg
    per_pkg = {}
    skipped = []
    for ob in precise:
        pkg = pkg_of_file(ob["file"])
        (per_pkg.setdefault(pkg, ([], []))[0].append(ob) if pkg
         else skipped.append(ob["file"]))
    for ob in fn_level:
        pkg = pkg_of_file(ob["file"])
        (per_pkg.setdefault(pkg, ([], []))[1].append(ob) if pkg
         else skipped.append(ob["file"]))
    per_pkg.pop(None, None)

    pkgs = list(per_pkg)
    if args.crates:
        pkgs = [p for p in pkgs if p in args.crates]
    order = [p for p in topo_order(pkgs) if p in pkgs]

    n_prec = sum(len(per_pkg[p][0]) for p in order)
    n_fn = sum(len(per_pkg[p][1]) for p in order)
    n_markers = sum(1 for o in report["obligations"] if o["status"] == "comment_precise")
    print(f"probing {n_prec + n_fn} obligations: {n_prec} precise "
          f"(from {n_markers} precise markers), {n_fn} fn-level, "
          f"across {len(order)} crates: {order}", flush=True)
    if skipped:
        print(f"  note: {len(skipped)} obligation(s) in crates outside the pkg map, skipped",
              flush=True)

    saved_originals = {}
    crate_results = {}
    t_start = time.time()
    try:
        for pkg in order:
            prec, fnl = per_pkg[pkg]
            will_unmask = bool(transitive_dependents(pkg, pkgs) & set(order))
            t0 = time.time()
            crate_results[pkg] = probe_crate(pkg, prec, fnl, args.log_dir,
                                             args.timeout, args.budget, will_unmask,
                                             saved_originals, lambda m: print(m, flush=True))
            crate_results[pkg]["wall_s"] = round(time.time() - t0, 1)
            _write(args, report, crate_results, t_start)  # checkpoint
            print(f"  [{pkg}] done in {crate_results[pkg]['wall_s']}s", flush=True)
    finally:
        # restore every temporarily-trusted file (never git stash)
        for path, text in saved_originals.items():
            Path(path).write_text(text)
        if saved_originals:
            print(f"restored {len(saved_originals)} unmask-trusted file(s)", flush=True)

    rep = _write(args, report, crate_results, t_start)

    # tree-clean safety
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "*.rs", "*Cargo.toml"],
                           capture_output=True, text=True, cwd=str(ROOT)).stdout
    src = [l for l in dirty.splitlines() if l.strip().endswith((".rs", "Cargo.toml"))]
    if src:
        print("\n!!! dirty .rs after run (NOT auto-reverting — likely your uncommitted work):",
              *src, sep="\n   ")

    g = rep["summary"]
    print(f"\nsummary: discharged={g['discharged']} frontier={g['frontier']} "
          f"vacuous={g['vacuous']}  gate={g['gate']}")
    print(f"flux_errors: total={g['flux_errors']['total']} "
          f"assert={g['flux_errors']['assert_obligation']} "
          f"other={g['flux_errors']['other_obligation']}")
    print(f"wrote {args.out}")
    return 0 if g["gate"] == "pass" else 1


def _write(args, report, crate_results, t_start):
    obligations, flux_errors = [], []
    by_status = {}
    for pkg, r in crate_results.items():
        for o in r["obligations"]:
            obligations.append(o)
            by_status[o["status"]] = by_status.get(o["status"], 0) + 1
        flux_errors.extend(r.get("flux_errors", []))
    discharged = sum(by_status.get(s, 0) for s in DISCHARGED)
    frontier = sum(by_status.get(s, 0) for s in FRONTIER)
    vacuous = sum(by_status.get(s, 0) for s in VACUOUS)
    fe = {"total": len(flux_errors),
          "assert_obligation": sum(1 for e in flux_errors if e["category"] == "assert_obligation"),
          "other_obligation": sum(1 for e in flux_errors if e["category"] == "other_obligation")}
    rep = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t_start)),
        "inputs": {"invariant1_report": str(args.report),
                   "panic_survey_json": str(report.get("inputs", {}).get("panic_survey_json", ""))},
        "summary": {"total_obligations": len(obligations), "by_status": by_status,
                    "discharged": discharged, "frontier": frontier, "vacuous": vacuous,
                    "gate": "pass" if vacuous == 0 else "fail", "flux_errors": fe},
        "crates": {p: {k: v for k, v in r.items() if k != "obligations" and k != "flux_errors"}
                   for p, r in crate_results.items()},
        "obligations": obligations,
        "flux_errors": flux_errors,
    }
    args.out.write_text(json.dumps(rep, indent=2))
    return rep


if __name__ == "__main__":
    sys.exit(main())
