#!/usr/bin/env bash
# Print all NAS-local checkpoint validation results.

set -euo pipefail

EVAL_ROOT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_runs/mrnabert-full-devbox-20260707024008/eval"

shopt -s nullglob
FILES=("$EVAL_ROOT"/checkpoint-*-valid*/eval_results.json)
if [ "${#FILES[@]}" -eq 0 ]; then
  echo "No eval_results.json files found under: $EVAL_ROOT" >&2
  exit 1
fi

for FILE in "${FILES[@]}"; do
  echo "=== $FILE ==="
  cat "$FILE"
  echo
done
