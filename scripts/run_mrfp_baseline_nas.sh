#!/usr/bin/env bash
# Fine-tune the internal and public encoders on the same mRFP expression split.
# Run several seeds independently, e.g. 13, 42, and 73.
#
# Usage:
#   scripts/run_mrfp_baseline_nas.sh
#   scripts/run_mrfp_baseline_nas.sh 42 600000

set -euo pipefail

if [ $# -gt 2 ]; then
  echo "Usage: $0 [seed] [internal_checkpoint_step]" >&2
  exit 1
fi

SEED="${1:-42}"
STEP="${2:-600000}"
MODEL_REVISION="a1eb7df25804d23f08646e1cb996b234d7208a40"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PUBLIC_MODEL="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_baselines/YYLY66-mRNABERT-$MODEL_REVISION"
INTERNAL_MODEL="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/mrnabert-full-devbox-20260707024008/output/checkpoint-$STEP"
SOURCE_DATA_PATH="$REPO_ROOT/sample_data/fine-tune/mRFP"
CLEAN_DATA_PATH="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/downstream/clean/mRFP"
RESULT_ROOT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/downstream/mRFP"

if [ ! -f "$PUBLIC_MODEL/pytorch_model.bin" ]; then
  "$SCRIPT_DIR/download_baseline_assets_nas.sh"
fi
if [ ! -f "$INTERNAL_MODEL/pytorch_model.bin" ]; then
  echo "Internal checkpoint not found: $INTERNAL_MODEL" >&2
  exit 1
fi

run_model() {
  local NAME="$1"
  local MODEL_PATH="$2"
  local INIT_MODE="${3:-pretrained}"
  local OUTPUT_DIR="$RESULT_ROOT/$NAME-seed$SEED"

  python regression.py \
    --model_name_or_path "$MODEL_PATH" \
    --init_mode "$INIT_MODE" \
    --data_path "$CLEAN_DATA_PATH" \
    --output_dir "$OUTPUT_DIR" \
    --run_name "$NAME-seed$SEED" \
    --model_max_length 250 \
    --num_train_epochs 20 \
    --per_device_train_batch_size 8 \
    --per_device_eval_batch_size 32 \
    --gradient_accumulation_steps 2 \
    --learning_rate 1e-4 \
    --warmup_steps 50 \
    --logging_steps 10 \
    --eval_steps 20 \
    --save_steps 20 \
    --save_total_limit 3 \
    --evaluation_strategy steps \
    --load_best_model_at_end true \
    --metric_for_best_model spearman_corr \
    --greater_is_better true \
    --bf16 true \
    --tf32 true \
    --seed "$SEED" \
    --data_seed "$SEED" \
    --eval_and_save_results true \
    --save_model false \
    --overwrite_output_dir true \
    --report_to none
}

mkdir -p "$RESULT_ROOT"
cd "$REPO_ROOT"
export CUDA_VISIBLE_DEVICES=0
export NCCL_DEBUG=WARN

python data_process/clean_downstream_splits.py \
  --input-dir "$SOURCE_DATA_PATH" \
  --output-dir "$CLEAN_DATA_PATH"

run_model "internal-checkpoint-$STEP" "$INTERNAL_MODEL"
run_model "public-YYLY66-$MODEL_REVISION" "$PUBLIC_MODEL"
run_model "random-init-internal-architecture" "$INTERNAL_MODEL" scratch

find "$RESULT_ROOT" -path "*/results/*/eval_results.json" -print -exec cat {} \;
