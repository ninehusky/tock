import csv
import json
import os
from collections import defaultdict

SHA = "9548b2ed92b4da0094fe6c11b8ea3e4a273988de"
REPO = "ninehusky/tock"

MD_PATH = '/Users/andrew/research/tock/tools/panic_sites.md'
CSV_PATH = '/Users/andrew/research/tock/tools/panic_sites.csv'
DEFAULT_STATUS = 'not started'
VALID_STATUSES = {'not started', 'locally proven', 'caller-proven', 'callee-proven'}

with open('/Users/andrew/research/tock/tools/panic_survey.json') as f:
    data = json.load(f)
sites = data['sites']
meta = data['meta']

# Merge user-edited fields (Status, Assignee) from the existing CSV so regen
# doesn't clobber claims/progress. Keyed by address.
prior = {}
if os.path.exists(CSV_PATH):
    with open(CSV_PATH) as f:
        for row in csv.DictReader(f):
            addr = row.get('Addr', '').strip().strip('`')
            if not addr:
                continue
            status = (row.get('Status') or '').strip() or DEFAULT_STATUS
            if status not in VALID_STATUSES:
                status = DEFAULT_STATUS
            prior[addr] = {
                'status': status,
                'assignee': (row.get('Assignee') or '').strip(),
            }

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
out.append("Each panic site has a verification status (orthogonal to `Blockers`; "
           "ownership lives in `Assignee`):\n"
           "- `not started`: no verification work has begun.\n"
           "- `locally proven`: the panic site is proven _locally_, i.e., the "
           "enclosing function is annotated with some precondition indicating "
           "that the panic won't hit. The precondition has not yet been "
           "discharged at call sites.\n"
           "- `caller-proven`: every transitive caller in the linked binary "
           "(`nrf52840dk`) has been checked to ensure the precondition holds, "
           "so the panic won't hit in this build.\n"
           "- `callee-proven` (a.k.a. `no-panic`): the enclosing function "
           "provably cannot panic — no caller obligation needed.\n")
out.append("**Columns:**  `Addr` is the panic-site address in the linked binary. "
           "`Flavor` is the panic kind (explicit panic, slice OOB, div by zero, …). "
           "`Location` links to the deepest user-code frame (sites that bottom out "
           "in libcore are shown unlinked). `Source` is the offending line, when "
           "available. `Blockers` are tags from the survey indicating what stops "
           "removal today (e.g. `blocked_cell` = needs Cell-state invariant). "
           "`Status` is one of the verification states above. Fill in `Assignee` "
           "to claim a row.\n")

# Summary row
out.append("## Summary\n")
out.append("| Module | Sites |")
out.append("|---|---:|")
for m in ordered:
    out.append(f"| [{m}](#{m.replace('/', '').replace(' ', '-')}) | {len(by_mod[m])} |")
out.append(f"| **total** | **{len(sites)}** |\n")

# Per-module tables; also collect rows for CSV emission.
csv_rows = []
for m in ordered:
    anchor = m.replace('/', '').replace(' ', '-')
    out.append(f"## {m}  <a id=\"{anchor}\"></a>\n")
    out.append("| Addr | Flavor | Location | Source | Notes | Blockers | Status | Assignee |")
    out.append("|---|---|---|---|---|---|---|---|")
    for s in sorted(by_mod[m], key=sort_key):
        addr = s['address']
        carry = prior.get(addr, {})
        status = carry.get('status', DEFAULT_STATUS)
        assignee = carry.get('assignee', '')
        row = [
            f"`{addr}`",
            f"`{s['sink_flavor']}`",
            fmt_location(s),
            fmt_source(s['effective_source']),
            md_escape(s.get('notes') or ""),
            fmt_blockers(s.get('blockers', [])),
            status,
            assignee,
        ]
        out.append("| " + " | ".join(row) + " |")
        csv_rows.append({
            'Module': m,
            'Addr': f"`{addr}`",
            'Flavor': f"`{s['sink_flavor']}`",
            'Location': fmt_location(s),
            'Source': fmt_source(s['effective_source']),
            'Notes': (s.get('notes') or '').replace('\n', ' ').replace('\r', ''),
            'Blockers': fmt_blockers(s.get('blockers', [])),
            'Status': status,
            'Assignee': assignee,
        })
    out.append("")

with open(MD_PATH, 'w') as f:
    f.write("\n".join(out))

# Sort CSV: blocker-empty first, then by blocker, module, location.
def csv_key(r):
    b = r['Blockers'].strip()
    return (0 if b == '' else 1, b, r['Module'], r['Location'])
csv_rows.sort(key=csv_key)

with open(CSV_PATH, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=['Module', 'Addr', 'Flavor', 'Location',
                                       'Source', 'Notes', 'Blockers',
                                       'Status', 'Assignee'])
    w.writeheader()
    w.writerows(csv_rows)

print(f"Wrote {len(sites)} rows across {len(ordered)} module sections.")
print(f"Carried over status/assignee for {sum(1 for a in (s['address'] for s in sites) if a in prior)} sites.")
