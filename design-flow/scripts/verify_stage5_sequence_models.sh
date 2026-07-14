#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="/data00/home/wangzhi.wit/models/design-flow-tools/stage5"
TOOLCHAIN_MANIFEST="${INSTALL_ROOT}/toolchain.json"
VENV_PYTHON="${INSTALL_ROOT}/venv/bin/python"
MODEL_ROOT="${INSTALL_ROOT}/models/tmbed-prott5-xl-u50"
TMBED_REVISION="8cee893523eb655bc9485c00c65336d27a236191"
METAPREDICT_REVISION="34ddeefba8285c57fb5307792ce5f6789f860bef"
TMBED_SOURCE="${INSTALL_ROOT}/sources/TMbed-${TMBED_REVISION}"
METAPREDICT_SOURCE="${INSTALL_ROOT}/sources/metapredict-${METAPREDICT_REVISION}"

for required_path in \
  "${TOOLCHAIN_MANIFEST}" \
  "${INSTALL_ROOT}/requirements.freeze.txt" \
  "${VENV_PYTHON}" \
  "${MODEL_ROOT}/config.json" \
  "${TMBED_SOURCE}/.git" \
  "${METAPREDICT_SOURCE}/.git"; do
  if [[ ! -e "${required_path}" ]]; then
    printf 'Missing Stage 5 toolchain artifact: %s\n' "${required_path}" >&2
    exit 1
  fi
done

if [[ "$(git -C "${TMBED_SOURCE}" rev-parse HEAD)" != "${TMBED_REVISION}" ]]; then
  printf 'TMbed source revision mismatch\n' >&2
  exit 1
fi
if [[ "$(git -C "${METAPREDICT_SOURCE}" rev-parse HEAD)" != "${METAPREDICT_REVISION}" ]]; then
  printf 'metapredict source revision mismatch\n' >&2
  exit 1
fi
if ! git -C "${TMBED_SOURCE}" diff --quiet \
  || ! git -C "${METAPREDICT_SOURCE}" diff --quiet; then
  printf 'Stage 5 source checkout contains tracked modifications\n' >&2
  exit 1
fi

"${VENV_PYTHON}" - <<'PY'
import importlib.metadata
import json
import torch
import metapredict
import tmbed

versions = {
    "cuda_available": torch.cuda.is_available(),
    "metapredict": importlib.metadata.version("metapredict"),
    "tmbed": importlib.metadata.version("tmbed"),
    "torch": torch.__version__,
}
if versions["tmbed"] != "1.0.2":
    raise SystemExit(f"TMbed version mismatch: {versions['tmbed']}")
print(json.dumps(versions, sort_keys=True))
PY

printf 'Stage 5 sequence-model toolchain verified: %s\n' "${INSTALL_ROOT}"
