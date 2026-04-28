import json
from collections import defaultdict

SHA = "9548b2ed92b4da0094fe6c11b8ea3e4a273988de"
REPO = "ninehusky/tock"

with open('/Users/andrew/research/tock/tools/panic_survey.json') as f:
    data = json.load(f)
sites = data['sites']
meta = data['meta']

def gh_link(file_path, line):
    if not file_path.startswith('./'):
        return None
    rel = file_path[2:]
    if line is None:
        return f"https://github.com/{REPO}/blob/{SHA}/{rel}"
    return f"https://github.com/{REPO}/blob/{SHA}/{rel}#L{line}"

def short_loc(file_path, line):
    p = file_path[2:] if file_path.startswith('./') else file_path
    return f"{p}:{line}" if line is not None else p

def md_escape(s):
    if s is None:
        return ""
    # Pipes break tables; collapse newlines.
    return s.replace('|', '\\|').replace('\n', ' ').replace('\r', '')

def fmt_source(src, limit=90):
    if src is None:
        return ""
    src = src.strip()
    if len(src) > limit:
        src = src[:limit-1] + "…"
    return f"`{md_escape(src)}`"

def fmt_blockers(bs):
    if not bs:
        return ""
    return ", ".join(f"`{b}`" for b in bs)

def fmt_location(s):
    f = s['effective_frame']
    label = short_loc(f['file'], f['line'])
    url = gh_link(f['file'], f['line'])
    if url is None:
        # No user-code frame — show inline-chain hint if any user frame exists.
        for frame in s.get('inline_chain', []):
            if frame['file'].startswith('./'):
                u2 = gh_link(frame['file'], frame['line'])
                inner_label = short_loc(frame['file'], frame['line'])
                return f"_{md_escape(label)}_<br/>via [{md_escape(inner_label)}]({u2})"
        return f"_{md_escape(label)}_"
    return f"[{md_escape(label)}]({url})"

# Group by module
by_mod = defaultdict(list)
for s in sites:
    by_mod[s['module_bucket']].append(s)

# Stable ordering across modules
MOD_ORDER = ['capsules/core', 'capsules/extra', 'chips', 'kernel', 'arch',
             'boards', 'libraries', 'stdlib']
ordered = [m for m in MOD_ORDER if m in by_mod] + \
          [m for m in by_mod if m not in MOD_ORDER]

def sort_key(s):
    f = s['effective_frame']
    return (f['file'], f['line'] if f['line'] is not None else -1, s['address'])

out = []
out.append(f"# Panic call sites — assignment table\n")
out.append(f"Generated from `tools/panic_survey.json` "
           f"(binary `{meta['binary'].rsplit('/',1)[-1]}`, "
           f"{meta['total_sites']} sites). "
           f"Permalinks pin to commit "
           f"[`{SHA[:9]}`](https://github.com/{REPO}/commit/{SHA}). "
           "Bump the `SHA` constant in `tools/gen_panic_sites_md.py` "
           "and regenerate if files have moved.\n")
out.append("**Columns:**  `Addr` is the panic-site address in the linked binary. "
           "`Flavor` is the panic kind (explicit panic, slice OOB, div by zero, …). "
           "`Location` links to the deepest user-code frame (sites that bottom out "
           "in libcore are shown unlinked). `Source` is the offending line, when "
           "available. `Blockers` are tags from the survey indicating what stops "
           "removal today (e.g. `blocked_cell` = needs Cell-state invariant). "
           "Fill in `Assignee` to claim a row.\n")

# Summary row
out.append("## Summary\n")
out.append("| Module | Sites |")
out.append("|---|---:|")
for m in ordered:
    out.append(f"| [{m}](#{m.replace('/', '').replace(' ', '-')}) | {len(by_mod[m])} |")
out.append(f"| **total** | **{len(sites)}** |\n")

# Per-module tables
for m in ordered:
    anchor = m.replace('/', '').replace(' ', '-')
    out.append(f"## {m}  <a id=\"{anchor}\"></a>\n")
    out.append("| Addr | Flavor | Location | Source | Notes | Blockers | Assignee |")
    out.append("|---|---|---|---|---|---|---|")
    for s in sorted(by_mod[m], key=sort_key):
        row = [
            f"`{s['address']}`",
            f"`{s['sink_flavor']}`",
            fmt_location(s),
            fmt_source(s['effective_source']),
            md_escape(s.get('notes') or ""),
            fmt_blockers(s.get('blockers', [])),
            "",
        ]
        out.append("| " + " | ".join(row) + " |")
    out.append("")

with open('/Users/andrew/research/tock/tools/panic_sites.md', 'w') as f:
    f.write("\n".join(out))

print(f"Wrote {len(sites)} rows across {len(ordered)} module sections.")
