#!/usr/bin/env bash
# Evaluate one NAS-local checkpoint on the fixed NAS validation set using streaming.
#
# Usage:
#   scripts/eval_one_checkpoint_nas.sh 100000
#   scripts/eval_one_checkpoint_nas.sh 100000 2000

set -euo pipefail

if [ $# -gt 2 ]; then
  echo "Usage: $0 [checkpoint_step] [max_eval_samples]" >&2
  exit 1
fi

STEP="${1:-100000}"
MAX_EVAL_SAMPLES="${2:-100000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CHECKPOINT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/mrnabert-full-devbox-20260707024008/output/checkpoint-$STEP"
VALIDATION_FILE="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/eval/valid_100k.txt"
OUTPUT_DIR="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/mrnabert-full-devbox-20260707024008/eval/checkpoint-$STEP-valid${MAX_EVAL_SAMPLES}"
DATASET_CACHE_DIR="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/cache/datasets"

if [ ! -d "$CHECKPOINT" ]; then
  echo "Checkpoint not found: $CHECKPOINT" >&2
  echo "Run scripts/sync_checkpoints_to_nas.sh first." >&2
  exit 1
fi
if [ ! -f "$VALIDATION_FILE" ]; then
  echo "Validation file not found: $VALIDATION_FILE" >&2
  echo "Run scripts/make_validation_split_nas.sh first." >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR" "$DATASET_CACHE_DIR"

cd "$REPO_ROOT"
export CUDA_VISIBLE_DEVICES=0
export NCCL_DEBUG=WARN
python main.py pretrain \
  --do_eval \
  --init_mode pretrained \
  --model_name_or_path "$CHECKPOINT" \
  --validation_file "$VALIDATION_FILE" \
  --output_dir "$OUTPUT_DIR" \
  --dataset_cache_dir "$DATASET_CACHE_DIR" \
  --line_by_line \
  --streaming \
  --streaming_reader line-stride \
  --max_eval_samples "$MAX_EVAL_SAMPLES" \
  --max_seq_length 1024 \
  --per_device_eval_batch_size 32 \
  --dataloader_num_workers 0 \
  --mlm_probability 0.15 \
  --bf16 \
  --tf32 true \
  --prediction_loss_only true \
  --overwrite_output_dir \
  --report_to none

echo "Eval result:"
cat "$OUTPUT_DIR/eval_results.json"
