#!/usr/bin/env bash

# Licensed under the Apache License, Version 2.0 or the MIT License.
# SPDX-License-Identifier: Apache-2.0 OR MIT
# Copyright Tock Contributors 2024.
#
# Counts the number of `flux_support::assume` calls and `#[flux_rs::trusted]`
# annotations in the codebase and prints a summary with a per-reason breakdown.
#
# Must be run from root Tock directory.

set -e

# Verify that we're running in the base directory
if [ ! -x tools/count_flux_annotations.sh ]; then
    echo "ERROR: $0 must be run from the tock repository root."
    echo ""
    exit 1
fi

ASSUME_COUNT=$(grep -r --include="*.rs" "flux_support::assume" . | grep -v "^\./tools/" | grep -v '^\s*//' | wc -l | tr -d ' ')

python3 - "$ASSUME_COUNT" <<'PYEOF'
import re
import sys
from collections import Counter
from pathlib import Path

assume_count = int(sys.argv[1])

root = Path(".")
exclude_dirs = {root / "tools"}

# Match #[flux_rs::trusted(...)] or #[flux_rs::trusted] (single or multiline).
# Uses a non-greedy match on the attribute body to handle both forms.
trusted_attr_re = re.compile(
    r'#\[flux_rs::trusted\b(.*?)\]',
    re.DOTALL,
)
reason_re = re.compile(r'reason\s*=\s*"(.*?)"', re.DOTALL)

reasons: Counter = Counter()
no_reason = 0

for rs_file in sorted(root.rglob("*.rs")):
    if any(rs_file.is_relative_to(d) for d in exclude_dirs):
        continue
    try:
        text = rs_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    for m in trusted_attr_re.finditer(text):
        args = m.group(1).strip()
        r = reason_re.search(args)
        if r:
            reasons[r.group(1)] += 1
        else:
            no_reason += 1

trusted_total = sum(reasons.values()) + no_reason

print("Flux annotation summary")
print("=======================")
print(f"  flux_support::assume calls       : {assume_count}")
print(f"  #[flux_rs::trusted] annotations  : {trusted_total}")
print()
print(f"Total : {assume_count + trusted_total}")
print()
print("#[flux_rs::trusted] breakdown by reason:")
print(f"  {'(no reason)':<70s}  {no_reason:4d}")
for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
    # Truncate very long reasons for display
    display = reason if len(reason) <= 70 else reason[:67] + "..."
    print(f"  {display:<70s}  {count:4d}")
PYEOF
