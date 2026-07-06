#!/usr/bin/env bash
# mRNABERT - Neptune/Merlin training launcher
#
# Examples:
#   ./run_train.sh --env devbox --smoke --train-file /mnt/hdfs/byte_neptune_ai/mrna/pre.txt
#   ./run_train.sh --env devbox --train-file /mnt/hdfs/byte_neptune_ai/mrna/pre.txt
#   ./run_train.sh --env devbox --train-file /mnt/hdfs/byte_neptune_ai/mrna/pre.txt --batch-size 48 --grad-accum 2

set -euo pipefail

MODEL_NAME="YYLY66/mRNABERT"
ENV_NAME=""
MODE="full"
TRAIN_FILE="/mnt/hdfs/byte_neptune_ai/mrna/pre.txt"
OUTPUT_ROOT="/mnt/hdfs/byte_neptune_ai/mrna/train/runs"
OUTPUT_DIR=""
RUN_NAME=""
SAMPLE_LINES=20000

BATCH_SIZE=32
GRAD_ACCUM=4
EPOCHS=10
MAX_STEPS=""
LR="5e-5"
MAX_SEQ_LENGTH=1024
WARMUP_STEPS=2000
LOGGING_STEPS=100
SAVE_STEPS=1000
SAVE_TOTAL_LIMIT=3
MLM_PROBABILITY=0.15
DTYPE="bf16"
RESUME=""
INSTALL_DEPS=false

BATCH_SIZE_SET=false
GRAD_ACCUM_SET=false
WARMUP_STEPS_SET=false
LOGGING_STEPS_SET=false
SAVE_STEPS_SET=false
TRAIN_ARGS=()

usage() {
  cat <<'EOF'
Usage:
  ./run_train.sh --env <devbox|online> [launcher args] [run_mlm.py args...]

Launcher args:
  --env <devbox|online>       Required environment label.
  --smoke                     Create a small smoke dataset and run 20 steps by default.
  --model <name-or-path>      Model path or HuggingFace ID. Default: YYLY66/mRNABERT.
  --train-file <path>         Preprocessed pre.txt path. Default: /mnt/hdfs/byte_neptune_ai/mrna/pre.txt.
  --output-root <dir>         Run workspace root. Default: /mnt/hdfs/byte_neptune_ai/mrna/train/runs.
  --output-dir <dir>          Exact output dir for Trainer. Overrides --output-root workspace output.
  --run-name <name>           Run name under --output-root.
  --sample-lines <n>          Smoke sample lines. Default: 20000.
  --batch-size <n>            Per-device train batch size. Default: 32, smoke default: 8.
  --grad-accum <n>            Gradient accumulation. Default: 4, smoke default: 1.
  --epochs <n>                Epoch count. Default: 10.
  --max-steps <n>             Max training steps. Smoke default: 20.
  --lr <float>                Learning rate. Default: 5e-5.
  --max-seq-length <n>        Max token length. Default: 1024.
  --warmup-steps <n>          Warmup steps. Default: 2000, smoke default: 0.
  --logging-steps <n>         Logging interval. Default: 100, smoke default: 1.
  --save-steps <n>            Checkpoint interval. Default: 1000, smoke default: 20.
  --save-total-limit <n>      Max checkpoints to keep. Default: 3.
  --mlm-probability <float>   MLM mask probability. Default: 0.15.
  --dtype <bf16|fp16|fp32>    Training dtype. Default: bf16.
  --resume <checkpoint>       Resume from checkpoint.
  --install-deps              pip install -r requirements.txt before training.

Any unknown arguments are passed through to run_mlm.py.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_NAME="$2"; shift 2 ;;
    --smoke) MODE="smoke"; shift ;;
    --model) MODEL_NAME="$2"; shift 2 ;;
    --train-file|--train_file) TRAIN_FILE="$2"; shift 2 ;;
    --output-root|--output_root) OUTPUT_ROOT="$2"; shift 2 ;;
    --output-dir|--output_dir) OUTPUT_DIR="$2"; shift 2 ;;
    --run-name|--run_name) RUN_NAME="$2"; shift 2 ;;
    --sample-lines|--sample_lines) SAMPLE_LINES="$2"; shift 2 ;;
    --batch-size|--batch_size) BATCH_SIZE="$2"; BATCH_SIZE_SET=true; shift 2 ;;
    --grad-accum|--grad_accum) GRAD_ACCUM="$2"; GRAD_ACCUM_SET=true; shift 2 ;;
    --epochs) EPOCHS="$2"; shift 2 ;;
    --max-steps|--max_steps) MAX_STEPS="$2"; shift 2 ;;
    --lr) LR="$2"; shift 2 ;;
    --max-seq-length|--max_seq_length) MAX_SEQ_LENGTH="$2"; shift 2 ;;
    --warmup-steps|--warmup_steps) WARMUP_STEPS="$2"; WARMUP_STEPS_SET=true; shift 2 ;;
    --logging-steps|--logging_steps) LOGGING_STEPS="$2"; LOGGING_STEPS_SET=true; shift 2 ;;
    --save-steps|--save_steps) SAVE_STEPS="$2"; SAVE_STEPS_SET=true; shift 2 ;;
    --save-total-limit|--save_total_limit) SAVE_TOTAL_LIMIT="$2"; shift 2 ;;
    --mlm-probability|--mlm_probability) MLM_PROBABILITY="$2"; shift 2 ;;
    --dtype) DTYPE="$2"; shift 2 ;;
    --resume) RESUME="$2"; shift 2 ;;
    --install-deps|--install_deps) INSTALL_DEPS=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) TRAIN_ARGS+=("$1"); shift ;;
  esac
