#!/usr/bin/env bash
# Compare internal BERT, public mRNABERT, and Evo 2 7B with one frozen ridge probe.
#
# Usage:
#   scripts/run_three_model_frozen_probe_nas.sh
#   scripts/run_three_model_frozen_probe_nas.sh 700000

set -euo pipefail

if [ $# -gt 1 ]; then
  echo "Usage: $0 [internal_checkpoint_step]" >&2
  exit 1
fi

STEP="${1:-600000}"
if ! [[ "$STEP" =~ ^[0-9]+$ ]]; then
  echo "Invalid checkpoint step: $STEP" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MODEL_REVISION="a1eb7df25804d23f08646e1cb996b234d7208a40"
EVO2_REVISION="bda0089f92582d5baabf0f22d9fc85f3588f6b58"
INTERNAL_MODEL="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/mrnabert-full-devbox-20260707024008/output/checkpoint-$STEP"
PUBLIC_MODEL="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_baselines/YYLY66-mRNABERT-$MODEL_REVISION"
EVO2_ROOT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_baselines/evo2"
EVO2_PYTHON="$EVO2_ROOT/venv-evo2-0.6.0/bin/python"
EVO2_MODEL="$EVO2_ROOT/evo2_7b-$EVO2_REVISION/evo2_7b.pt"
EVO2_MODEL_SIZE=13766621200
SOURCE_DATA="$REPO_ROOT/sample_data/fine-tune/mRFP"
CLEAN_DATA="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/downstream/clean/mRFP"
RESULT_ROOT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/downstream/mRFP/three-model-frozen-ridge"
INTERNAL_EMBEDDINGS="$RESULT_ROOT/embeddings/internal-checkpoint-$STEP"
PUBLIC_EMBEDDINGS="$RESULT_ROOT/embeddings/public-YYLY66-$MODEL_REVISION"
EVO2_EMBEDDINGS="$RESULT_ROOT/embeddings/evo2-7b-$EVO2_REVISION-block28"
COMPARISON_OUTPUT="$RESULT_ROOT/results/internal-$STEP-vs-public-vs-evo2-7b"

if [ ! -f "$INTERNAL_MODEL/pytorch_model.bin" ] && [ ! -f "$INTERNAL_MODEL/model.safetensors" ]; then
  echo "Internal checkpoint not found or incomplete: $INTERNAL_MODEL" >&2
  exit 1
fi

if [ ! -f "$PUBLIC_MODEL/pytorch_model.bin" ]; then
  "$SCRIPT_DIR/download_baseline_assets_nas.sh"
fi
if [ ! -x "$EVO2_PYTHON" ] \
  || ! "$EVO2_PYTHON" -c "import evo2, flash_attn" >/dev/null 2>&1 \
  || [ ! -f "$EVO2_MODEL" ] \
  || [ "$(stat -c %s "$EVO2_MODEL" 2>/dev/null || echo 0)" -ne "$EVO2_MODEL_SIZE" ]; then
  "$SCRIPT_DIR/setup_evo2_baseline_nas.sh"
fi

cd "$REPO_ROOT"
python data_process/clean_downstream_splits.py \
  --input-dir "$SOURCE_DATA" \
  --output-dir "$CLEAN_DATA"

export CUDA_VISIBLE_DEVICES=0
python data_process/extract_bert_frozen_embeddings.py \
  --model-name "internal-checkpoint-$STEP" \
  --model-path "$INTERNAL_MODEL" \
  --data-dir "$CLEAN_DATA" \
  --output-dir "$INTERNAL_EMBEDDINGS" \
  --max-length 250 \
  --batch-size 32 \
  --device cuda:0

python data_process/extract_bert_frozen_embeddings.py \
  --model-name "public-YYLY66-$MODEL_REVISION" \
  --model-path "$PUBLIC_MODEL" \
  --data-dir "$CLEAN_DATA" \
  --output-dir "$PUBLIC_EMBEDDINGS" \
  --max-length 250 \
  --batch-size 32 \
  --device cuda:0

"$EVO2_PYTHON" data_process/extract_evo2_frozen_embeddings.py \
  --model-name evo2_7b \
  --model-revision "$EVO2_REVISION" \
  --model-path "$EVO2_MODEL" \
  --layer blocks.28.mlp.l3 \
  --data-dir "$CLEAN_DATA" \
  --output-dir "$EVO2_EMBEDDINGS" \
  --checkpoint-every 25

python data_process/evaluate_frozen_embeddings.py \
  --model "internal-checkpoint-$STEP=$INTERNAL_EMBEDDINGS" \
  --model "public-YYLY66=$PUBLIC_EMBEDDINGS" \
  --model "evo2-7b=$EVO2_EMBEDDINGS" \
  --probe-dim 256 \
  --output-dir "$COMPARISON_OUTPUT"

echo "Three-model comparison finished: $COMPARISON_OUTPUT/results.json"
