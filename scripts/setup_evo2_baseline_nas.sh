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
VTX_WHEEL_URL="https://files.pythonhosted.org/packages/e2/ed/9dab64893b6b78f832e4d18522bbd6696350a415c20e0af6bcea1b0f8152/vtx-1.1.0-py3-none-any.whl#sha256=0ff9f1db2f9e81e288150b60fd4fe4832b8b992ac2c6c947271b2036ffeb8299"

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

"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel ninja packaging psutil

if ! "$VENV_DIR/bin/python" -c "import flash_attn; assert flash_attn.__version__ == '2.8.0.post2'" >/dev/null 2>&1; then
  "$VENV_DIR/bin/python" - <<'PY'
import platform
import sys
from pathlib import Path

import torch
from torch.utils.cpp_extension import CUDA_HOME

actual = {
    "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    "torch": torch.__version__,
    "cuda": torch.version.cuda,
    "cxx11_abi": torch._C._GLIBCXX_USE_CXX11_ABI,
    "cuda_home": CUDA_HOME,
    "machine": platform.machine(),
}
compatible = (
    sys.version_info[:2] == (3, 11)
    and torch.__version__.startswith("2.7.")
    and torch.version.cuda is not None
    and torch.version.cuda.startswith("12.")
    and CUDA_HOME is not None
    and (Path(CUDA_HOME) / "bin" / "nvcc").is_file()
    and platform.machine() == "x86_64"
)
if not compatible:
    raise RuntimeError(f"Cannot build FlashAttention in this environment: {actual}")
print(f"FlashAttention build environment: {actual}")
PY
  echo "Building FlashAttention 2.8.0.post2 for the worker Torch ABI (A100 sm80 only)..."
  FLASH_ATTENTION_FORCE_BUILD=TRUE \
  FLASH_ATTN_CUDA_ARCHS=80 \
  MAX_JOBS=8 \
  NVCC_THREADS=4 \
    "$VENV_DIR/bin/python" -m pip install \
    --no-cache-dir \
    --no-binary flash-attn \
    --no-build-isolation \
    --force-reinstall \
    --no-deps \
    "flash-attn==2.8.0.post2"
  "$VENV_DIR/bin/python" -c "import flash_attn, flash_attn_2_cuda; assert flash_attn.__version__ == '2.8.0.post2'; print('FlashAttention CUDA extension is ready')"
fi

if ! "$VENV_DIR/bin/python" -c "from importlib.metadata import version; assert version('vtx') == '1.1.0'" >/dev/null 2>&1; then
  "$VENV_DIR/bin/python" -m pip install \
    --no-cache-dir \
    "$VTX_WHEEL_URL"
fi

"$VENV_DIR/bin/python" -m pip install "evo2==0.6.0"

"$VENV_DIR/bin/python" "$REPO_ROOT/data_process/download_evo2_baseline.py" \
  --output-dir "$MODEL_DIR"

"$VENV_DIR/bin/python" -c "import evo2, torch; print('Evo 2 environment ready; torch=' + torch.__version__)"
echo "venv: $VENV_DIR"
echo "model: $MODEL_PATH"
