#!/usr/bin/env bash
# Summarize all retained mRFP fine-tuning results across seeds.

set -euo pipefail

RESULT_ROOT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/downstream/mRFP"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ ! -d "$RESULT_ROOT" ]; then
  echo "No mRFP result directory found: $RESULT_ROOT" >&2
  exit 1
fi

cd "$REPO_ROOT"
python data_process/summarize_regression_results.py "$RESULT_ROOT"
