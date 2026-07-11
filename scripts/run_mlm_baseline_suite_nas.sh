#!/usr/bin/env bash
# Download and compare one internal checkpoint with the pinned public baseline.
#
# Usage:
#   scripts/run_mlm_baseline_suite_nas.sh
#   scripts/run_mlm_baseline_suite_nas.sh 600000 100000 42

set -euo pipefail

if [ $# -gt 3 ]; then
  echo "Usage: $0 [checkpoint_step] [max_eval_samples] [seed]" >&2
  exit 1
fi

STEP="${1:-600000}"
MAX_EVAL_SAMPLES="${2:-100000}"
SEED="${3:-42}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$SCRIPT_DIR/download_baseline_assets_nas.sh"
"$SCRIPT_DIR/eval_one_checkpoint_nas.sh" "$STEP" "$MAX_EVAL_SAMPLES" "$SEED"
"$SCRIPT_DIR/eval_hf_baseline_nas.sh" "$MAX_EVAL_SAMPLES" "$SEED"
"$SCRIPT_DIR/print_eval_results_nas.sh"
