#!/usr/bin/env bash
# mRNABERT - Neptune/Merlin training launcher
#
# Examples:
#   ./run_train.sh --env devbox --smoke --train-file /mnt/hdfs/byte_neptune_ai/mrna/pre.txt
#   ./run_train.sh --env devbox --train-file /mnt/hdfs/byte_neptune_ai/mrna/pre.txt
#   ./run_train.sh --env devbox --train-file /mnt/hdfs/byte_neptune_ai/mrna/pre.txt --batch-size 48 --grad-accum 2

set -euo pipefail

MODEL_NAME="assets/mrnabert-base"
INIT_MODE="scratch"
ENV_NAME=""
MODE="full"
TRAIN_FILE="/mnt/hdfs/byte_neptune_ai/mrna/pre.txt"
OUTPUT_ROOT="/mnt/hdfs/byte_neptune_ai/mrna/train/runs"
OUTPUT_DIR=""
RUN_NAME=""
SAMPLE_LINES=20000
DATASET_CACHE_DIR=""
HF_CACHE_DIR="${MRNABERT_HF_CACHE_DIR:-}"

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
PREPROCESSING_NUM_WORKERS=8
DATALOADER_NUM_WORKERS=4
TF32=true
USE_TRITON_FLASH_ATTN=false
RESUME=""
INSTALL_DEPS=false
PYTHON_BIN="${MRNABERT_PYTHON:-python}"
CUDA_DEVICES="${MRNABERT_CUDA_VISIBLE_DEVICES:-}"
CUDA_DEVICES_SET=false
LAUNCHER="${MRNABERT_LAUNCHER:-direct}"
NPROC_PER_NODE="${MRNABERT_NPROC_PER_NODE:-}"
MASTER_PORT="${MRNABERT_MASTER_PORT:-}"
STREAMING_MODE="auto"
STREAMING_READER="${MRNABERT_STREAMING_READER:-line-stride}"
STREAMING_SHUFFLE_BUFFER="${MRNABERT_STREAMING_SHUFFLE_BUFFER:-0}"
STREAMING_SHUFFLE_SEED="${MRNABERT_STREAMING_SHUFFLE_SEED:-42}"
STREAMING_RESUME_SKIP_SAMPLES="${MRNABERT_STREAMING_RESUME_SKIP_SAMPLES:-}"
STREAMING_RESUME_MODE="exact-replay"
AUTO_SHARD_MODE="${MRNABERT_AUTO_SHARD:-auto}"
SHARD_DIR="${MRNABERT_SHARD_DIR:-}"
SHARD_COUNT="${MRNABERT_SHARD_COUNT:-}"
SHARD_SEED="${MRNABERT_SHARD_SEED:-42}"
SHARD_PROGRESS_INTERVAL="${MRNABERT_SHARD_PROGRESS_INTERVAL:-30}"
RESHARD=false

BATCH_SIZE_SET=false
GRAD_ACCUM_SET=false
WARMUP_STEPS_SET=false
LOGGING_STEPS_SET=false
SAVE_STEPS_SET=false
DATALOADER_NUM_WORKERS_SET=false
STREAMING_READER_SET=false
STREAMING_SHUFFLE_BUFFER_SET=false
STREAMING_RESUME_SKIP_SAMPLES_SET=false
TRAIN_ARGS=()

