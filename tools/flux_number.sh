#!/usr/bin/env bash
# flux_number.sh — reproducible, LLM-free driver for the panic-obligation number.
#
# Two stages, deliberately separated:
#
#   health   (fast-ish, per-crate flux run; FAILS LOUDLY on any ICE/MASK)
#            This is the CI gate. A new ICE => exit 1 => a human triages it.
#            We do NOT auto-dodge new ICEs: that is exactly the silent-miscount
#            hole that motivated this tool.
#
#   number   (slow; flips every flux_support::assert one-at-a-time) — the real
#            count. Intended as a nightly/periodic job, not per-commit. Only runs
#            against crates the health gate marked CLEAN.
#
# Both stages are pure functions of (source tree + installed flux). The ICE-dodge
# #[flux_rs::trusted] annotations and the def:command exclusion are committed
# source, so this reproduces deterministically with no LLM in the loop. See
# tools/ice_trusted_manifest.md for what was dodged and why.
#
# Usage:
#   tools/flux_number.sh health     # CI gate: all crates must be CLEAN
#   tools/flux_number.sh number     # full negation probe -> the headline number
#   tools/flux_number.sh all        # health then number
set -euo pipefail
cd "$(dirname "$0")/.."
PY=tools/.venv/bin/python3

health() {
    echo ">>> flux health gate (must be all-CLEAN)"
    $PY tools/flux_health.py --out tools/flux_health_logs --json tools/flux_health.json
}

number() {
    echo ">>> negation probe (the real number)"
    $PY tools/negation_probe.py --health tools/flux_health.json \
        --out tools/negation_probe.json --log-dir tools/negation_probe_logs
}

case "${1:-all}" in
    health) health ;;
    number) number ;;
    all)    health && number ;;
    *) echo "usage: $0 {health|number|all}" >&2; exit 2 ;;
esac
