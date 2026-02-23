import re
import sys
from collections import Counter

if len(sys.argv) != 2:
    print("Usage: python triage_logs.py path/to/log.txt")
    sys.exit(1)

log_path = sys.argv[1]

function_counter = Counter()
reason_counter = Counter()
function_reason_counter = Counter()

# Capture:
# call to <FUNCTION> may panic: MightPanic(<REASON>)
pattern = re.compile(r"call to (.+?) may panic: MightPanic\((.+)\)")

total_lines = 0
total_matches = 0

with open(log_path, errors="replace") as f:
    for line in f:
        total_lines += 1
        m = pattern.search(line)
        if not m:
            continue

        total_matches += 1
        function = m.group(1).strip()
        reason = m.group(2).strip()

        function_counter[function] += 1
        reason_counter[reason] += 1
        function_reason_counter[(function, reason)] += 1

print(f"Read {total_lines} lines")
print(f"Matched {total_matches} 'may panic' lines")

print("\n=== Sample reasons (first 10 distinct) ===")
for i, r in enumerate(reason_counter.keys()):
    if i >= 10:
        break
    print(r)

print("\n=== Panic Reasons ===")
for reason, count in reason_counter.most_common():
    print(f"{count:5d}  {reason}")

print("\n=== Top Functions ===")
for function, count in function_counter.most_common(15):
    print(f"{count:5d}  {function}")

print("\n=== Function + Reason ===")
for (function, reason), count in function_reason_counter.most_common():
    print(f"{count:5d}  {function} | {reason}")