usage() {
  cat <<'EOF'
Usage:
  ./run_train.sh --env <devbox|online> [launcher args] [pretrain args...]

Launcher args:
  --env <devbox|online>       Required environment label.
  --smoke                     Create a small smoke dataset and run 20 steps by default.
  --model <name-or-path>      Config/tokenizer path or HF ID. Default: assets/mrnabert-base.
  --init-mode <mode>          scratch or pretrained. Default: scratch.
  --train-file <path>         Preprocessed pre.txt path. Default: /mnt/hdfs/byte_neptune_ai/mrna/pre.txt.
  --output-root <dir>         Run workspace root. Default: /mnt/hdfs/byte_neptune_ai/mrna/train/runs.
  --output-dir <dir>          Exact output dir for Trainer. Overrides --output-root workspace output.
  --run-name <name>           Run name under --output-root.
  --dataset-cache-dir <dir>   HuggingFace datasets cache. Default: <output-root>/cache/datasets.
  --hf-cache-dir <dir>        HuggingFace hub cache for explicit pretrained runs.
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
  --preprocessing-workers <n> Dataset tokenization workers. Default: 8.
  --dataloader-workers <n>    PyTorch DataLoader workers. Default: 4.
  --no-tf32                   Disable TF32 matmul on Ampere+ GPUs.
  --use-triton-flash-attn     Use YYLY66/mRNABERT remote Triton flash-attention
                              for explicit pretrained baseline runs.
  --resume <checkpoint>       Resume from checkpoint.
  --install-deps              pip install -r requirements.txt before training.
  --python <path>             Python binary. Default: $MRNABERT_PYTHON or python.
  --launcher <direct|torchrun> Launch mode. Default: direct.
  --nproc-per-node <n>        Processes for torchrun. Default: visible GPU count.
  --master-port <n>           Master port for torchrun. Default: auto.
  --devices <list|all>        CUDA_VISIBLE_DEVICES. Default: first currently visible GPU.
                              For torchrun, default is all visible GPUs.
  --streaming                 Stream data without Arrow/tokenized cache. Default when --max-steps is set.
  --streaming-reader <reader> line-stride, file-shard, byte-range, or hf. Default: line-stride.
  --streaming-shuffle-buffer <n>
                              Per-rank local streaming line shuffle buffer. Default: 20000 for file-shard.
  --streaming-shuffle-seed <n>
                              Streaming shuffle seed. Default: 42.
  --streaming-resume-skip-samples <n>
                              Override global raw examples skipped on streaming resume.
                              Default: checkpoint global_step * effective batch.
  --streaming-resume-mode <mode>
                              exact-replay or fast-seek. Default: exact-replay.
  --no-streaming              Force Arrow/tokenized cache creation.
  --auto-shard                Split one train file into random shard files before torchrun training.
                              Default: auto for torchrun + streaming + one txt file.
  --no-auto-shard             Disable launcher-side data sharding.
  --shard-dir <dir>           Shard cache dir. Default: <output-root>/data_shards/<file>-<n>shards-seed<seed>.
  --shard-count <n>           Number of random shards. Default: torchrun process count.
  --shard-seed <n>            Deterministic random shard assignment seed. Default: 42.
  --shard-progress-interval <sec>
                              Progress log interval for sharding. Default: 30.
  --reshard                   Rebuild shard files even if manifest matches.

Any unknown arguments are passed through to `python main.py pretrain`.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_NAME="$2"; shift 2 ;;
    --smoke) MODE="smoke"; shift ;;
    --model) MODEL_NAME="$2"; shift 2 ;;
    --init-mode|--init_mode) INIT_MODE="$2"; shift 2 ;;
    --train-file|--train_file) TRAIN_FILE="$2"; shift 2 ;;
    --output-root|--output_root) OUTPUT_ROOT="$2"; shift 2 ;;
    --output-dir|--output_dir) OUTPUT_DIR="$2"; shift 2 ;;
    --run-name|--run_name) RUN_NAME="$2"; shift 2 ;;
    --dataset-cache-dir|--dataset_cache_dir) DATASET_CACHE_DIR="$2"; shift 2 ;;
    --hf-cache-dir|--hf_cache_dir) HF_CACHE_DIR="$2"; shift 2 ;;
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
    --preprocessing-workers|--preprocessing_workers) PREPROCESSING_NUM_WORKERS="$2"; shift 2 ;;
    --dataloader-workers|--dataloader_workers) DATALOADER_NUM_WORKERS="$2"; DATALOADER_NUM_WORKERS_SET=true; shift 2 ;;
    --no-tf32|--no_tf32) TF32=false; shift ;;
    --use-triton-flash-attn|--use_triton_flash_attn) USE_TRITON_FLASH_ATTN=true; shift ;;
    --resume) RESUME="$2"; shift 2 ;;
    --install-deps|--install_deps) INSTALL_DEPS=true; shift ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --launcher) LAUNCHER="$2"; shift 2 ;;
    --nproc-per-node|--nproc_per_node) NPROC_PER_NODE="$2"; shift 2 ;;
    --master-port|--master_port) MASTER_PORT="$2"; shift 2 ;;
    --devices|--cuda-visible-devices|--cuda_visible_devices) CUDA_DEVICES="$2"; CUDA_DEVICES_SET=true; shift 2 ;;
    --streaming) STREAMING_MODE=true; shift ;;
    --streaming-reader|--streaming_reader) STREAMING_READER="$2"; STREAMING_READER_SET=true; shift 2 ;;
    --streaming-shuffle-buffer|--streaming_shuffle_buffer) STREAMING_SHUFFLE_BUFFER="$2"; STREAMING_SHUFFLE_BUFFER_SET=true; shift 2 ;;
    --streaming-shuffle-seed|--streaming_shuffle_seed) STREAMING_SHUFFLE_SEED="$2"; shift 2 ;;
    --streaming-resume-skip-samples|--streaming_resume_skip_samples) STREAMING_RESUME_SKIP_SAMPLES="$2"; STREAMING_RESUME_SKIP_SAMPLES_SET=true; shift 2 ;;
    --streaming-resume-mode|--streaming_resume_mode) STREAMING_RESUME_MODE="$2"; shift 2 ;;
    --no-streaming|--no_streaming) STREAMING_MODE=false; shift ;;
    --auto-shard|--auto_shard) AUTO_SHARD_MODE=true; shift ;;
    --no-auto-shard|--no_auto_shard) AUTO_SHARD_MODE=false; shift ;;
    --shard-dir|--shard_dir) SHARD_DIR="$2"; shift 2 ;;
    --shard-count|--shard_count) SHARD_COUNT="$2"; shift 2 ;;
    --shard-seed|--shard_seed) SHARD_SEED="$2"; shift 2 ;;
    --shard-progress-interval|--shard_progress_interval) SHARD_PROGRESS_INTERVAL="$2"; shift 2 ;;
    --reshard) RESHARD=true; shift ;;
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
if [ "$INIT_MODE" != "scratch" ] && [ "$INIT_MODE" != "pretrained" ]; then
  echo "Error: --init-mode must be scratch or pretrained."
  exit 1
