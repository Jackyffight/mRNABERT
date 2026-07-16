#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="/data00/home/wangzhi.wit/models/mRNABERT"
PROJECT_CONFIG="${REPOSITORY_ROOT}/design-flow/projects/three-protein/project.json"
VAXFLOW="${REPOSITORY_ROOT}/design-flow/vaxflow"
DEFAULT_STAGE6_RUN="/data00/home/wangzhi.wit/models/design-flow-runtime/three-protein/runs/20260716T060350582289Z-stage6-29403999"
OUTPUT_ROOT="/data00/home/wangzhi.wit/models/design-flow-runtime/three-protein/transfer/stage6-evo2"

if [[ "$#" -gt 1 ]]; then
  printf 'Usage: %s [/absolute/path/to/verified-stage6-run]\n' "$0" >&2
  exit 2
fi

STAGE6_RUN="${1:-${DEFAULT_STAGE6_RUN}}"
if [[ "${STAGE6_RUN#/}" == "${STAGE6_RUN}" ]] || [[ ! -d "${STAGE6_RUN}" ]]; then
  printf 'Stage 6 run must be an existing absolute directory: %s\n' "${STAGE6_RUN}" >&2
  exit 2
fi

"${VAXFLOW}" prepare-stage6-evo2 \
  "${PROJECT_CONFIG}" \
  --from-run "${STAGE6_RUN}" \
  --output-root "${OUTPUT_ROOT}"
