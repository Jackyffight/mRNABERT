#!/usr/bin/env bash
# Copy the existing HDFS training output/checkpoints to NAS for runtime use.

set -euo pipefail

HDFS_OUTPUT="/mnt/hdfs/byte_neptune_ai/mrna/train/runs/mrnabert-full-devbox-20260707024008/output"
NAS_OUTPUT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/mrnabert-full-devbox-20260707024008/output"

if [ ! -d "$HDFS_OUTPUT" ]; then
  echo "HDFS output not found: $HDFS_OUTPUT" >&2
  exit 1
fi

mkdir -p "$NAS_OUTPUT"

if command -v rsync >/dev/null 2>&1; then
  rsync -a --info=progress2 "$HDFS_OUTPUT/" "$NAS_OUTPUT/"
else
  cp -a "$HDFS_OUTPUT/." "$NAS_OUTPUT/"
fi

echo "NAS output is ready: $NAS_OUTPUT"
find "$NAS_OUTPUT" -maxdepth 1 -type d -name 'checkpoint-*' | sort -V