fi
if [ "$LAUNCHER" != "direct" ] && [ "$LAUNCHER" != "torchrun" ]; then
  echo "Error: --launcher must be direct or torchrun."
  exit 1
fi
if [ "$STREAMING_READER" != "line-stride" ] && [ "$STREAMING_READER" != "file-shard" ] && [ "$STREAMING_READER" != "byte-range" ] && [ "$STREAMING_READER" != "hf" ]; then
  echo "Error: --streaming-reader must be line-stride, file-shard, byte-range, or hf."
  exit 1
fi
if [ "$STREAMING_RESUME_MODE" != "exact-replay" ] && [ "$STREAMING_RESUME_MODE" != "fast-seek" ]; then
  echo "Error: --streaming-resume-mode must be exact-replay or fast-seek."
  exit 1
fi
if [ "$AUTO_SHARD_MODE" != "auto" ] && [ "$AUTO_SHARD_MODE" != "true" ] && [ "$AUTO_SHARD_MODE" != "false" ]; then
  echo "Error: auto shard mode must be auto, true, or false."
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

if [ "$STREAMING_MODE" = "auto" ]; then
  if [ -n "$MAX_STEPS" ]; then
    STREAMING_MODE=true
  else
    STREAMING_MODE=false
  fi
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
if [ -z "$DATASET_CACHE_DIR" ]; then
  DATASET_CACHE_DIR="${OUTPUT_ROOT}/cache/datasets"
fi
if [ "$INIT_MODE" = "pretrained" ] && [ -z "$HF_CACHE_DIR" ]; then
  HF_CACHE_DIR="${OUTPUT_ROOT}/cache/huggingface"
fi

