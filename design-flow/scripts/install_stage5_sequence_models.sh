#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="/data00/home/wangzhi.wit/models/design-flow-tools/stage5"
SOURCE_ROOT="${INSTALL_ROOT}/sources"
VENV_ROOT="${INSTALL_ROOT}/venv"
MODEL_ROOT="${INSTALL_ROOT}/models/tmbed-prott5-xl-u50"
PYTHON_BOOTSTRAP="/usr/bin/python3.11"
TMBED_REVISION="8cee893523eb655bc9485c00c65336d27a236191"
METAPREDICT_REVISION="34ddeefba8285c57fb5307792ce5f6789f860bef"
TMBED_SOURCE="${SOURCE_ROOT}/TMbed-${TMBED_REVISION}"
METAPREDICT_SOURCE="${SOURCE_ROOT}/metapredict-${METAPREDICT_REVISION}"
VENV_PYTHON="${VENV_ROOT}/bin/python"

mkdir -p "${SOURCE_ROOT}" "${INSTALL_ROOT}/models"

clone_revision() {
  local repository_url="$1"
  local revision="$2"
  local destination="$3"
  local observed_revision

  if [[ ! -d "${destination}/.git" ]]; then
    rm -rf "${destination}.partial"
    git clone --filter=blob:none "${repository_url}" "${destination}.partial"
    git -C "${destination}.partial" checkout --detach "${revision}"
    mv "${destination}.partial" "${destination}"
  fi
  observed_revision="$(git -C "${destination}" rev-parse HEAD)"
  if [[ "${observed_revision}" != "${revision}" ]]; then
    printf 'Source revision mismatch: %s expected=%s observed=%s\n' \
      "${destination}" "${revision}" "${observed_revision}" >&2
    exit 1
  fi
}

clone_revision \
  "https://github.com/BernhoferM/TMbed.git" \
  "${TMBED_REVISION}" \
  "${TMBED_SOURCE}"
clone_revision \
  "https://github.com/idptools/metapredict.git" \
  "${METAPREDICT_REVISION}" \
  "${METAPREDICT_SOURCE}"

if [[ ! -x "${VENV_PYTHON}" ]]; then
  "${PYTHON_BOOTSTRAP}" -m venv --system-site-packages "${VENV_ROOT}"
fi

"${VENV_PYTHON}" -m pip install --upgrade "pip>=24" "setuptools>=70" wheel
"${VENV_PYTHON}" -m pip install --editable "${TMBED_SOURCE}"
"${VENV_PYTHON}" -m pip install --editable "${METAPREDICT_SOURCE}"

"${VENV_PYTHON}" -m tmbed download \
  --no-use-gpu \
  --model-dir "${MODEL_ROOT}"

"${VENV_PYTHON}" -m pip freeze > "${INSTALL_ROOT}/requirements.freeze.txt"
"${VENV_PYTHON}" - \
  "${INSTALL_ROOT}/toolchain.json" \
  "${VENV_PYTHON}" \
  "${TMBED_SOURCE}" \
  "${METAPREDICT_SOURCE}" \
  "${MODEL_ROOT}" \
  "${INSTALL_ROOT}/requirements.freeze.txt" \
  "${TMBED_REVISION}" \
  "${METAPREDICT_REVISION}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

(
    output,
    python_executable,
    tmbed_source,
    metapredict_source,
    model_dir,
    freeze_path,
    tmbed_revision,
    metapredict_revision,
) = sys.argv[1:]
document = {
    "schema_version": "vaxflow.stage5-sequence-toolchain.v1",
    "python_executable": python_executable,
    "tmbed_source_root": tmbed_source,
    "metapredict_source_root": metapredict_source,
    "tmbed_model_dir": model_dir,
    "requirements_freeze_sha256": hashlib.sha256(
        Path(freeze_path).read_bytes()
    ).hexdigest(),
    "source_revisions": {
        "tmbed": tmbed_revision,
        "metapredict": metapredict_revision,
    },
    "installation_profile": {
        "gpu_supported": True,
        "system_site_packages": True,
        "tmbed_encoder": "Rostlab/prot_t5_xl_half_uniref50-enc",
        "metapredict_model": "V3",
    },
}
Path(output).write_text(
    json.dumps(document, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

printf 'Stage 5 sequence-model toolchain is installed: %s\n' "${INSTALL_ROOT}"
printf 'Verify with: %s\n' \
  "/data00/home/wangzhi.wit/models/mRNABERT/design-flow/scripts/verify_stage5_sequence_models.sh"
