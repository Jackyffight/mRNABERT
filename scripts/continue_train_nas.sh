#!/usr/bin/env bash
# Continue the main mRNABERT NAS-local training run from a NAS checkpoint.
#
# Usage:
#   scripts/continue_train_nas.sh
#   scripts/continue_train_nas.sh 150000
#   scripts/continue_train_nas.sh 200000 150000
#
# Arguments:
#   1. target global step, default 150000
#   2. resume checkpoint step, default 100000

set -euo pipefail

if [ $# -gt 2 ]; then
  echo "Usage: $0 [target_step] [resume_step]" >&2
  exit 1
fi

TARGET_STEP="${1:-150000}"
RESUME_STEP="${2:-100000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

TRAIN_FILE="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/pre.txt"
SHARD_DIR="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/data_shards/pre-3shards-seed42"
OUTPUT_ROOT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs"
RUN_NAME="mrnabert-full-devbox-20260707024008"
RESUME_CHECKPOINT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/mrnabert-full-devbox-20260707024008/output/checkpoint-$RESUME_STEP"

if [ ! -f "$TRAIN_FILE" ]; then
  echo "Training file not found: $TRAIN_FILE" >&2
  exit 1
fi
if [ ! -d "$RESUME_CHECKPOINT" ]; then
  echo "Resume checkpoint not found: $RESUME_CHECKPOINT" >&2
  echo "Run scripts/sync_checkpoints_to_nas.sh first, or pass an existing resume step." >&2
  exit 1
fi

cd "$REPO_ROOT"
./run_train.sh \
  --env devbox \
  --train-file "$TRAIN_FILE" \
  --shard-dir "$SHARD_DIR" \
  --output-root "$OUTPUT_ROOT" \
  --launcher torchrun \
  --devices 0,1,2 \
  --max-steps "$TARGET_STEP" \
  --batch-size 32 \
  --grad-accum 1 \
  --warmup-steps 2000 \
  --logging-steps 50 \
  --save-steps 5000 \
  --save-total-limit 5 \
  --lr 3e-5 \
  --dataloader-workers 0 \
  --run-name "$RUN_NAME" \
  --resume "$RESUME_CHECKPOINT"
