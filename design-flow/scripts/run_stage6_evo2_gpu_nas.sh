#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mRNABERT"
EVO2_ROOT="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_baselines/evo2"
EVO2_PYTHON="${EVO2_ROOT}/venv-evo2-0.6.0/bin/python"
EVO2_MODEL="${EVO2_ROOT}/evo2_7b-bda0089f92582d5baabf0f22d9fc85f3588f6b58/evo2_7b.pt"
WORKER="${REPOSITORY_ROOT}/design-flow/scripts/stage6_evo2_worker.py"

if [[ "$#" -ne 1 ]] || [[ "${1#/}" == "$1" ]] || [[ ! -f "$1" ]]; then
  printf 'Usage: %s /absolute/path/to/stage6-evo2-job.tar.gz\n' "$0" >&2
  exit 2
fi

JOB_ARCHIVE="$1"
OUTPUT_ROOT="$(dirname "${JOB_ARCHIVE}")/results"

if [[ ! -x "${EVO2_PYTHON}" ]]; then
  printf 'Pinned Evo 2 Python is missing: %s\n' "${EVO2_PYTHON}" >&2
  exit 1
fi
if [[ ! -f "${EVO2_MODEL}" ]]; then
  printf 'Pinned Evo 2 checkpoint is missing: %s\n' "${EVO2_MODEL}" >&2
  exit 1
fi
if [[ ! -f "${WORKER}" ]]; then
  printf 'Stage 6 Evo 2 worker is missing: %s\n' "${WORKER}" >&2
  exit 1
fi

"${EVO2_PYTHON}" -c "import evo2, flash_attn, torch; assert torch.cuda.is_available(); print('Evo 2 GPU environment ready:', torch.__version__)"
mkdir -p "${OUTPUT_ROOT}"

CUDA_VISIBLE_DEVICES=0 "${EVO2_PYTHON}" "${WORKER}" \
  --job-archive "${JOB_ARCHIVE}" \
  --model-path "${EVO2_MODEL}" \
  --output-root "${OUTPUT_ROOT}" \
  --device cuda:0
