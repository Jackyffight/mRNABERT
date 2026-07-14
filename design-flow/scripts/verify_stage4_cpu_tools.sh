#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="/data00/home/wangzhi.wit/models/design-flow-tools/stage4"
BIN_ROOT="${INSTALL_ROOT}/bin"

for command_name in datasets dataformat blastp makeblastdb blastdbcmd mafft; do
  if [[ ! -x "${BIN_ROOT}/${command_name}" ]]; then
    printf 'Missing executable: %s\n' "${BIN_ROOT}/${command_name}" >&2
    exit 1
  fi
done

temporary_root="$(mktemp -d "${INSTALL_ROOT}/.verify.XXXXXX")"
trap 'rm -rf "${temporary_root}"' EXIT

printf '>query\nMKTAYIAKQRQISFVKSHFSRQ\n>subject\nMKTAYIAKQRQISFVKSHFSRQ\n' \
  > "${temporary_root}/proteins.fasta"

"${BIN_ROOT}/mafft" --auto "${temporary_root}/proteins.fasta" \
  > "${temporary_root}/alignment.fasta" 2> "${temporary_root}/mafft.log"
test "$(grep -c '^>' "${temporary_root}/alignment.fasta")" -eq 2

"${BIN_ROOT}/makeblastdb" \
  -in "${temporary_root}/proteins.fasta" \
  -dbtype prot \
  -out "${temporary_root}/proteins" \
  > "${temporary_root}/makeblastdb.log"
"${BIN_ROOT}/blastp" \
  -query "${temporary_root}/proteins.fasta" \
  -db "${temporary_root}/proteins" \
  -outfmt '6 qseqid sseqid pident length' \
  -out "${temporary_root}/blast.tsv"
test -s "${temporary_root}/blast.tsv"

"${BIN_ROOT}/datasets" version
"${BIN_ROOT}/dataformat" tsv virus-genome --help > /dev/null
printf 'dataformat: checksum-pinned NCBI Datasets companion (parser check passed)\n'
"${BIN_ROOT}/blastp" -version | head -1
"${BIN_ROOT}/mafft" --version 2>&1 | head -1
printf 'Stage 4 CPU tool verification passed.\n'
