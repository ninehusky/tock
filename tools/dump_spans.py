#!/usr/bin/env python3
"""
dump_spans.py — emit every probed assert as a clickable repo-relative span,
annotated with category + why-it's-silent, for investigation / sharing with the
Flux maintainer. Reads tools/negation_probe.json (+ dead_proven_validate.json if
present). Writes tools/flux_spans.tsv (grep-friendly) and tools/flux_silent_spans.md
(grouped: closure vs in-scope-non-closure vs out-of-scope).
"""
import json, re, pathlib, tomllib

CR = {"kernel":"kernel","tock-cells":"libraries/tock-cells","tickv":"libraries/tickv",
      "cortexm":"arch/cortex-m","cortexv7m":"arch/cortex-v7m","capsules-core":"capsules/core",
      "capsules-extra":"capsules/extra","nrf52":"chips/nrf52","nrf52840":"chips/nrf52840","nrf5x":"chips/nrf5x"}
ASSERT="flux_support::assert("
CLOSURE_HDR=re.compile(r"\|[^|\n]*\|\s*$")
CLOSURE_CALL=re.compile(r"\.\s*(map|map_or|map_or_else|and_then|unwrap_or_else|inspect|filter|for_each|map_err)\s*\(\s*(move\s*)?\|")
FN=re.compile(r"(?m)^[ \t]*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+|unsafe\s+|async\s+|extern\s+\"[^\"]*\"\s+)*fn\s+(\w+)")

def incl(pkg):
    t=tomllib.load(open(pathlib.Path(CR[pkg])/"Cargo.toml","rb"))
    inc=t.get("package",{}).get("metadata",{}).get("flux",{}).get("include",None)
    if inc is None: return ("WHOLE",set(),set())
    return ("FILTER",{x for x in inc if not x.startswith(("def:","span:"))},{x[4:] for x in inc if x.startswith("def:")})

def is_closure(text,off):
    depth=0;i=off
    while i>0:
        c=text[i]
        if c=='}':depth+=1
        elif c=='{':
            if depth==0:
                pre=text[max(0,i-80):i]; return bool(CLOSURE_HDR.search(pre.rstrip())) or bool(CLOSURE_CALL.search(pre))
            depth-=1
        i-=1
    return False

def enc_fn(text,off):
    last=None
    for m in FN.finditer(text):
        if m.start()<off: last=m.group(1)
        else: break
    return last

d=json.load(open("tools/negation_probe.json"))
dead={}
try: dead=json.load(open("tools/dead_proven_validate.json"))
except FileNotFoundError: pass
dead_verdict={(p,r["file"],r["line"]):r["verdict"] for p,rows in dead.items() for r in rows}

rows=[]
for pkg,r in d.items():
    mode,files,defs=incl(pkg); cd=pathlib.Path(CR[pkg])
    byf={}
    for s in r["sites"]: byf.setdefault(s["file"],[]).append(s)
    for f,sites in byf.items():
        t=(cd/f).read_text(errors="ignore")
        offs=[m.start() for m in re.finditer(re.escape(ASSERT),t)]
        for s in sites:
            o=next((x for x in offs if t.count("\n",0,x)+1==s["line"]),None)
            # skip commented-out asserts (probe counted these as phantom sites)
            lt=t.splitlines()
            if s["line"]-1 < len(lt):
                L=lt[s["line"]-1]; ci=L.find(ASSERT)
                if ci!=-1 and "//" in L[:ci]: continue
            fn=enc_fn(t,o) if o is not None else None
            clo=is_closure(t,o) if o is not None else False
            insc=(mode=="WHOLE") or (f in files) or any(dn in (fn or "") for dn in defs)
            cat=s["category"]
            if cat=="DEAD_PROVEN":
                cat=dead_verdict.get((pkg,f,s["line"]),"DEAD_PROVEN")
            why=""
            if s["category"]=="SILENT":
                why="closure" if clo else ("out_of_scope" if not insc else "in_scope_skipped")
            rows.append({"crate":pkg,"path":f"{CR[pkg]}/{f}:{s['line']}","fn":fn or "?",
                         "category":cat,"why":why,"closure":clo,"in_scope":insc,"cond":s["inner"]})

# TSV
with open("tools/flux_spans.tsv","w") as fh:
    fh.write("crate\tpath\tfn\tcategory\twhy_silent\tclosure\tin_scope\tcondition\n")
    for r in rows:
        fh.write(f"{r['crate']}\t{r['path']}\t{r['fn']}\t{r['category']}\t{r['why']}\t{r['closure']}\t{r['in_scope']}\t{r['cond']}\n")

# Markdown — every NOT-genuinely-proven assert (cmd-click the path:line)
sil=[r for r in rows if r["category"]=="SILENT"]
vac=[r for r in rows if r["category"]=="DEAD_VACUOUS"]
trb=[r for r in rows if r["category"]=="TRUSTED_BLOCKED"]
def grp(w): return [r for r in sil if r["why"]==w]
def emit(fh, items):
    for r in sorted(items,key=lambda x:x["path"]):
        fh.write(f"- {r['path']}  —  `fn {r['fn']}`  —  `{r['cond']}`\n")
with open("tools/flux_silent_spans.md","w") as fh:
    fh.write("# Asserts Flux does NOT genuinely check\n\n")
    fh.write(f"From `tools/negation_probe.json` (+ `dead_proven_validate.json`), commented-out "
             f"sites excluded. cmd-click any `path:line`.\n\n")
    fh.write(f"**Silent/vacuous total: {len(sil)+len(vac)}** "
             f"= SILENT {len(sil)} (closure {len(grp('closure'))} + in-scope-skipped "
             f"{len(grp('in_scope_skipped'))} + out-of-scope {len(grp('out_of_scope'))}) "
             f"+ vacuous-dead {len(vac)}. Plus {len(trb)} trusted-blocked (appendix).\n\n")
    fh.write("> Note: the `closure` count is a LOWER bound — the detector misses closures "
             "nested in match-arms, so some 'in-scope-skipped' below may actually be closures.\n")
    fh.write("> The 'in-scope-skipped' set includes the `#[flux_rs::sig]`-with-unsatisfiable-"
             "`requires` vacuity (e.g. framer.rs incoming_frame_security).\n\n")
    for w,title in [("closure","## SILENT — inside a closure (`cell.map(|x| {…})`) — confirmed Flux bug"),
                    ("in_scope_skipped","## SILENT — in-scope, NOT a closure, still skipped"),
                    ("out_of_scope","## SILENT — out of include scope (fn not in include filter)")]:
        g=grp(w)
        fh.write(f"\n{title}  ({len(g)})\n\n"); emit(fh,g)
    fh.write(f"\n## DEAD_VACUOUS — `assert(false)` sentinels whose body Flux never checks  ({len(vac)})\n\n")
    emit(fh,vac)
    fh.write(f"\n## APPENDIX: TRUSTED_BLOCKED — inside `#[flux_rs::trusted]` fns (blocked_cell/blocked_ice)  ({len(trb)})\n\n")
    emit(fh,trb)

print(f"wrote tools/flux_spans.tsv ({len(rows)} asserts) and tools/flux_silent_spans.md")
print(f"SILENT {len(sil)} (closure={len(grp('closure'))} in_scope_skipped={len(grp('in_scope_skipped'))} "
      f"out_of_scope={len(grp('out_of_scope'))}) + DEAD_VACUOUS {len(vac)} = {len(sil)+len(vac)} silent/vacuous; "
      f"TRUSTED_BLOCKED {len(trb)}")
