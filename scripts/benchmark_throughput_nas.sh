#!/usr/bin/env bash
# Benchmark NAS-local mRNABERT training throughput with short controlled runs.
#
# Usage:
#   scripts/benchmark_throughput_nas.sh
#   scripts/benchmark_throughput_nas.sh smoke
#   scripts/benchmark_throughput_nas.sh full
#
# Modes:
#   smoke: fewer cases, 120 steps each
#   quick: default, main acceleration matrix, 300 steps each
#   full : quick + extra larger-batch/single-GPU probes, 500 steps each

set -u

MODE="${1:-quick}"
if [ "$MODE" != "smoke" ] && [ "$MODE" != "quick" ] && [ "$MODE" != "full" ]; then
  echo "Usage: $0 [smoke|quick|full]" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TIMESTAMP="$(date +%Y%m%d%H%M%S)"

FULL_TRAIN_FILE="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/pre.txt"
SHARD_DIR="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/data_shards/pre-3shards-seed42"
SHARD_GLOB="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/data_shards/pre-3shards-seed42/pre_shard_*.txt"
SINGLE_SHARD="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/data_shards/pre-3shards-seed42/pre_shard_00000.txt"
MODEL_CONFIG="assets/mrnabert-base"
BENCH_ROOT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/benchmarks/throughput-${MODE}-${TIMESTAMP}"
SUMMARY_FILE="${BENCH_ROOT}/summary.tsv"

if [ ! -f "$FULL_TRAIN_FILE" ]; then
  echo "Training file not found: $FULL_TRAIN_FILE" >&2
  exit 1
fi
if [ ! -d "$SHARD_DIR" ]; then
  echo "Shard dir not found: $SHARD_DIR" >&2
  echo "Run the main train script once with --shard-dir, or rebuild shards first." >&2
  exit 1
fi
if [ ! -f "$SINGLE_SHARD" ]; then
  echo "Single shard not found: $SINGLE_SHARD" >&2
  exit 1
fi
case "$MODE" in
  smoke) MAX_STEPS=120 ;;
  quick) MAX_STEPS=300 ;;
  full) MAX_STEPS=500 ;;
esac

mkdir -p "$BENCH_ROOT"
printf "case\tstatus\tlauncher\tdevices\tbatch\tworkers\treader\tshuffle\tseq_len\ttrain_samples_per_second\ttrain_steps_per_second\ttrain_runtime\ttrain_loss\toutput_dir\n" > "$SUMMARY_FILE"

echo "Benchmark root: $BENCH_ROOT"
echo "Summary: $SUMMARY_FILE"
echo "Mode: $MODE, max_steps: $MAX_STEPS"