done

if [ -z "$ENV_NAME" ]; then
  echo "Error: --env devbox or --env online is required."
  exit 1
fi
if [ "$ENV_NAME" != "devbox" ] && [ "$ENV_NAME" != "online" ]; then
  echo "Error: --env only supports devbox or online."
  exit 1
fi
if [ "$DTYPE" != "bf16" ] && [ "$DTYPE" != "fp16" ] && [ "$DTYPE" != "fp32" ]; then
  echo "Error: --dtype must be bf16, fp16, or fp32."
  exit 1
fi

if [ "$MODE" = "smoke" ]; then
  [ "$BATCH_SIZE_SET" = false ] && BATCH_SIZE=8
  [ "$GRAD_ACCUM_SET" = false ] && GRAD_ACCUM=1
  [ "$WARMUP_STEPS_SET" = false ] && WARMUP_STEPS=0
  [ "$LOGGING_STEPS_SET" = false ] && LOGGING_STEPS=1
  [ "$SAVE_STEPS_SET" = false ] && SAVE_STEPS=20
  [ -z "$MAX_STEPS" ] && MAX_STEPS=20
fi

HDFS_MNT="/mnt/hdfs/byte_neptune_ai"
TIMESTAMP=$(date +%Y%m%d%H%M%S)
if [ -z "$RUN_NAME" ]; then
  RUN_NAME="mrnabert-${MODE}-${ENV_NAME}-${TIMESTAMP}"
fi
WORKSPACE="${OUTPUT_ROOT}/${RUN_NAME}"
WORK_DATA="${WORKSPACE}/data"
WORK_LOGS="${WORKSPACE}/logs"
if [ -z "$OUTPUT_DIR" ]; then
  OUTPUT_DIR="${WORKSPACE}/output"
fi

