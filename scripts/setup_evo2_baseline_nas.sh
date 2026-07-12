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
FLASH_ATTN_WHEEL_URL="https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.0.post2/flash_attn-2.8.0.post2%2Bcu12torch2.7cxx11abiFALSE-cp311-cp311-linux_x86_64.whl"

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
  "$VENV_DIR/bin/python" - <<'PY'
import platform
import sys

import torch

actual = {
    "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    "torch": torch.__version__,
    "cuda": torch.version.cuda,
    "cxx11_abi": torch._C._GLIBCXX_USE_CXX11_ABI,
    "machine": platform.machine(),
}
compatible = (
    sys.version_info[:2] == (3, 11)
    and torch.__version__.startswith("2.7.")
    and torch.version.cuda is not None
    and torch.version.cuda.startswith("12.")
    and not torch._C._GLIBCXX_USE_CXX11_ABI
    and platform.machine() == "x86_64"
)
if not compatible:
    raise RuntimeError(f"No pinned FlashAttention wheel for this environment: {actual}")
PY
  # Installing the release wheel directly avoids flash-attn's cross-filesystem wheel-cache rename.
  "$VENV_DIR/bin/python" -m pip install \
    --no-cache-dir \
    --force-reinstall \
    --no-deps \
    "$FLASH_ATTN_WHEEL_URL"
fi

"$VENV_DIR/bin/python" -m pip install \
  "vtx==1.1.0" \
  "evo2==0.6.0"

"$VENV_DIR/bin/python" "$REPO_ROOT/data_process/download_evo2_baseline.py" \
  --output-dir "$MODEL_DIR"

"$VENV_DIR/bin/python" -c "import evo2, torch; print('Evo 2 environment ready; torch=' + torch.__version__)"
echo "venv: $VENV_DIR"
echo "model: $MODEL_PATH"