run_case() {
  local name="$1"
  local launcher="$2"
  local devices="$3"
  local train_file="$4"
  local reader="$5"
  local workers="$6"
  local batch="$7"
  local shuffle="$8"
  local seq_len="$9"
  local extra_auto_shard="${10}"

  local run_name="bench-${name}"
  local output_dir="${BENCH_ROOT}/${run_name}/output"
  local log_file="${BENCH_ROOT}/${run_name}.log"
  local gpu_log="${BENCH_ROOT}/${run_name}.gpu.csv"
  local monitor_pid=""

  mkdir -p "${BENCH_ROOT}/${run_name}"
  echo ""
  echo "===== case: $name ====="
  echo "log: $log_file"
  echo "gpu_log: $gpu_log"

  if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi \
      --query-gpu=timestamp,index,utilization.gpu,utilization.memory,memory.used,power.draw \
      --format=csv \
      -l 5 > "$gpu_log" 2>/dev/null &
    monitor_pid="$!"
  fi

  set +e
  (
    cd "$REPO_ROOT"
    ./run_train.sh \
      --env devbox \
      --model "$MODEL_CONFIG" \
      --init-mode scratch \
      --train-file "$train_file" \
      --shard-dir "$SHARD_DIR" \
      --output-root "$BENCH_ROOT" \
      --launcher "$launcher" \
      --devices "$devices" \
      --max-steps "$MAX_STEPS" \
      --batch-size "$batch" \
      --grad-accum 1 \
      --warmup-steps 0 \
      --logging-steps 50 \
      --save-steps 1000000 \
      --save-total-limit 1 \
      --lr 1e-5 \
      --dataloader-workers "$workers" \
      --streaming \
      --streaming-reader "$reader" \
      --streaming-shuffle-buffer "$shuffle" \
      --max-seq-length "$seq_len" \
      --run-name "$run_name" \
      $extra_auto_shard
  ) > "$log_file" 2>&1
  local status=$?
  set -e

  if [ -n "$monitor_pid" ]; then
    kill "$monitor_pid" >/dev/null 2>&1 || true
    wait "$monitor_pid" >/dev/null 2>&1 || true
  fi

  if [ "$status" -ne 0 ]; then
    printf "%s\tfailed:%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t\t\t\t\t%s\n" \
      "$name" "$status" "$launcher" "$devices" "$batch" "$workers" "$reader" "$shuffle" "$seq_len" "$output_dir" >> "$SUMMARY_FILE"
    echo "case failed: $name status=$status"
    tail -n 40 "$log_file" || true
    return 0
  fi

  python - "$name" "$launcher" "$devices" "$batch" "$workers" "$reader" "$shuffle" "$seq_len" "$output_dir" "$SUMMARY_FILE" <<'PY'
import json
import sys
from pathlib import Path

name, launcher, devices, batch, workers, reader, shuffle, seq_len, output_dir, summary_file = sys.argv[1:]
metrics_file = Path(output_dir) / "train_results.json"
metrics = {}
if metrics_file.exists():
    metrics = json.loads(metrics_file.read_text())

row = [
    name,
    "ok",
    launcher,
    devices,
    batch,
    workers,
    reader,
    shuffle,
    seq_len,
    str(metrics.get("train_samples_per_second", "")),
    str(metrics.get("train_steps_per_second", "")),
    str(metrics.get("train_runtime", "")),
    str(metrics.get("train_loss", "")),
    output_dir,
]
with open(summary_file, "a", encoding="utf-8") as handle:
    handle.write("\t".join(row) + "\n")
PY
  tail -n 20 "$log_file" | sed -n '/train metrics/,$p' || true
}

set -e

# Baseline: one GPU, no DDP, line-stride over one shard. This tells us per-GPU
# speed without all-reduce.
run_case "single_b32_w0_line" "direct" "0" "$SINGLE_SHARD" "line-stride" "0" "32" "0" "1024" "--no-auto-shard"

# Main DDP matrix: same global training shape as production, changing only CPU
# workers, batch, shuffle, and reader.
run_case "ddp_b32_w0_file" "torchrun" "0,1,2" "$SHARD_GLOB" "file-shard" "0" "32" "20000" "1024" "--no-auto-shard"
run_case "ddp_b32_w4_file" "torchrun" "0,1,2" "$SHARD_GLOB" "file-shard" "4" "32" "20000" "1024" "--no-auto-shard"

if [ "$MODE" != "smoke" ]; then
  run_case "ddp_b32_w8_file" "torchrun" "0,1,2" "$SHARD_GLOB" "file-shard" "8" "32" "20000" "1024" "--no-auto-shard"
  run_case "ddp_b32_w4_shuffle0" "torchrun" "0,1,2" "$SHARD_GLOB" "file-shard" "4" "32" "0" "1024" "--no-auto-shard"
  run_case "ddp_b48_w4_file" "torchrun" "0,1,2" "$SHARD_GLOB" "file-shard" "4" "48" "20000" "1024" "--no-auto-shard"
  run_case "ddp_b32_w4_byte_range" "torchrun" "0,1,2" "$FULL_TRAIN_FILE" "byte-range" "4" "32" "20000" "1024" "--no-auto-shard"
fi

if [ "$MODE" = "full" ]; then
  run_case "single_b64_w0_line" "direct" "0" "$SINGLE_SHARD" "line-stride" "0" "64" "0" "1024" "--no-auto-shard"
  run_case "ddp_b64_w4_file" "torchrun" "0,1,2" "$SHARD_GLOB" "file-shard" "4" "64" "20000" "1024" "--no-auto-shard"
  run_case "ddp_b32_w4_seq512" "torchrun" "0,1,2" "$SHARD_GLOB" "file-shard" "4" "32" "20000" "512" "--no-auto-shard"
fi

echo ""
echo "===== throughput summary ====="
cat "$SUMMARY_FILE"
echo ""
echo "Benchmark root: $BENCH_ROOT"
