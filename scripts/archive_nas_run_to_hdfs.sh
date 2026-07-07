#!/usr/bin/env bash
# Archive the NAS run directory back to HDFS after training/evaluation is stable.

set -euo pipefail

NAS_RUN="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/mrnabert-full-devbox-20260707024008"
HDFS_RUN="/mnt/hdfs/byte_neptune_ai/mrna/train/runs/mrnabert-full-devbox-20260707024008"

if [ ! -d "$NAS_RUN" ]; then
  echo "NAS run not found: $NAS_RUN" >&2
  exit 1
fi

mkdir -p "$HDFS_RUN"

if command -v rsync >/dev/null 2>&1; then
  rsync -a --info=progress2 "$NAS_RUN/" "$HDFS_RUN/"
else
  cp -a "$NAS_RUN/." "$HDFS_RUN/"
fi

echo "Archived NAS run to HDFS:"
echo "  source: $NAS_RUN"
echo "  target: $HDFS_RUN"
