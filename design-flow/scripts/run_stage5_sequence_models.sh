#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="/data00/home/wangzhi.wit/models/mRNABERT"
PROJECT_CONFIG="${REPOSITORY_ROOT}/design-flow/projects/three-protein/project.json"
STAGE3_RUN="/data00/home/wangzhi.wit/models/design-flow-runtime/three-protein/runs/20260713T154013507984Z-stage3-8ac573ab"
RUNTIME_ROOT="/data00/home/wangzhi.wit/models/design-flow-runtime/three-protein"
TOOLCHAIN_ROOT="/data00/home/wangzhi.wit/models/design-flow-tools/stage5"
VAXFLOW="${REPOSITORY_ROOT}/design-flow/vaxflow"
DEVICE="${1:-cpu}"

if [[ ! "${DEVICE}" =~ ^(cpu|cuda|cuda:[0-9]+)$ ]]; then
  printf 'Usage: %s [cpu|cuda|cuda:<index>]\n' "$0" >&2
  exit 2
fi

"${REPOSITORY_ROOT}/design-flow/scripts/verify_stage5_sequence_models.sh"

"${VAXFLOW}" prepare-stage5-sequence-models \
  "${PROJECT_CONFIG}" \
  --from-run "${STAGE3_RUN}" \
  --toolchain-root "${TOOLCHAIN_ROOT}" \
  --device "${DEVICE}" \
  --tmbed-batch-size 4000

"${VAXFLOW}" run-stage4-5 \
  "${PROJECT_CONFIG}" \
  --from-run "${STAGE3_RUN}"

latest_run="$(awk -F'"' '/"run_path":/{print $4}' "${RUNTIME_ROOT}/runs/latest.json")"
if [[ -z "${latest_run}" ]] || [[ ! -d "${latest_run}" ]]; then
  printf 'Unable to resolve the generated Stage 4/5 run from latest.json\n' >&2
  exit 1
fi

"${VAXFLOW}" verify-run "${latest_run}"
printf 'Stage 5 sequence-model run passed: %s\n' "${latest_run}"
