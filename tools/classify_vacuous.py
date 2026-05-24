#!/usr/bin/env python3
"""
classify_vacuous.py — break the vacuous/silent asserts into hard sub-causes.

Input: tools/negation_probe.json (+ dead_proven_validate.json). Considers every
SILENT and DEAD_VACUOUS site (commented-out phantoms excluded). Assigns ONE
primary cause, in priority order:

  TRUSTED      enclosing fn (or its impl block) is #[flux_rs::trusted] — NOT a
               silent-skip bug, just blocked; my earlier 400-char window missed
               long-reason attrs and mislabeled these as silent.
  CLOSURE      assert is inside a closure block (any enclosing block, not just
               the innermost) — the maintainer-confirmed closure non-checking bug.
  TRAIT_IMPL   method of a trait impl (`impl Trait for T`) with no closure/trust —
               the `read_region`/tickv-trait shape we reproduced.
  SIG_EDGE     free/inherent fn that carries a #[flux_rs::sig] — candidate for the
               unsatisfiable-`requires` vacuity (framer) or no_panic_if gap (stream).
  UNEXPLAINED  free/inherent fn, no sig, not a closure/trusted — these SHOULD check
               per the minimal repro, so they need direct re-probing.
"""
import csv, json, re, pathlib
from collections import Counter

CR={"kernel":"kernel","tock-cells":"libraries/tock-cells","tickv":"libraries/tickv","cortexm":"arch/cortex-m",
    "cortexv7m":"arch/cortex-v7m","capsules-core":"capsules/core","capsules-extra":"capsules/extra",
    "nrf52":"chips/nrf52","nrf52840":"chips/nrf52840","nrf5x":"chips/nrf5x"}
ASSERT="flux_support::assert("
FN=re.compile(r"(?m)^[ \t]*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+|unsafe\s+|async\s+|extern\s+\"[^\"]*\"\s+)*fn\s+\w+")
IMPL=re.compile(r"(?m)^[ \t]*impl\b[^\{]*")
CLOSURE_HDR=re.compile(r"\|[^|\n]*\|\s*$")
CLOSURE_CALL=re.compile(r"\.\s*(map|map_or|map_or_else|and_then|unwrap_or_else|inspect|filter|for_each|map_err|each|enter)\s*\(\s*(move\s*)?\|")

def is_commented(t, line):
    ls=t.splitlines()
    if line-1>=len(ls): return False
    L=ls[line-1]; ci=L.find(ASSERT); return ci!=-1 and "//" in L[:ci]

def enclosing_fn_start(t, off):
    last=None
    for m in FN.finditer(t):
        if m.start()<off: last=m
        else: break
    return last

def trusted(t, off):
    """line-based: scan contiguous attr/comment/blank lines above the enclosing fn
    (and the enclosing impl header) for flux_rs::trusted."""
    fn=enclosing_fn_start(t, off)
    if not fn: return False
    lines=t[:fn.start()].splitlines()
    i=len(lines)-1
    block=[]
    while i>=0:
        s=lines[i].strip()
        if s=="" or s.startswith("//") or s.startswith("#[") or s.startswith("#!") \
           or s.startswith(")") or s.startswith("]") or "=" in s or s.endswith(",") or s.startswith('"'):
            block.append(lines[i]); i-=1
        else:
            break
    if any("flux_rs::trusted" in b for b in block): return True
    # enclosing impl header trusted?
    impl=None
    for m in IMPL.finditer(t):
        if m.start()<fn.start(): impl=m
        else: break
    if impl:
        above=t[max(0,impl.start()-300):impl.start()]
        if "flux_rs::trusted" in above: return True
    return False

def in_closure(t, off):
    """walk ALL enclosing blocks; True if any is a closure block."""
    depth=0; i=off
    while i>0:
        c=t[i]
        if c=='}': depth+=1
        elif c=='{':
            if depth==0:
                pre=t[max(0,i-90):i].rstrip()
                if CLOSURE_HDR.search(pre) or CLOSURE_CALL.search(pre): return True
                # step outside this block and keep walking up
                i-=1; continue
            depth-=1
        i-=1
    return False

def impl_kind(t, off):
    fn=enclosing_fn_start(t, off)
    if not fn: return "free_fn"
    impl=None
    for m in IMPL.finditer(t):
        if m.start()<fn.start(): impl=m.group(0)
        else: break
    if impl: return "trait_method" if " for " in impl else "inherent"
    return "free_fn"

def has_sig(t, off):
    fn=enclosing_fn_start(t, off)
    if not fn: return False
    lines=t[:fn.start()].splitlines()
    i=len(lines)-1; block=[]
    while i>=0:
        s=lines[i].strip()
        if s=="" or s.startswith(("//","#[","#!",")","]",'"')) or "=" in s or s.endswith(","):
            block.append(lines[i]); i-=1
        else: break
    return any("flux_rs::sig" in b for b in block)

def main():
    probe=json.load(open("tools/negation_probe.json"))
    dead=json.load(open("tools/dead_proven_validate.json"))
    verdict={(p,r["file"],r["line"]):r["verdict"] for p,rows in dead.items() for r in rows}
    cause=Counter(); rows_out=[]
    for pkg,r in probe.items():
        cd=CR[pkg]
        for s in r["sites"]:
            cat=s["category"]
            if cat=="DEAD_PROVEN": cat=verdict.get((pkg,s["file"],s["line"]),"DEAD_PROVEN")
            if cat not in ("SILENT","DEAD_VACUOUS"): continue
            t=pathlib.Path(cd+"/"+s["file"]).read_text(errors="ignore")
            if is_commented(t, s["line"]): continue
            off=None
            for m in re.finditer(re.escape(ASSERT), t):
                if t.count("\n",0,m.start())+1==s["line"]: off=m.start(); break
            if off is None: continue
            if trusted(t,off): c="TRUSTED(mislabeled)"
            elif in_closure(t,off): c="CLOSURE"
            elif impl_kind(t,off)=="trait_method": c="TRAIT_IMPL_skip"
            elif has_sig(t,off): c="SIG_EDGE"
            else: c="UNEXPLAINED(free/inherent,no-sig)"
            cause[c]+=1
            rows_out.append((c,pkg,f"{cd}/{s['file']}:{s['line']}",s["inner"][:48]))
    total=sum(cause.values())
    print(f"=== {total} vacuous/silent asserts, by primary cause ===")
    for c,n in cause.most_common(): print(f"  {c:<32} {n}")
    pathlib.Path("tools/vacuous_breakdown.tsv").write_text(
        "cause\tcrate\tpath\tcondition\n"+"\n".join("\t".join(x) for x in sorted(rows_out)))
    print("\nwrote tools/vacuous_breakdown.tsv")
    print("\nUNEXPLAINED (should check per repro — need direct re-probe):")
    for c,pkg,path,inner in sorted(rows_out):
        if c.startswith("UNEXPLAINED"): print(f"  {path}  {inner}")

if __name__=="__main__": main()
