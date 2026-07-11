#!/usr/bin/env bash
# Add checkpoint-level streaming cursor lineage to a legacy NAS checkpoint.
#
# Usage:
#   scripts/bootstrap_streaming_state_nas.sh 600000 57600000

set -euo pipefail

if [ $# -ne 2 ]; then
  echo "Usage: $0 <checkpoint_step> <next_sample_cursor>" >&2
  echo "Legacy cursor overrides cannot be recovered from checkpoint weights." >&2
  exit 1
fi

STEP="$1"
NEXT_SAMPLE_CURSOR="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CHECKPOINT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/mrnabert-full-devbox-20260707024008/output/checkpoint-$STEP"
SHARD_MANIFEST="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/data_shards/pre-3shards-seed42/manifest.json"

if [ ! -f "$CHECKPOINT/trainer_state.json" ]; then
  echo "Trainer state not found: $CHECKPOINT/trainer_state.json" >&2
  exit 1
fi
if [ ! -f "$SHARD_MANIFEST" ]; then
  echo "Shard manifest not found: $SHARD_MANIFEST" >&2
  exit 1
fi

cd "$REPO_ROOT"
python -m mrnabert.streaming_state bootstrap \
  --checkpoint "$CHECKPOINT" \
  --next-sample-cursor "$NEXT_SAMPLE_CURSOR" \
  --effective-batch 96 \
  --streaming-reader file-shard \
  --shuffle-buffer 20000 \
  --shuffle-seed 42 \
  --world-size 3 \
  --dataloader-num-workers 4 \
  --shard-manifest "$SHARD_MANIFEST"

echo "Streaming state:"
cat "$CHECKPOINT/streaming_state.json"
