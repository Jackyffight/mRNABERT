#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="/data00/home/wangzhi.wit/models/mRNABERT"
PROJECT_CONFIG="${REPOSITORY_ROOT}/design-flow/projects/three-protein/project.json"
RUNTIME_ROOT="/data00/home/wangzhi.wit/models/design-flow-runtime/three-protein"
NETMHCPAN_ROOT="/data00/home/wangzhi.wit/models/netMHCpan-4.2"
NETMHCIIPAN_ROOT="/data00/home/wangzhi.wit/models/netMHCIIpan-4.3"
TOOLCHAIN_ROOT="/data00/home/wangzhi.wit/models/design-flow-tools/stage5"
VAXFLOW="${REPOSITORY_ROOT}/design-flow/vaxflow"
DEVICE="${2:-cpu}"

if [[ "$#" -lt 1 ]] || [[ "$#" -gt 2 ]] \
  || [[ "${1#/}" == "$1" ]] || [[ ! -d "$1" ]] \
  || [[ ! "${DEVICE}" =~ ^(cpu|cuda|cuda:[0-9]+)$ ]]; then
  printf 'Usage: %s /absolute/path/to/verified-stage3-run [cpu|cuda|cuda:<index>]\n' "$0" >&2
  exit 2
fi
STAGE3_RUN="$1"

"${VAXFLOW}" verify-run "${STAGE3_RUN}"
"${REPOSITORY_ROOT}/design-flow/scripts/verify_stage4_cpu_tools.sh"
"${REPOSITORY_ROOT}/design-flow/scripts/verify_stage5_sequence_models.sh"

"${VAXFLOW}" init-stage4-5 \
  "${PROJECT_CONFIG}" \
  --from-run "${STAGE3_RUN}"

"${VAXFLOW}" prepare-stage4-mhc \
  "${PROJECT_CONFIG}" \
  --from-run "${STAGE3_RUN}" \
  --netmhcpan-root "${NETMHCPAN_ROOT}" \
  --netmhciipan-root "${NETMHCIIPAN_ROOT}" \
  --class-i-allele "BoLA-1:00901" \
  --class-ii-allele "BoLA-DRB3_00101"

"${VAXFLOW}" prepare-stage5-sequence-models \
  "${PROJECT_CONFIG}" \
  --from-run "${STAGE3_RUN}" \
  --toolchain-root "${TOOLCHAIN_ROOT}" \
  --device "${DEVICE}" \
  --tmbed-batch-size 4000

"${VAXFLOW}" run-stage4-5 \
  "${PROJECT_CONFIG}" \
  --from-run "${STAGE3_RUN}"

latest_run="$(jq -r '.run_path // empty' "${RUNTIME_ROOT}/runs/latest.json" 2>/dev/null || true)"
if [[ -z "${latest_run}" ]] || [[ ! -d "${latest_run}" ]]; then
  printf 'Unable to resolve the generated Stage 4/5 run from latest.json\n' >&2
  exit 1
fi

"${VAXFLOW}" verify-run "${latest_run}"
printf 'Stage 4/5 full-candidate run passed: %s\n' "${latest_run}"
