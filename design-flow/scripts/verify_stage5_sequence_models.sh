#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="/data00/home/wangzhi.wit/models/design-flow-tools/stage5"
TOOLCHAIN_MANIFEST="${INSTALL_ROOT}/toolchain.json"
VENV_PYTHON="${INSTALL_ROOT}/venv/bin/python"
MODEL_ROOT="${INSTALL_ROOT}/models/tmbed-prott5-xl-u50"
TMBED_REVISION="8cee893523eb655bc9485c00c65336d27a236191"
METAPREDICT_REVISION="34ddeefba8285c57fb5307792ce5f6789f860bef"
TRANSFORMERS_VERSION="4.57.6"
TMBED_SOURCE="${INSTALL_ROOT}/sources/TMbed-${TMBED_REVISION}"
METAPREDICT_SOURCE="${INSTALL_ROOT}/sources/metapredict-${METAPREDICT_REVISION}"

for required_path in \
  "${TOOLCHAIN_MANIFEST}" \
  "${INSTALL_ROOT}/requirements.freeze.txt" \
  "${VENV_PYTHON}" \
  "${MODEL_ROOT}/config.json" \
  "${TMBED_SOURCE}" \
  "${METAPREDICT_SOURCE}"; do
  if [[ ! -e "${required_path}" ]]; then
    printf 'Missing Stage 5 toolchain artifact: %s\n' "${required_path}" >&2
    exit 1
  fi
done

verify_source() {
  local source_root="$1"
  local expected_revision="$2"

  if [[ -d "${source_root}/.git" ]]; then
    if [[ "$(git -C "${source_root}" rev-parse HEAD)" != "${expected_revision}" ]]; then
      printf 'Source revision mismatch: %s\n' "${source_root}" >&2
      exit 1
    fi
    if ! git -C "${source_root}" diff --quiet; then
      printf 'Source checkout contains tracked modifications: %s\n' "${source_root}" >&2
      exit 1
    fi
    return
  fi

  "${VENV_PYTHON}" - "${source_root}" "${expected_revision}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
expected_revision = sys.argv[2]
provenance_path = root / ".source-provenance.json"
if not provenance_path.is_file():
    raise SystemExit(f"Missing source provenance: {provenance_path}")
provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
if provenance.get("revision") != expected_revision:
    raise SystemExit(f"Source revision mismatch: {root}")
for relative_path, expected_sha256 in provenance.get("files", {}).items():
    source_file = root / relative_path
    if not source_file.is_file():
        raise SystemExit(f"Source file is missing: {source_file}")
    if hashlib.sha256(source_file.read_bytes()).hexdigest() != expected_sha256:
        raise SystemExit(f"Source file checksum mismatch: {source_file}")
PY
}

verify_source "${TMBED_SOURCE}" "${TMBED_REVISION}"
verify_source "${METAPREDICT_SOURCE}" "${METAPREDICT_REVISION}"

"${VENV_PYTHON}" - <<'PY'
import importlib.metadata
import json
import torch
import metapredict
import tmbed
import transformers
from transformers import T5Tokenizer

versions = {
    "cuda_available": torch.cuda.is_available(),
    "metapredict": importlib.metadata.version("metapredict"),
    "tmbed": importlib.metadata.version("tmbed"),
    "torch": torch.__version__,
    "transformers": transformers.__version__,
}
if versions["tmbed"] != "1.0.2":
    raise SystemExit(f"TMbed version mismatch: {versions['tmbed']}")
if versions["transformers"] != "4.57.6":
    raise SystemExit(f"Transformers version mismatch: {versions['transformers']}")
if not hasattr(T5Tokenizer, "batch_encode_plus"):
    raise SystemExit("Transformers T5Tokenizer lacks TMbed's required API")
print(json.dumps(versions, sort_keys=True))
PY

printf 'Stage 5 sequence-model toolchain verified: %s\n' "${INSTALL_ROOT}"
