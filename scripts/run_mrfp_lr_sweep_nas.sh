#!/usr/bin/env bash
# Equal-budget full-fine-tuning LR sweep for internal and public mRNABERT encoders.
# Existing 1e-4 results are reused conceptually; this script runs the missing LRs.

set -euo pipefail

if [ $# -gt 1 ]; then
  echo "Usage: $0 [internal_checkpoint_step]" >&2
  exit 1
fi

STEP="${1:-600000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for LEARNING_RATE in 2e-5 5e-5; do
  for SEED in 13 42 73; do
    "$SCRIPT_DIR/run_mrfp_baseline_nas.sh" \
      "$SEED" \
      "$STEP" \
      "$LEARNING_RATE" \
      full \
      learned
  done
done

"$SCRIPT_DIR/print_mrfp_results_nas.sh"
