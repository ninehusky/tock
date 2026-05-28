#!/usr/bin/env bash

# Licensed under the Apache License, Version 2.0 or the MIT License.
# SPDX-License-Identifier: Apache-2.0 OR MIT
# Copyright Tock Contributors 2024.
#
# Counts the number of `flux_support::assume` calls and `#[flux_rs::trusted]`
# annotations in the codebase and prints a summary.
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
TRUSTED_COUNT=$(grep -r --include="*.rs" "flux_rs::trusted" . | grep -v "^\./tools/" | grep -v '^\s*//' | wc -l | tr -d ' ')

echo "Flux annotation summary"
echo "======================="
echo "  flux_support::assume calls : ${ASSUME_COUNT}"
echo "  #[flux_rs::trusted] annotations : ${TRUSTED_COUNT}"
echo ""
echo "Total : $((ASSUME_COUNT + TRUSTED_COUNT))"