requires_hdfs=false
for path in "$TRAIN_FILE" "$OUTPUT_ROOT" "$OUTPUT_DIR" "$DATASET_CACHE_DIR"; do
  if [[ "$path" == /mnt/hdfs/* ]]; then
    requires_hdfs=true
  fi
done
if [ -n "$HF_CACHE_DIR" ] && [[ "$HF_CACHE_DIR" == /mnt/hdfs/* ]]; then
  requires_hdfs=true
fi
if [ "$requires_hdfs" = true ]; then
  if [ ! -d "$HDFS_MNT" ] || ! ls "$HDFS_MNT" >/dev/null 2>&1; then
    echo "Error: HDFS mount is not ready: $HDFS_MNT"
    exit 1
  fi
fi

if [[ "$DATASET_CACHE_DIR" == "$HOME"/* || "$DATASET_CACHE_DIR" == /home/* || "$DATASET_CACHE_DIR" == /root/* ]]; then
  if [ "${MRNABERT_ALLOW_HOME_CACHE:-false}" != "true" ]; then
    echo "Error: dataset cache points to a home/root filesystem: $DATASET_CACHE_DIR"
    echo "Use --dataset-cache-dir on an HDFS-mounted training path, or set MRNABERT_ALLOW_HOME_CACHE=true to override."
    exit 1
  fi
fi
if [ -n "$HF_CACHE_DIR" ] && [[ "$HF_CACHE_DIR" == "$HOME"/* || "$HF_CACHE_DIR" == /home/* || "$HF_CACHE_DIR" == /root/* ]]; then
  if [ "${MRNABERT_ALLOW_HOME_CACHE:-false}" != "true" ]; then
    echo "Error: HuggingFace cache points to a home/root filesystem: $HF_CACHE_DIR"
    echo "Use --hf-cache-dir on an HDFS-mounted training path, or set MRNABERT_ALLOW_HOME_CACHE=true to override."
    exit 1
  fi
fi

if [ ! -f "$TRAIN_FILE" ] && ! compgen -G "$TRAIN_FILE" >/dev/null; then
  echo "Error: training file not found: $TRAIN_FILE"
  exit 1
fi

# Clear distributed variables that may be left by previous platform attempts.
unset RANK WORLD_SIZE LOCAL_RANK LOCAL_WORLD_SIZE GROUP_RANK ROLE_RANK
unset MASTER_ADDR

STALE_PIDS=$(
  {
    pgrep -f "torchrun.*main.py pretrain" || true
    pgrep -f "torchrun.*run_mlm.py" || true
  } 2>/dev/null | sort -u
)
if [ -n "$STALE_PIDS" ]; then
  echo "[preflight] Killing stale mRNABERT torchrun processes: $STALE_PIDS"
  kill -9 $STALE_PIDS 2>/dev/null || true
  sleep 2
fi

export TRITON_CACHE_DIR=/tmp/triton_cache_mrnabert_$$
mkdir -p "$TRITON_CACHE_DIR"
export NCCL_DEBUG=${MRNABERT_NCCL_DEBUG:-WARN}
export PYTORCH_CUDA_ALLOC_CONF=${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}
export TRANSFORMERS_NO_ADVISORY_WARNINGS=${TRANSFORMERS_NO_ADVISORY_WARNINGS:-1}
if [ -n "$RESUME" ]; then
  export TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=${TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD:-1}
fi
export HF_DATASETS_CACHE="$DATASET_CACHE_DIR"
if [ -n "$HF_CACHE_DIR" ]; then
  export HF_HOME="$HF_CACHE_DIR"
  export HUGGINGFACE_HUB_CACHE="${HF_CACHE_DIR}/hub"
  export HF_MODULES_CACHE="${HF_CACHE_DIR}/modules"
  export TRANSFORMERS_CACHE="${HF_CACHE_DIR}/hub"
fi

# Direct `python` launch defaults to one GPU to avoid implicit DataParallel.
# torchrun defaults to all GPUs made visible by the environment.
ORIGINAL_CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-}"
if [ -z "$CUDA_DEVICES" ] && [ "$CUDA_DEVICES_SET" = false ]; then
  if [ -n "$ORIGINAL_CUDA_VISIBLE_DEVICES" ]; then
    if [ "$LAUNCHER" = "torchrun" ]; then
      CUDA_DEVICES="$ORIGINAL_CUDA_VISIBLE_DEVICES"
    else
      CUDA_DEVICES="${ORIGINAL_CUDA_VISIBLE_DEVICES%%,*}"
    fi
  else
    if [ "$LAUNCHER" = "torchrun" ]; then
      CUDA_DEVICES="all"
    else
      CUDA_DEVICES="0"
    fi
  fi
fi
if [ "$CUDA_DEVICES" != "all" ]; then
  export CUDA_VISIBLE_DEVICES="$CUDA_DEVICES"
fi

PYTHON="$PYTHON_BIN"
if [ ! -x "$PYTHON" ]; then
  if command -v "$PYTHON" >/dev/null 2>&1; then
    PYTHON=$(command -v "$PYTHON")
  else
    echo "Error: python is not executable or on PATH: $PYTHON"
    exit 1
  fi
fi

if [ "$INSTALL_DEPS" = true ]; then
  "$PYTHON" -m pip install -r requirements.txt
fi

"$PYTHON" - <<'PY'
import importlib.util
missing = [name for name in ("torch", "transformers", "datasets", "accelerate") if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit("Missing Python packages: " + ", ".join(missing) + ". Install dependencies or rerun with --install-deps.")
PY

"$PYTHON" - <<'PY'
import accelerate
import datasets
import torch
import transformers
from transformers import Trainer  # noqa: F401

print(
    "Dependency check: "
    f"torch={torch.__version__} cuda={torch.version.cuda} "
    f"transformers={transformers.__version__} "
    f"datasets={datasets.__version__} "
    f"accelerate={accelerate.__version__}",
    flush=True,
)
PY

NUM_GPUS=$("$PYTHON" - <<'PY'
import torch
print(torch.cuda.device_count())
PY
)
if [ "$NUM_GPUS" -lt 1 ]; then
  echo "Error: no CUDA GPU detected."
  exit 1
fi
if [ "$LAUNCHER" = "torchrun" ]; then
  if [ -z "$NPROC_PER_NODE" ]; then
    NPROC_PER_NODE="$NUM_GPUS"
  fi
  if [ "$NPROC_PER_NODE" -lt 1 ]; then
    echo "Error: --nproc-per-node must be >= 1."
    exit 1
  fi
  if [ "$NPROC_PER_NODE" -gt "$NUM_GPUS" ]; then
    echo "Error: --nproc-per-node ($NPROC_PER_NODE) is greater than visible GPU count ($NUM_GPUS)."
    exit 1
  fi
  if [ -z "$MASTER_PORT" ]; then
    MASTER_PORT=$("$PYTHON" - <<'PY'
import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("", 0))
    print(sock.getsockname()[1])
PY
)
  fi
fi

TRAIN_FILE_HAS_GLOB=false
if [[ "$TRAIN_FILE" == *"*"* || "$TRAIN_FILE" == *"?"* || "$TRAIN_FILE" == *"["* ]]; then
  TRAIN_FILE_HAS_GLOB=true
fi

AUTO_SHARD_ENABLED=false
if [ "$AUTO_SHARD_MODE" = "true" ]; then
  AUTO_SHARD_ENABLED=true
elif [ "$AUTO_SHARD_MODE" = "auto" ]; then
  if [ "$MODE" != "smoke" ] \
    && [ "$LAUNCHER" = "torchrun" ] \
    && [ "$STREAMING_MODE" = "true" ] \
    && [ "$TRAIN_FILE_HAS_GLOB" = false ] \
    && [ -f "$TRAIN_FILE" ] \
    && [[ "$TRAIN_FILE" == *.txt ]] \
    && [ "${NPROC_PER_NODE:-1}" -gt 1 ]; then
    AUTO_SHARD_ENABLED=true
  fi
fi

if [ "$AUTO_SHARD_ENABLED" = true ]; then
  if [ "$STREAMING_MODE" != "true" ]; then
    echo "Error: --auto-shard requires streaming mode. Remove --no-streaming or add --streaming."
    exit 1
  fi
  if [ "$TRAIN_FILE_HAS_GLOB" = true ] || [ ! -f "$TRAIN_FILE" ]; then
    echo "Error: --auto-shard requires one concrete training txt file, got: $TRAIN_FILE"
    exit 1
  fi
  if [[ "$TRAIN_FILE" != *.txt ]]; then
    echo "Error: --auto-shard currently supports .txt pretraining files only: $TRAIN_FILE"
    exit 1
  fi
  if [ -z "$SHARD_COUNT" ]; then
    if [ "$LAUNCHER" = "torchrun" ]; then
      SHARD_COUNT="$NPROC_PER_NODE"
    else
      SHARD_COUNT="$NUM_GPUS"
    fi
  fi
  if [ "$SHARD_COUNT" -lt 1 ]; then
    echo "Error: --shard-count must be >= 1."
    exit 1
  fi
  if [ -z "$SHARD_DIR" ]; then
    SHARD_BASE=$(basename "$TRAIN_FILE")
    SHARD_STEM="${SHARD_BASE%.*}"
    SHARD_STEM=$(printf '%s' "$SHARD_STEM" | tr -c 'A-Za-z0-9._-' '_')
    SHARD_DIR="${OUTPUT_ROOT}/data_shards/${SHARD_STEM}-${SHARD_COUNT}shards-seed${SHARD_SEED}"
  fi
  if [ "$STREAMING_READER_SET" = false ]; then
    STREAMING_READER="file-shard"
  fi
  # Keep dataloader workers > 0 here. The file-shard reader is worker-aware: within a
  # rank's shard it further splits lines by (line_index % num_workers) with a
  # per-worker shuffle seed (covered by tests/test_streaming.py), so >0 workers do
  # not duplicate or drop data. Forcing 0 serializes tokenization on the training
  # process and starves the GPU (~8% MFU in the first 100k-step run); the default
  # (4) overlaps tokenization with compute. Raise via --dataloader-workers if CPU allows.
fi

if [ "$STREAMING_MODE" = "true" ] && [ "$STREAMING_READER" = "file-shard" ] && [ "$STREAMING_SHUFFLE_BUFFER_SET" = false ]; then
  STREAMING_SHUFFLE_BUFFER=20000
fi
if [ "$STREAMING_RESUME_MODE" = "fast-seek" ] && [ "$STREAMING_READER" != "file-shard" ]; then
  echo "Error: --streaming-resume-mode fast-seek requires --streaming-reader file-shard."
  exit 1
fi

mkdir -p "$WORK_DATA" "$WORK_LOGS" "$OUTPUT_DIR" "$DATASET_CACHE_DIR"
if [ -n "$HF_CACHE_DIR" ]; then
  mkdir -p "$HUGGINGFACE_HUB_CACHE" "$HF_MODULES_CACHE"
fi

EFFECTIVE_TRAIN_FILE="$TRAIN_FILE"
if [ "$MODE" = "smoke" ]; then
  EFFECTIVE_TRAIN_FILE="${WORK_DATA}/smoke.txt"
  echo "Creating smoke dataset: $EFFECTIVE_TRAIN_FILE (${SAMPLE_LINES} lines)"
  SMOKE_SOURCE="$TRAIN_FILE"
  if [ ! -f "$SMOKE_SOURCE" ]; then
    SMOKE_SOURCE=$(compgen -G "$TRAIN_FILE" | sort | head -n 1)
  fi
  head -n "$SAMPLE_LINES" "$SMOKE_SOURCE" > "$EFFECTIVE_TRAIN_FILE"
fi

if [ "$AUTO_SHARD_ENABLED" = true ]; then
  SHARD_ARGS=(
    --input "$TRAIN_FILE"
    --output-dir "$SHARD_DIR"
    --shards "$SHARD_COUNT"
    --seed "$SHARD_SEED"
    --progress-interval "$SHARD_PROGRESS_INTERVAL"
  )
  if [ "$RESHARD" = true ]; then
    SHARD_ARGS+=(--overwrite)
  fi
  "$PYTHON" data_process/shard_pretrain_text.py "${SHARD_ARGS[@]}"
  EFFECTIVE_TRAIN_FILE="${SHARD_DIR}/pre_shard_*.txt"
fi

PRECISION_ARGS=()
case "$DTYPE" in
  bf16) PRECISION_ARGS=(--bf16) ;;
  fp16) PRECISION_ARGS=(--fp16) ;;
  fp32) PRECISION_ARGS=() ;;
esac

RESUME_ARGS=()
STREAMING_RESUME_GLOBAL_STEP=""
STREAMING_RESUME_WORLD_SIZE=""
STREAMING_RESUME_CURSOR_SOURCE=""
STREAMING_SHARD_MANIFEST=""
if [ "$STREAMING_MODE" = "true" ] && [ -n "$SHARD_DIR" ] && [ -f "$SHARD_DIR/manifest.json" ]; then
  STREAMING_SHARD_MANIFEST="$SHARD_DIR/manifest.json"
fi
if [ -n "$RESUME" ]; then
  RESUME_ARGS=(--resume_from_checkpoint "$RESUME")
  if [ "$STREAMING_MODE" = "true" ]; then
    if [ "$LAUNCHER" = "torchrun" ]; then
      STREAMING_RESUME_WORLD_SIZE="$NPROC_PER_NODE"
    else
      STREAMING_RESUME_WORLD_SIZE=1
    fi
    STREAMING_RESUME_EFFECTIVE_BATCH=$((BATCH_SIZE * GRAD_ACCUM * STREAMING_RESUME_WORLD_SIZE))
    STREAMING_RESOLVE_ARGS=(
      --checkpoint "$RESUME"
      --effective-batch "$STREAMING_RESUME_EFFECTIVE_BATCH"
      --streaming-reader "$STREAMING_READER"
      --shuffle-buffer "$STREAMING_SHUFFLE_BUFFER"
      --shuffle-seed "$STREAMING_SHUFFLE_SEED"
      --world-size "$STREAMING_RESUME_WORLD_SIZE"
      --dataloader-num-workers "$DATALOADER_NUM_WORKERS"
    )
    if [ "$STREAMING_RESUME_SKIP_SAMPLES_SET" = true ]; then
      STREAMING_RESOLVE_ARGS+=(--override-sample-cursor "$STREAMING_RESUME_SKIP_SAMPLES")
    fi
    if [ -n "$STREAMING_SHARD_MANIFEST" ]; then
      STREAMING_RESOLVE_ARGS+=(--shard-manifest "$STREAMING_SHARD_MANIFEST")
    fi
    STREAMING_RESOLVED=$("$PYTHON" -m mrnabert.streaming_state resolve "${STREAMING_RESOLVE_ARGS[@]}")
    IFS=$'\t' read -r STREAMING_RESUME_GLOBAL_STEP STREAMING_RESUME_SKIP_SAMPLES STREAMING_RESUME_CURSOR_SOURCE <<< "$STREAMING_RESOLVED"
    RESUME_ARGS+=(
      --ignore_data_skip true
      --streaming_resume_skip_samples "$STREAMING_RESUME_SKIP_SAMPLES"
      --streaming_resume_global_step "$STREAMING_RESUME_GLOBAL_STEP"
      --streaming_resume_cursor_source "$STREAMING_RESUME_CURSOR_SOURCE"
    )
  fi
fi

MAX_STEP_ARGS=()
if [ -n "$MAX_STEPS" ]; then
  MAX_STEP_ARGS=(--max_steps "$MAX_STEPS")
fi

FLASH_ATTN_ARGS=(--attention_backend remote-safe)
if [ "$USE_TRITON_FLASH_ATTN" = true ]; then
  FLASH_ATTN_ARGS=(--attention_backend remote-triton)
fi
if [ "$INIT_MODE" = "scratch" ]; then
  FLASH_ATTN_ARGS=(--attention_backend pytorch)
fi

CACHE_ARGS=()
if [ -n "$HF_CACHE_DIR" ]; then
  CACHE_ARGS=(--cache_dir "$HF_CACHE_DIR")
fi

DDP_ARGS=()
if [ "$LAUNCHER" = "torchrun" ]; then
  DDP_ARGS=(--ddp_backend nccl --ddp_find_unused_parameters false)
  if [ "$STREAMING_MODE" = "true" ]; then
    DDP_ARGS+=(--dispatch_batches false)
  fi
fi

STREAMING_ARGS=()
if [ "$STREAMING_MODE" = "true" ]; then
  STREAMING_ARGS=(--streaming)
  if [ -n "$STREAMING_SHARD_MANIFEST" ]; then
    STREAMING_ARGS+=(--streaming_shard_manifest "$STREAMING_SHARD_MANIFEST")
  fi
fi

echo "=== mRNABERT training ==="
echo "env: $ENV_NAME"
echo "mode: $MODE"
echo "workspace: $WORKSPACE"
echo "model: $MODEL_NAME"
echo "init_mode: $INIT_MODE"
echo "train_file: $EFFECTIVE_TRAIN_FILE"
echo "output_dir: $OUTPUT_DIR"
echo "dataset_cache_dir: $DATASET_CACHE_DIR"
[ -n "$HF_CACHE_DIR" ] && echo "hf_cache_dir: $HF_CACHE_DIR"
echo "launcher: $LAUNCHER"
[ "$LAUNCHER" = "torchrun" ] && echo "nproc_per_node: $NPROC_PER_NODE"
[ "$LAUNCHER" = "torchrun" ] && echo "master_port: $MASTER_PORT"
echo "gpus: $NUM_GPUS"
echo "cuda_visible_devices: ${CUDA_VISIBLE_DEVICES:-all}"
echo "batch_size: $BATCH_SIZE"
echo "grad_accum: $GRAD_ACCUM"
echo "epochs: $EPOCHS"
[ -n "$MAX_STEPS" ] && echo "max_steps: $MAX_STEPS"
[ -n "$RESUME" ] && echo "resume: $RESUME"
[ -n "$RESUME" ] && [ "$STREAMING_MODE" = "true" ] && echo "ignore_data_skip: true"
[ -n "$RESUME" ] && [ "$STREAMING_MODE" = "true" ] && echo "streaming_resume_global_step: $STREAMING_RESUME_GLOBAL_STEP"
[ -n "$RESUME" ] && [ "$STREAMING_MODE" = "true" ] && echo "streaming_resume_world_size: $STREAMING_RESUME_WORLD_SIZE"
[ -n "$RESUME" ] && [ "$STREAMING_MODE" = "true" ] && echo "streaming_resume_skip_samples: $STREAMING_RESUME_SKIP_SAMPLES"
[ -n "$RESUME" ] && [ "$STREAMING_MODE" = "true" ] && echo "streaming_resume_cursor_source: $STREAMING_RESUME_CURSOR_SOURCE"
[ -n "$STREAMING_SHARD_MANIFEST" ] && echo "streaming_shard_manifest: $STREAMING_SHARD_MANIFEST"
[ -n "$RESUME" ] && echo "torch_force_no_weights_only_load: ${TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD:-}"
echo "dtype: $DTYPE"
echo "preprocessing_workers: $PREPROCESSING_NUM_WORKERS"
echo "dataloader_workers_per_rank: $DATALOADER_NUM_WORKERS"
if [ "$LAUNCHER" = "torchrun" ]; then
  echo "dataloader_workers_total: $((DATALOADER_NUM_WORKERS * NPROC_PER_NODE))"
else
  echo "dataloader_workers_total: $DATALOADER_NUM_WORKERS"
fi
echo "streaming: $STREAMING_MODE"
echo "streaming_reader: $STREAMING_READER"
echo "streaming_shuffle_buffer: $STREAMING_SHUFFLE_BUFFER"
echo "streaming_shuffle_seed: $STREAMING_SHUFFLE_SEED"
[ -n "$RESUME" ] && [ "$STREAMING_MODE" = "true" ] && echo "streaming_resume_mode: $STREAMING_RESUME_MODE"
echo "auto_shard: $AUTO_SHARD_ENABLED"
if [ "$AUTO_SHARD_ENABLED" = true ]; then
  echo "shard_dir: $SHARD_DIR"
  echo "shard_count: $SHARD_COUNT"
  echo "shard_seed: $SHARD_SEED"
fi
echo "tf32: $TF32"
echo "use_triton_flash_attn: $USE_TRITON_FLASH_ATTN"
echo "python: $($PYTHON --version)"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader
fi
echo "========================="

TRAIN_CMD=(
  main.py pretrain
  --output_dir "$OUTPUT_DIR"
  --model_name_or_path "$MODEL_NAME"
  "${CACHE_ARGS[@]}"
  --init_mode "$INIT_MODE"
  --do_train
  --train_file "$EFFECTIVE_TRAIN_FILE"
  --dataset_cache_dir "$DATASET_CACHE_DIR"
  --line_by_line
  --max_seq_length "$MAX_SEQ_LENGTH"
  --per_device_train_batch_size "$BATCH_SIZE"
  --gradient_accumulation_steps "$GRAD_ACCUM"
  --num_train_epochs "$EPOCHS"
  "${MAX_STEP_ARGS[@]}"
  --learning_rate "$LR"
  --warmup_steps "$WARMUP_STEPS"
  --mlm_probability "$MLM_PROBABILITY"
  --preprocessing_num_workers "$PREPROCESSING_NUM_WORKERS"
  --dataloader_num_workers "$DATALOADER_NUM_WORKERS"
  --tf32 "$TF32"
  "${PRECISION_ARGS[@]}"
  "${FLASH_ATTN_ARGS[@]}"
  "${DDP_ARGS[@]}"
  "${STREAMING_ARGS[@]}"
  --streaming_reader "$STREAMING_READER"
  --streaming_shuffle_buffer "$STREAMING_SHUFFLE_BUFFER"
  --streaming_shuffle_seed "$STREAMING_SHUFFLE_SEED"
  --streaming_resume_mode "$STREAMING_RESUME_MODE"
  --save_steps "$SAVE_STEPS"
  --save_total_limit "$SAVE_TOTAL_LIMIT"
  --logging_steps "$LOGGING_STEPS"
  --overwrite_output_dir
  --report_to none
  "${RESUME_ARGS[@]}"
  "${TRAIN_ARGS[@]}"
)

if [ "$LAUNCHER" = "torchrun" ]; then
  "$PYTHON" -m torch.distributed.run \
    --nnodes 1 \
    --nproc_per_node "$NPROC_PER_NODE" \
    --master_port "$MASTER_PORT" \
    "${TRAIN_CMD[@]}"
else
  "$PYTHON" "${TRAIN_CMD[@]}"
fi

echo ""
echo "===== training finished ====="
echo "workspace: $WORKSPACE"
echo "output_dir: $OUTPUT_DIR"
