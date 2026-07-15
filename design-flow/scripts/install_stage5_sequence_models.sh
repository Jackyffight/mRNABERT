#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="/data00/home/wangzhi.wit/models/design-flow-tools/stage5"
SOURCE_ROOT="${INSTALL_ROOT}/sources"
DOWNLOAD_ROOT="${INSTALL_ROOT}/downloads"
VENV_ROOT="${INSTALL_ROOT}/venv"
MODEL_ROOT="${INSTALL_ROOT}/models/tmbed-prott5-xl-u50"
PYTHON_BOOTSTRAP="/usr/bin/python3.11"
VIRTUALENV_VERSION="20.39.1"
VIRTUALENV_ROOT="${INSTALL_ROOT}/bootstrap/virtualenv-${VIRTUALENV_VERSION}"
TMBED_REVISION="8cee893523eb655bc9485c00c65336d27a236191"
METAPREDICT_REVISION="34ddeefba8285c57fb5307792ce5f6789f860bef"
TMBED_SOURCE="${SOURCE_ROOT}/TMbed-${TMBED_REVISION}"
METAPREDICT_SOURCE="${SOURCE_ROOT}/metapredict-${METAPREDICT_REVISION}"
TMBED_ARCHIVE="${DOWNLOAD_ROOT}/TMbed-${TMBED_REVISION}.zip"
METAPREDICT_ARCHIVE="${DOWNLOAD_ROOT}/metapredict-${METAPREDICT_REVISION}.zip"
VENV_PYTHON="${VENV_ROOT}/bin/python"

mkdir -p "${SOURCE_ROOT}" "${DOWNLOAD_ROOT}" "${INSTALL_ROOT}/models"

verify_archive_source() {
  local destination="$1"
  local revision="$2"

  "${PYTHON_BOOTSTRAP}" - "${destination}" "${revision}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
expected_revision = sys.argv[2]
provenance_path = root / ".source-provenance.json"
if not provenance_path.is_file():
    raise SystemExit(f"Missing archive source provenance: {provenance_path}")
provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
if provenance.get("revision") != expected_revision:
    raise SystemExit(
        f"Archive source revision mismatch: expected={expected_revision} "
        f"observed={provenance.get('revision')}"
    )
for relative_path, expected_sha256 in provenance.get("files", {}).items():
    source_file = root / relative_path
    if not source_file.is_file():
        raise SystemExit(f"Archive source file is missing: {source_file}")
    observed_sha256 = hashlib.sha256(source_file.read_bytes()).hexdigest()
    if observed_sha256 != expected_sha256:
        raise SystemExit(f"Archive source file checksum mismatch: {source_file}")
PY
}

