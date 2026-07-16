#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="/data00/home/wangzhi.wit/models/mRNABERT"
PROJECT_CONFIG="${REPOSITORY_ROOT}/design-flow/projects/three-protein/project.json"
RUNTIME_ROOT="/data00/home/wangzhi.wit/models/design-flow-runtime/three-protein"
STAGE5_RUN="${RUNTIME_ROOT}/runs/20260716T041527036725Z-stage4-5-aae38adc"
VAXFLOW="${REPOSITORY_ROOT}/design-flow/vaxflow"

if [[ "$#" -ne 1 ]] || [[ "${1#/}" == "$1" ]] || [[ ! -f "$1" ]]; then
  printf 'Usage: %s /absolute/path/to/stage6-evo2-result.tar.gz\n' "$0" >&2
  exit 2
fi

RESULT_ARCHIVE="$1"

"${VAXFLOW}" import-stage6-evo2 \
  "${PROJECT_CONFIG}" \
  --results "${RESULT_ARCHIVE}"

"${VAXFLOW}" run-stage6 \
  "${PROJECT_CONFIG}" \
  --from-run "${STAGE5_RUN}"

LATEST_RUN="$(jq -r '.run_path // empty' "${RUNTIME_ROOT}/runs/latest.json")"
if [[ -z "${LATEST_RUN}" ]] || [[ ! -d "${LATEST_RUN}" ]]; then
  printf 'Unable to resolve the new Stage 6 run from latest.json\n' >&2
  exit 1
fi

"${VAXFLOW}" verify-run "${LATEST_RUN}"
printf 'Stage 6 with Evo 2 evidence passed: %s\n' "${LATEST_RUN}"
