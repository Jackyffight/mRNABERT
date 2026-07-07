#!/usr/bin/env bash
# Evaluate retained NAS-local checkpoints on the fixed NAS validation set.
#
# Usage:
#   scripts/eval_checkpoints_nas.sh
#   scripts/eval_checkpoints_nas.sh 95000 100000

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ $# -eq 0 ]; then
  STEPS=(80000 85000 90000 95000 100000)
else
  STEPS=("$@")
fi

for STEP in "${STEPS[@]}"; do
  CHECKPOINT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/mrnabert-full-devbox-20260707024008/output/checkpoint-$STEP"
  if [ ! -d "$CHECKPOINT" ]; then
    echo "skip missing checkpoint-$STEP"
    continue
  fi
  "$SCRIPT_DIR/eval_one_checkpoint_nas.sh" "$STEP"
done
