#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="/data00/home/wangzhi.wit/models/mRNABERT"
PROJECT_CONFIG="${REPOSITORY_ROOT}/design-flow/projects/three-protein/project.json"
RUNTIME_ROOT="/data00/home/wangzhi.wit/models/design-flow-runtime/three-protein"
VAXFLOW="${REPOSITORY_ROOT}/design-flow/vaxflow"

if [[ "$#" -ne 1 ]] || [[ "${1#/}" == "$1" ]] || [[ ! -d "$1" ]]; then
  printf 'Usage: %s /absolute/path/to/verified-stage4-5-run\n' "$0" >&2
  exit 2
fi
STAGE5_RUN="$1"

"${VAXFLOW}" verify-run "${STAGE5_RUN}"
"${VAXFLOW}" init-stage6 \
  "${PROJECT_CONFIG}" \
  --from-run "${STAGE5_RUN}" \
  --refresh-selection
"${VAXFLOW}" run-stage6 \
  "${PROJECT_CONFIG}" \
  --from-run "${STAGE5_RUN}"

latest_run="$(jq -r '.run_path // empty' "${RUNTIME_ROOT}/runs/latest.json" 2>/dev/null || true)"
if [[ -z "${latest_run}" ]] || [[ ! -d "${latest_run}" ]]; then
  printf 'Unable to resolve the generated Stage 6 run from latest.json\n' >&2
  exit 1
fi

"${VAXFLOW}" verify-run "${latest_run}"
printf 'Stage 6 routed run passed: %s\n' "${latest_run}"
