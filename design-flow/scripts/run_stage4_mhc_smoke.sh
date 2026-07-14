#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="/data00/home/wangzhi.wit/models/mRNABERT"
PROJECT_CONFIG="${REPOSITORY_ROOT}/design-flow/projects/three-protein/project.json"
STAGE3_RUN="/data00/home/wangzhi.wit/models/design-flow-runtime/three-protein/runs/20260713T154013507984Z-stage3-8ac573ab"
RUNTIME_ROOT="/data00/home/wangzhi.wit/models/design-flow-runtime/three-protein"
NETMHCPAN_ROOT="/data00/home/wangzhi.wit/models/netMHCpan-4.2"
NETMHCIIPAN_ROOT="/data00/home/wangzhi.wit/models/netMHCIIpan-4.3"
VAXFLOW="${REPOSITORY_ROOT}/design-flow/vaxflow"

"${REPOSITORY_ROOT}/design-flow/scripts/install_stage4_cpu_tools.sh"
"${REPOSITORY_ROOT}/design-flow/scripts/verify_stage4_cpu_tools.sh"

"${VAXFLOW}" prepare-stage4-mhc \
  "${PROJECT_CONFIG}" \
  --from-run "${STAGE3_RUN}" \
  --netmhcpan-root "${NETMHCPAN_ROOT}" \
  --netmhciipan-root "${NETMHCIIPAN_ROOT}" \
  --class-i-allele "BoLA-1:00901" \
  --class-ii-allele "BoLA-DRB3_00101"

"${VAXFLOW}" run-stage4-5 \
  "${PROJECT_CONFIG}" \
  --from-run "${STAGE3_RUN}"

latest_run="$(awk -F'"' '/"run_path":/{print $4}' "${RUNTIME_ROOT}/runs/latest.json")"
if [[ -z "${latest_run}" ]] || [[ ! -d "${latest_run}" ]]; then
  printf 'Unable to resolve the generated Stage 4/5 run from latest.json\n' >&2
  exit 1
fi

"${VAXFLOW}" verify-run "${latest_run}"
printf 'Stage 4 MHC smoke run passed: %s\n' "${latest_run}"
