#!/usr/bin/env bash
# Frozen-encoder probe: train only the newly initialized pooler and regression head.

set -euo pipefail

if [ $# -gt 1 ]; then
  echo "Usage: $0 [internal_checkpoint_step]" >&2
  exit 1
fi

STEP="${1:-600000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for LEARNING_RATE in 1e-4 3e-4 1e-3; do
  for SEED in 13 42 73; do
    "$SCRIPT_DIR/run_mrfp_baseline_nas.sh" \
      "$SEED" \
      "$STEP" \
      "$LEARNING_RATE" \
      frozen \
      learned
  done
done

"$SCRIPT_DIR/print_mrfp_results_nas.sh"
