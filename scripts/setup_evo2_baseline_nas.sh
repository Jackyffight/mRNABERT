#!/usr/bin/env bash
# Build an isolated Evo 2 environment and download the pinned 7B checkpoint to NAS.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BASELINE_ROOT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_baselines/evo2"
VENV_DIR="$BASELINE_ROOT/venv-evo2-0.6.0"
MODEL_DIR="$BASELINE_ROOT/evo2_7b-bda0089f92582d5baabf0f22d9fc85f3588f6b58"
MODEL_PATH="$MODEL_DIR/evo2_7b.pt"
MODEL_SIZE=13766621200

mkdir -p "$BASELINE_ROOT"

if [ ! -f "$MODEL_PATH" ] || [ "$(stat -c %s "$MODEL_PATH" 2>/dev/null || echo 0)" -ne "$MODEL_SIZE" ]; then
  AVAILABLE_KIB=$(df -Pk "$BASELINE_ROOT" | awk 'NR == 2 {print $4}')
  REQUIRED_KIB=$((18 * 1024 * 1024))
  if [ -n "$AVAILABLE_KIB" ] && [ "$AVAILABLE_KIB" -lt "$REQUIRED_KIB" ]; then
    echo "Evo 2 setup needs at least 18 GiB free under: $BASELINE_ROOT" >&2
    echo "Available: $((AVAILABLE_KIB / 1024 / 1024)) GiB" >&2
    exit 1
  fi
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  python -m venv --system-site-packages "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel ninja packaging

if ! "$VENV_DIR/bin/python" -c "import flash_attn; assert flash_attn.__version__ == '2.8.0.post2'" >/dev/null 2>&1; then
  MAX_JOBS=8 "$VENV_DIR/bin/python" -m pip install \
    --no-build-isolation \
    "flash-attn==2.8.0.post2"
fi

"$VENV_DIR/bin/python" -m pip install \
  "vtx==1.1.0" \
  "evo2==0.6.0"

"$VENV_DIR/bin/python" "$REPO_ROOT/data_process/download_evo2_baseline.py" \
  --output-dir "$MODEL_DIR"

"$VENV_DIR/bin/python" -c "import evo2, torch; print('Evo 2 environment ready; torch=' + torch.__version__)"
echo "venv: $VENV_DIR"
echo "model: $MODEL_PATH"
