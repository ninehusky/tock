import re, os, subprocess
import tomllib

def find_crate_root(filepath):
    d = os.path.dirname(os.path.abspath(filepath))
    while d != '/':
        if os.path.exists(os.path.join(d, 'Cargo.toml')):
            return d
        d = os.path.dirname(d)
    return None

def extract_fn_name(lines, line_idx):
    fn_pat = re.compile(r'^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+|unsafe\s+|const\s+|extern\s+(?:"[^"]*"\s+)?)*\s*fn\s+([a-zA-Z_][a-zA-Z0-9_]*)')
    for i in range(line_idx, -1, -1):
        m = fn_pat.match(lines[i])
        if m:
            return m.group(1)
    return None

def parse_includes(cargo_path):
    """Returns ('whole-crate' | 'has-include', set_of_includes). None if no flux metadata."""
    with open(cargo_path, 'rb') as f:
        data = tomllib.load(f)
    flux = data.get('package', {}).get('metadata', {}).get('flux')
    if flux is None:
        return None
    if not flux.get('enabled', False):
        return None
    inc = flux.get('include')
    if inc is None:
        return ('whole-crate', set())
    return ('has-include', set(inc))

result = subprocess.run(['grep', '-rln', '// FLUX-TODO', '--include=*.rs'], capture_output=True, text=True)
files = result.stdout.strip().split('\n')

per_crate = {}
for fp in files:
    if not fp.strip(): continue
    crate_root = find_crate_root(fp)
    if not crate_root: continue
    crate_rel = os.path.relpath(crate_root, os.getcwd())
    file_rel = os.path.relpath(os.path.abspath(fp), crate_root)
    with open(fp) as f:
        lines = f.readlines()
    fns = set()
    for i, line in enumerate(lines):
        if '// FLUX-TODO' in line:
            fn = extract_fn_name(lines, i)
            if fn: fns.add(fn)
    per_crate.setdefault(crate_rel, {})[file_rel] = fns

ok = True
for crate_rel in sorted(per_crate.keys()):
    parsed = parse_includes(os.path.join(crate_rel, 'Cargo.toml'))
    if parsed is None:
        print(f'{crate_rel}: NO FLUX METADATA')
        ok = False
        continue
    style, inc = parsed
    if style == 'whole-crate':
        continue
    for f, fns in per_crate[crate_rel].items():
        if f in inc:
            continue
        # check def: coverage
        uncovered = []
        for fn in fns:
            if not any(d.startswith('def:') and d[4:] in fn for d in inc):
                uncovered.append(fn)
        if uncovered:
            print(f'{crate_rel}/{f}: missing fns: {sorted(uncovered)}')
            ok = False
if ok:
    print('All FLUX-TODO functions are included.')
