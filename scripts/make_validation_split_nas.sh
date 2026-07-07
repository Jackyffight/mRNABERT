#!/usr/bin/env bash
# Build the fixed hash-based validation split on NAS.
#
# Usage:
#   scripts/make_validation_split_nas.sh
#   scripts/make_validation_split_nas.sh --force

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

INPUT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/pre.txt"
VAL_OUT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/eval/valid_100k.txt"
TRAIN_OUT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/eval/train_holdout.txt"
VAL_FRACTION="0.00276"
SEED="20260707"
PROGRESS_INTERVAL="30"

FORCE=false
if [ "${1:-}" = "--force" ]; then
  FORCE=true
elif [ $# -gt 0 ]; then
  echo "Usage: $0 [--force]" >&2
  exit 1
fi

if [ ! -f "$INPUT" ]; then
  echo "Input pretraining file not found: $INPUT" >&2
  exit 1
fi

if [ "$FORCE" = false ] && [ -f "$VAL_OUT" ] && [ -f "$TRAIN_OUT" ]; then
  echo "Validation split already exists:"
  echo "  $VAL_OUT"
  echo "  $TRAIN_OUT"
  echo "Use --force to rebuild."
  exit 0
fi

mkdir -p "$(dirname "$VAL_OUT")"

cd "$REPO_ROOT"
python data_process/make_validation_split.py \
  --input "$INPUT" \
  --val-out "$VAL_OUT" \
  --train-out "$TRAIN_OUT" \
  --val-fraction "$VAL_FRACTION" \
  --seed "$SEED" \
  --progress-interval "$PROGRESS_INTERVAL"

echo "Validation split is ready:"
echo "  validation: $VAL_OUT"
echo "  train_holdout: $TRAIN_OUT"