extract_revision_archive() {
  local archive_path="$1"
  local repository_url="$2"
  local revision="$3"
  local destination="$4"

  rm -rf "${destination}.partial"
  "${PYTHON_BOOTSTRAP}" - \
    "${archive_path}" \
    "${repository_url}" \
    "${revision}" \
    "${destination}.partial" <<'PY'
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tempfile
import zipfile

archive_path = Path(sys.argv[1]).resolve()
repository_url = sys.argv[2]
revision = sys.argv[3]
destination = Path(sys.argv[4]).resolve()

with tempfile.TemporaryDirectory(
    prefix=f".{destination.name}.", dir=destination.parent
) as temporary_dir:
    extraction_root = Path(temporary_dir)
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if not members:
            raise SystemExit(f"Source archive is empty: {archive_path}")
        for member in members:
            relative = PurePosixPath(member.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise SystemExit(f"Unsafe source archive member: {member.filename}")
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise SystemExit(f"Source archive symlink is not allowed: {member.filename}")
        archive.extractall(extraction_root)

    roots = [path for path in extraction_root.iterdir() if path.is_dir()]
    if len(roots) != 1:
        raise SystemExit(
            f"Expected one source directory in {archive_path}, found {len(roots)}"
        )
    extracted_root = roots[0]
    files = {
        str(path.relative_to(extracted_root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(extracted_root.rglob("*"))
        if path.is_file()
    }
    provenance = {
        "schema_version": "vaxflow.source-archive.v1",
        "repository_url": repository_url,
        "revision": revision,
        "archive_filename": archive_path.name,
        "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "files": files,
    }
    (extracted_root / ".source-provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    shutil.move(str(extracted_root), destination)
PY
  mv "${destination}.partial" "${destination}"
}

materialize_revision() {
  local repository_url="$1"
  local revision="$2"
  local destination="$3"
  local archive_path="$4"
  local observed_revision

  if [[ -d "${destination}/.git" ]]; then
    observed_revision="$(git -C "${destination}" rev-parse HEAD)"
    if [[ "${observed_revision}" != "${revision}" ]]; then
      printf 'Source revision mismatch: %s expected=%s observed=%s\n' \
        "${destination}" "${revision}" "${observed_revision}" >&2
      exit 1
    fi
    return
  fi
  if [[ -f "${destination}/.source-provenance.json" ]]; then
    verify_archive_source "${destination}" "${revision}"
    return
  fi
  if [[ -e "${destination}" ]]; then
    printf 'Unrecognized source directory: %s\n' "${destination}" >&2
    exit 1
  fi
  if [[ -f "${archive_path}" ]]; then
    printf 'Using offline source archive: %s\n' "${archive_path}"
    extract_revision_archive \
      "${archive_path}" "${repository_url}" "${revision}" "${destination}"
    verify_archive_source "${destination}" "${revision}"
  else
    rm -rf "${destination}.partial"
    git clone --filter=blob:none "${repository_url}" "${destination}.partial"
    git -C "${destination}.partial" checkout --detach "${revision}"
    mv "${destination}.partial" "${destination}"
    observed_revision="$(git -C "${destination}" rev-parse HEAD)"
    if [[ "${observed_revision}" != "${revision}" ]]; then
      printf 'Source revision mismatch: %s expected=%s observed=%s\n' \
        "${destination}" "${revision}" "${observed_revision}" >&2
      exit 1
    fi
  fi
}

materialize_revision \
  "https://github.com/BernhoferM/TMbed.git" \
  "${TMBED_REVISION}" \
  "${TMBED_SOURCE}" \
  "${TMBED_ARCHIVE}"
materialize_revision \
  "https://github.com/idptools/metapredict.git" \
  "${METAPREDICT_REVISION}" \
  "${METAPREDICT_SOURCE}" \
  "${METAPREDICT_ARCHIVE}"

venv_is_healthy() {
  [[ -x "${VENV_PYTHON}" ]] \
    && "${VENV_PYTHON}" -m pip --version >/dev/null 2>&1
}

install_virtualenv_bootstrap() {
  local observed_version=""

  if [[ -d "${VIRTUALENV_ROOT}" ]]; then
    observed_version="$(
      PYTHONPATH="${VIRTUALENV_ROOT}" \
        "${PYTHON_BOOTSTRAP}" -c \
          'import importlib.metadata; print(importlib.metadata.version("virtualenv"))' \
          2>/dev/null || true
    )"
  fi
  if [[ "${observed_version}" == "${VIRTUALENV_VERSION}" ]]; then
    return
  fi

  rm -rf "${VIRTUALENV_ROOT}.partial"
  "${PYTHON_BOOTSTRAP}" -m pip install \
    --disable-pip-version-check \
    --target "${VIRTUALENV_ROOT}.partial" \
    "virtualenv==${VIRTUALENV_VERSION}"
  rm -rf "${VIRTUALENV_ROOT}"
  mv "${VIRTUALENV_ROOT}.partial" "${VIRTUALENV_ROOT}"
}

if ! venv_is_healthy; then
  printf 'Creating isolated Stage 5 environment with virtualenv %s\n' \
    "${VIRTUALENV_VERSION}"
  install_virtualenv_bootstrap
  rm -rf "${VENV_ROOT}.partial" "${VENV_ROOT}"
  PYTHONPATH="${VIRTUALENV_ROOT}" \
    "${PYTHON_BOOTSTRAP}" -m virtualenv \
      --system-site-packages \
      "${VENV_ROOT}.partial"
  mv "${VENV_ROOT}.partial" "${VENV_ROOT}"
fi
if ! venv_is_healthy; then
  printf 'Stage 5 virtual environment is incomplete: %s\n' "${VENV_ROOT}" >&2
  exit 1
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
  "${METAPREDICT_REVISION}" \
  "${VIRTUALENV_VERSION}" <<'PY'
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
    virtualenv_version,
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
        "environment_builder": f"virtualenv=={virtualenv_version}",
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