requires_hdfs=false
for path in "$TRAIN_FILE" "$OUTPUT_ROOT" "$OUTPUT_DIR"; do
  if [[ "$path" == /mnt/hdfs/* ]]; then
    requires_hdfs=true
  fi
done
if [ "$requires_hdfs" = true ]; then
  if [ ! -d "$HDFS_MNT" ] || ! ls "$HDFS_MNT" >/dev/null 2>&1; then
    echo "Error: HDFS mount is not ready: $HDFS_MNT"
    exit 1
  fi
fi

if [ ! -f "$TRAIN_FILE" ]; then
  echo "Error: training file not found: $TRAIN_FILE"
  exit 1
fi

# Clear distributed variables that may be left by previous platform attempts.
unset RANK WORLD_SIZE LOCAL_RANK LOCAL_WORLD_SIZE GROUP_RANK ROLE_RANK
unset MASTER_ADDR

STALE_PIDS=$(pgrep -f "torchrun.*run_mlm.py" 2>/dev/null || true)
if [ -n "$STALE_PIDS" ]; then
  echo "[preflight] Killing stale mRNABERT torchrun processes: $STALE_PIDS"
  kill -9 $STALE_PIDS 2>/dev/null || true
  sleep 2
fi

export TRITON_CACHE_DIR=/tmp/triton_cache_mrnabert_$$
mkdir -p "$TRITON_CACHE_DIR"
export NCCL_DEBUG=${NCCL_DEBUG:-WARN}
export PYTORCH_CUDA_ALLOC_CONF=${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON=$(command -v python3 || command -v python)
fi

if [ "$INSTALL_DEPS" = true ]; then
  "$PYTHON" -m pip install -r requirements.txt
  "$PYTHON" -m pip uninstall -y triton || true
fi

"$PYTHON" - <<'PY'
import importlib.util
missing = [name for name in ("torch", "transformers", "datasets", "accelerate") if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit("Missing Python packages: " + ", ".join(missing) + ". Install dependencies or rerun with --install-deps.")
PY

if [ -x "$(dirname "$PYTHON")/torchrun" ]; then
  TORCHRUN="$(dirname "$PYTHON")/torchrun"
else
  TORCHRUN=$(command -v torchrun)
fi

NUM_GPUS=$("$PYTHON" - <<'PY'
import torch
print(torch.cuda.device_count())
PY
)
if [ "$NUM_GPUS" -lt 1 ]; then
  echo "Error: no CUDA GPU detected."
  exit 1
fi

if [ -z "${MASTER_PORT:-}" ]; then
  MASTER_PORT=$("$PYTHON" - <<'PY'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind(("", 0))
print(s.getsockname()[1])
s.close()
PY
)
fi

mkdir -p "$WORK_DATA" "$WORK_LOGS" "$OUTPUT_DIR"

EFFECTIVE_TRAIN_FILE="$TRAIN_FILE"
if [ "$MODE" = "smoke" ]; then
  EFFECTIVE_TRAIN_FILE="${WORK_DATA}/smoke.txt"
  echo "Creating smoke dataset: $EFFECTIVE_TRAIN_FILE (${SAMPLE_LINES} lines)"
  head -n "$SAMPLE_LINES" "$TRAIN_FILE" > "$EFFECTIVE_TRAIN_FILE"
fi

PRECISION_ARGS=()
case "$DTYPE" in
  bf16) PRECISION_ARGS=(--bf16) ;;
  fp16) PRECISION_ARGS=(--fp16) ;;
  fp32) PRECISION_ARGS=() ;;
esac

RESUME_ARGS=()
if [ -n "$RESUME" ]; then
  RESUME_ARGS=(--resume_from_checkpoint "$RESUME")
fi

MAX_STEP_ARGS=()
if [ -n "$MAX_STEPS" ]; then
  MAX_STEP_ARGS=(--max_steps "$MAX_STEPS")
fi

echo "=== mRNABERT training ==="
echo "env: $ENV_NAME"
echo "mode: $MODE"
echo "workspace: $WORKSPACE"
echo "model: $MODEL_NAME"
echo "train_file: $EFFECTIVE_TRAIN_FILE"
echo "output_dir: $OUTPUT_DIR"
echo "gpus: $NUM_GPUS"
echo "master_port: $MASTER_PORT"
echo "batch_size: $BATCH_SIZE"
echo "grad_accum: $GRAD_ACCUM"
echo "epochs: $EPOCHS"
[ -n "$MAX_STEPS" ] && echo "max_steps: $MAX_STEPS"
echo "dtype: $DTYPE"
echo "python: $($PYTHON --version)"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader
fi
echo "========================="

"$TORCHRUN" \
  --nproc_per_node="$NUM_GPUS" \
  --master_port="$MASTER_PORT" \
  run_mlm.py \
  --output_dir "$OUTPUT_DIR" \
  --model_type bert \
  --model_name_or_path "$MODEL_NAME" \
  --do_train \
  --train_file "$EFFECTIVE_TRAIN_FILE" \
  --line_by_line \
  --max_seq_length "$MAX_SEQ_LENGTH" \
  --per_device_train_batch_size "$BATCH_SIZE" \
  --gradient_accumulation_steps "$GRAD_ACCUM" \
  --num_train_epochs "$EPOCHS" \
  "${MAX_STEP_ARGS[@]}" \
  --learning_rate "$LR" \
  --warmup_steps "$WARMUP_STEPS" \
  --mlm_probability "$MLM_PROBABILITY" \
  "${PRECISION_ARGS[@]}" \
  --save_steps "$SAVE_STEPS" \
  --save_total_limit "$SAVE_TOTAL_LIMIT" \
  --logging_steps "$LOGGING_STEPS" \
  --overwrite_output_dir \
  --report_to none \
  "${RESUME_ARGS[@]}" \
  "${TRAIN_ARGS[@]}"

echo ""
echo "===== training finished ====="
echo "workspace: $WORKSPACE"
echo "output_dir: $OUTPUT_DIR"
