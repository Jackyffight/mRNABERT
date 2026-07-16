#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="/data00/home/wangzhi.wit/models/mRNABERT"
VAXFLOW="${REPOSITORY_ROOT}/design-flow/vaxflow"
CODON_ROOT="/data00/home/wangzhi.wit/models/design-flow-runtime/three-protein/input/stage6/codon-usage"
SOURCE_ROOT="${CODON_ROOT}/sources"
ASSEMBLY="GCF_002263795.3_ARS-UCD2.0"
ANNOTATION_RELEASE="RS_2024_12"
SOURCE_NAME="${ASSEMBLY}_cds_from_genomic.fna.gz"
SOURCE_URL="https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/002/263/795/${ASSEMBLY}/${SOURCE_NAME}"
EXPECTED_MD5="f7a8f7cf1c230de8d136b4ca1a067a06"
SOURCE_PATH="${SOURCE_ROOT}/${SOURCE_NAME}"
OUTPUT_PATH="${CODON_ROOT}/bos-taurus-${ASSEMBLY}-${ANNOTATION_RELEASE}-longest-per-gene.json"
AUDIT_PATH="${CODON_ROOT}/bos-taurus-${ASSEMBLY}-${ANNOTATION_RELEASE}-longest-per-gene.audit.json"

mkdir -p "${SOURCE_ROOT}"

verify_source() {
  local observed_md5
  observed_md5="$(md5sum "${SOURCE_PATH}" | awk '{print $1}')"
  if [[ "${observed_md5}" != "${EXPECTED_MD5}" ]]; then
    printf 'NCBI source MD5 mismatch: expected=%s observed=%s path=%s\n' \
      "${EXPECTED_MD5}" "${observed_md5}" "${SOURCE_PATH}" >&2
    return 1
  fi
}

if [[ ! -f "${SOURCE_PATH}" ]] || ! verify_source; then
  rm -f "${SOURCE_PATH}.partial"
  curl \
    --fail \
    --location \
    --retry 3 \
    --retry-all-errors \
    --output "${SOURCE_PATH}.partial" \
    "${SOURCE_URL}"
  mv "${SOURCE_PATH}.partial" "${SOURCE_PATH}"
  verify_source
fi

"${VAXFLOW}" build-codon-usage \
  "${SOURCE_PATH}" \
  "${OUTPUT_PATH}" \
  --audit-output "${AUDIT_PATH}" \
  --species "Bos taurus" \
  --taxon-id 9913 \
  --assembly "${ASSEMBLY}" \
  --annotation-release "${ANNOTATION_RELEASE}" \
  --source-url "${SOURCE_URL}" \
  --expected-md5 "${EXPECTED_MD5}" \
  --selection-method longest-valid-cds-per-gene

printf 'Bos taurus codon table is ready: %s\n' "${OUTPUT_PATH}"
printf 'Derivation audit: %s\n' "${AUDIT_PATH}"
