#!/usr/bin/env bash
# Evaluate retained NAS-local checkpoints on the fixed NAS validation set.
#
# Usage:
#   scripts/eval_checkpoints_nas.sh
#   scripts/eval_checkpoints_nas.sh 680000 690000 700000

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_OUTPUT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/mrnabert-full-devbox-20260707024008/output"

if [ $# -eq 0 ]; then
  shopt -s nullglob
  CHECKPOINTS=("$RUN_OUTPUT"/checkpoint-*)
  if [ "${#CHECKPOINTS[@]}" -eq 0 ]; then
    echo "No checkpoints found under: $RUN_OUTPUT" >&2
    exit 1
  fi

  mapfile -t CHECKPOINTS < <(printf '%s\n' "${CHECKPOINTS[@]}" | sort -V)
  STEPS=()
  for CHECKPOINT in "${CHECKPOINTS[@]}"; do
    STEP="${CHECKPOINT##*-}"
    if [[ "$STEP" =~ ^[0-9]+$ ]]; then
      STEPS+=("$STEP")
    fi
  done
  if [ "${#STEPS[@]}" -eq 0 ]; then
    echo "No numeric checkpoint directories found under: $RUN_OUTPUT" >&2
    exit 1
  fi
else
  STEPS=("$@")
fi

echo "Evaluating checkpoint steps: ${STEPS[*]}"
for STEP in "${STEPS[@]}"; do
  if ! [[ "$STEP" =~ ^[0-9]+$ ]]; then
    echo "Invalid checkpoint step: $STEP" >&2
    exit 1
  fi
  CHECKPOINT="$RUN_OUTPUT/checkpoint-$STEP"
  if [ ! -d "$CHECKPOINT" ]; then
    echo "skip missing checkpoint-$STEP"
    continue
  fi
  "$SCRIPT_DIR/eval_one_checkpoint_nas.sh" "$STEP"
done
