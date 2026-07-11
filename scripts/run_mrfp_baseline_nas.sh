#!/usr/bin/env bash
# Fine-tune the internal and public encoders on the same mRFP expression split.
# Run several seeds independently, e.g. 13, 42, and 73.
#
# Usage:
#   scripts/run_mrfp_baseline_nas.sh
#   scripts/run_mrfp_baseline_nas.sh 42 600000
#   scripts/run_mrfp_baseline_nas.sh 42 600000 5e-5 full learned
#   scripts/run_mrfp_baseline_nas.sh 42 600000 3e-4 frozen learned

set -euo pipefail

if [ $# -gt 5 ]; then
  echo "Usage: $0 [seed] [internal_checkpoint_step] [learning_rate] [full|frozen] [all|learned|internal|public|random]" >&2
  exit 1
fi

SEED="${1:-42}"
STEP="${2:-600000}"
LEARNING_RATE="${3:-1e-4}"
TUNING_MODE="${4:-full}"
MODEL_SCOPE="${5:-all}"
MODEL_REVISION="a1eb7df25804d23f08646e1cb996b234d7208a40"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PUBLIC_MODEL="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_baselines/YYLY66-mRNABERT-$MODEL_REVISION"
INTERNAL_MODEL="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/mrnabert-full-devbox-20260707024008/output/checkpoint-$STEP"
SOURCE_DATA_PATH="$REPO_ROOT/sample_data/fine-tune/mRFP"
CLEAN_DATA_PATH="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/downstream/clean/mRFP"
RESULT_ROOT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/downstream/mRFP"

case "$TUNING_MODE" in
  full) FREEZE_ENCODER=false ;;
  frozen) FREEZE_ENCODER=true ;;
  *) echo "Tuning mode must be full or frozen: $TUNING_MODE" >&2; exit 1 ;;
esac
case "$MODEL_SCOPE" in
  all|learned|internal|public|random) ;;
  *) echo "Unknown model scope: $MODEL_SCOPE" >&2; exit 1 ;;
esac

EXPERIMENT_SUFFIX=""
if [ "$LEARNING_RATE" != "1e-4" ] || [ "$TUNING_MODE" != "full" ]; then
  EXPERIMENT_SUFFIX="-$TUNING_MODE-lr$LEARNING_RATE"
fi

if { [ "$MODEL_SCOPE" = "all" ] || [ "$MODEL_SCOPE" = "learned" ] || [ "$MODEL_SCOPE" = "public" ]; } \
  && [ ! -f "$PUBLIC_MODEL/pytorch_model.bin" ]; then
  "$SCRIPT_DIR/download_baseline_assets_nas.sh"
fi
if { [ "$MODEL_SCOPE" = "all" ] || [ "$MODEL_SCOPE" = "learned" ] \
  || [ "$MODEL_SCOPE" = "internal" ] || [ "$MODEL_SCOPE" = "random" ]; } \
  && [ ! -f "$INTERNAL_MODEL/pytorch_model.bin" ]; then
  echo "Internal checkpoint not found: $INTERNAL_MODEL" >&2
  exit 1
fi

run_model() {
  local NAME="$1"
  local MODEL_PATH="$2"
  local INIT_MODE="${3:-pretrained}"
  local RUN_ID="$NAME$EXPERIMENT_SUFFIX-seed$SEED"
  local OUTPUT_DIR="$RESULT_ROOT/$RUN_ID"

  python regression.py \
    --model_name_or_path "$MODEL_PATH" \
    --init_mode "$INIT_MODE" \
    --freeze_encoder "$FREEZE_ENCODER" \
    --data_path "$CLEAN_DATA_PATH" \
    --output_dir "$OUTPUT_DIR" \
    --run_name "$RUN_ID" \
    --model_max_length 250 \
    --num_train_epochs 20 \
    --per_device_train_batch_size 8 \
    --per_device_eval_batch_size 32 \
    --gradient_accumulation_steps 2 \
    --learning_rate "$LEARNING_RATE" \
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

if [ "$MODEL_SCOPE" = "all" ] || [ "$MODEL_SCOPE" = "learned" ] || [ "$MODEL_SCOPE" = "internal" ]; then
  run_model "internal-checkpoint-$STEP" "$INTERNAL_MODEL"
fi
if [ "$MODEL_SCOPE" = "all" ] || [ "$MODEL_SCOPE" = "learned" ] || [ "$MODEL_SCOPE" = "public" ]; then
  run_model "public-YYLY66-$MODEL_REVISION" "$PUBLIC_MODEL"
fi
if [ "$MODEL_SCOPE" = "all" ] || [ "$MODEL_SCOPE" = "random" ]; then
  run_model "random-init-internal-architecture" "$INTERNAL_MODEL" scratch
fi

find "$RESULT_ROOT" -path "*/results/*/eval_results.json" -print -exec cat {} \;
