import re
import os
import subprocess

# Map file path → crate root (where Cargo.toml lives)
def find_crate_root(filepath):
    # Walk up to find Cargo.toml
    d = os.path.dirname(os.path.abspath(filepath))
    while d != '/':
        if os.path.exists(os.path.join(d, 'Cargo.toml')):
            return d
        d = os.path.dirname(d)
    return None

def extract_fn_name_before(lines, line_idx):
    """Find the enclosing fn definition before line_idx (0-indexed)."""
    # Walk backwards looking for `fn <name>` 
    fn_pat = re.compile(r'^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+|unsafe\s+|const\s+|extern\s+(?:"[^"]*"\s+)?)*\s*fn\s+([a-zA-Z_][a-zA-Z0-9_]*)')
    brace_depth = 0
    for i in range(line_idx, -1, -1):
        # Count braces from the marker line backwards
        line = lines[i]
        # Track brace nesting (rough)
        # Look for fn declarations
        m = fn_pat.match(line)
        if m:
            return m.group(1)
    return None

# Collect FLUX-TODO functions
result = subprocess.run(['grep', '-rln', '// FLUX-TODO', '--include=*.rs'], capture_output=True, text=True)
files = result.stdout.strip().split('\n')

per_crate = {}  # crate_root -> set of (file_rel, fn_name)
for fp in files:
    if not fp.strip():
        continue
    crate_root = find_crate_root(fp)
    if not crate_root:
        continue
    abs_fp = os.path.abspath(fp)
    file_rel = os.path.relpath(abs_fp, crate_root)
    with open(fp) as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        if '// FLUX-TODO' in line:
            fn_name = extract_fn_name_before(lines, i)
            per_crate.setdefault(crate_root, set()).add((file_rel, fn_name))

# Print per-crate inventory
for crate, entries in sorted(per_crate.items()):
    crate_rel = os.path.relpath(crate, os.getcwd())
    print(f'\n=== {crate_rel} ===')
    by_file = {}
    for file_rel, fn_name in sorted(entries):
        by_file.setdefault(file_rel, set()).add(fn_name)
    for file_rel, fns in sorted(by_file.items()):
        fns_sorted = sorted([f for f in fns if f])
        print(f'  {file_rel}: {fns_sorted}')
