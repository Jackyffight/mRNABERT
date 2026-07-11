#!/usr/bin/env bash
# Evaluate the pinned public YYLY66/mRNABERT checkpoint on the same proxy validation set.
#
# Usage:
#   scripts/eval_hf_baseline_nas.sh
#   scripts/eval_hf_baseline_nas.sh 2000
#   scripts/eval_hf_baseline_nas.sh 100000 42

set -euo pipefail

if [ $# -gt 2 ]; then
  echo "Usage: $0 [max_eval_samples] [seed]" >&2
  exit 1
fi

MAX_EVAL_SAMPLES="${1:-100000}"
SEED="${2:-42}"
MODEL_REVISION="a1eb7df25804d23f08646e1cb996b234d7208a40"
MODEL_DIR="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_baselines/YYLY66-mRNABERT-$MODEL_REVISION"
VALIDATION_FILE="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/eval/valid_100k.txt"
OUTPUT_DIR="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/mrnabert-full-devbox-20260707024008/eval/baseline-YYLY66-$MODEL_REVISION-valid${MAX_EVAL_SAMPLES}-seed${SEED}"
DATASET_CACHE_DIR="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/cache/datasets"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ ! -f "$MODEL_DIR/pytorch_model.bin" ]; then
  echo "Baseline model is missing: $MODEL_DIR" >&2
  echo "Run scripts/download_baseline_assets_nas.sh first." >&2
  exit 1
fi
if [ ! -f "$VALIDATION_FILE" ]; then
  echo "Validation file not found: $VALIDATION_FILE" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR" "$DATASET_CACHE_DIR"
cd "$REPO_ROOT"
export CUDA_VISIBLE_DEVICES=0
export NCCL_DEBUG=WARN

python main.py pretrain \
  --do_eval \
  --init_mode pretrained \
  --attention_backend remote-safe \
  --model_name_or_path "$MODEL_DIR" \
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
  --seed "$SEED" \
  --bf16 \
  --tf32 true \
  --prediction_loss_only true \
  --overwrite_output_dir \
  --report_to none

echo "Eval result:"
cat "$OUTPUT_DIR/eval_results.json"
