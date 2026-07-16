#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="/data00/home/wangzhi.wit/models/mRNABERT"
PROJECT_CONFIG="${REPOSITORY_ROOT}/design-flow/projects/three-protein/project.json"
VAXFLOW="${REPOSITORY_ROOT}/design-flow/vaxflow"
BUILD_CODON_TABLE="${REPOSITORY_ROOT}/design-flow/scripts/build_bos_taurus_codon_usage.sh"
STAGE5_RUN="/data00/home/wangzhi.wit/models/design-flow-runtime/three-protein/runs/20260716T041527036725Z-stage4-5-aae38adc"
CODON_TABLE="/data00/home/wangzhi.wit/models/design-flow-runtime/three-protein/input/stage6/codon-usage/bos-taurus-GCF_002263795.3_ARS-UCD2.0-RS_2024_12-longest-per-gene.json"

"${BUILD_CODON_TABLE}"

"${VAXFLOW}" configure-stage6-mrna-codon-generation \
  "${PROJECT_CONFIG}" \
  --codon-table "${CODON_TABLE}" \
  --designs-per-candidate 4 \
  --search-multiplier 32 \
  --seed 42

"${VAXFLOW}" run-stage6 \
  "${PROJECT_CONFIG}" \
  --from-run "${STAGE5_RUN}"
