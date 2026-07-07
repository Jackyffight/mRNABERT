#!/usr/bin/env bash
# Short NAS-local run to measure throughput with dataloader_workers=4.
# It loads checkpoint weights but starts a fresh 1000-step Trainer run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TIMESTAMP="$(date +%Y%m%d%H%M%S)"

TRAIN_FILE="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/eval/train_holdout.txt"
SHARD_DIR="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/eval/data_shards/train_holdout-3shards-seed42"
OUTPUT_ROOT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs"
MODEL_CHECKPOINT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/mrnabert-full-devbox-20260707024008/output/checkpoint-100000"

if [ ! -f "$TRAIN_FILE" ]; then
  echo "Train holdout not found: $TRAIN_FILE" >&2
  echo "Run scripts/make_validation_split_nas.sh first." >&2
  exit 1
fi
if [ ! -d "$MODEL_CHECKPOINT" ]; then
  echo "Model checkpoint not found: $MODEL_CHECKPOINT" >&2
  echo "Run scripts/sync_checkpoints_to_nas.sh first." >&2
  exit 1
fi

cd "$REPO_ROOT"
./run_train.sh \
  --env devbox \
  --model "$MODEL_CHECKPOINT" \
  --init-mode pretrained \
  --train-file "$TRAIN_FILE" \
  --shard-dir "$SHARD_DIR" \
  --output-root "$OUTPUT_ROOT" \
  --launcher torchrun \
  --devices 0,1,2 \
  --max-steps 1000 \
  --batch-size 32 \
  --grad-accum 1 \
  --warmup-steps 0 \
  --logging-steps 50 \
  --save-steps 1000 \
  --save-total-limit 1 \
  --lr 1e-5 \
  --dataloader-workers 4 \
  --run-name "mrnabert-throughput-workers4-$TIMESTAMP"
