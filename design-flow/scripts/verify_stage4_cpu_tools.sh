#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="/data00/home/wangzhi.wit/models/design-flow-tools/stage4"
BIN_ROOT="${INSTALL_ROOT}/bin"

for command_name in \
  datasets dataformat blastp makeblastdb blastdbcmd mafft netMHCpan netMHCIIpan; do
  if [[ ! -x "${BIN_ROOT}/${command_name}" ]]; then
    printf 'Missing executable: %s\n' "${BIN_ROOT}/${command_name}" >&2
    exit 1
  fi
done

temporary_root="$(mktemp -d "${INSTALL_ROOT}/.verify.XXXXXX")"
trap 'rm -rf "${temporary_root}"' EXIT

printf '>query\nMKTAYIAKQRQISFVKSHFSRQ\n>subject\nMKTAYIAKQRQISFVKSHFSRQ\n' \
  > "${temporary_root}/proteins.fasta"
printf 'SYFPEITHI\n' > "${temporary_root}/mhci.peptides"
printf 'AAAGAEAGKATTEEQ\n' > "${temporary_root}/mhcii.peptides"

printf 'Checking MAFFT...\n'
"${BIN_ROOT}/mafft" --auto "${temporary_root}/proteins.fasta" \
  > "${temporary_root}/alignment.fasta" 2> "${temporary_root}/mafft.log"
test "$(grep -c '^>' "${temporary_root}/alignment.fasta")" -eq 2

printf 'Checking BLAST+...\n'
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

printf 'Checking NetMHCpan 4.2e with BoLA-1:00901...\n'
"${BIN_ROOT}/netMHCpan" \
  -p \
  -BA \
  -a BoLA-1:00901 \
  "${temporary_root}/mhci.peptides" \
  > "${temporary_root}/netmhcpan.out"
grep -Fq 'NetMHCpan version 4.2e' "${temporary_root}/netmhcpan.out"
grep -Fq 'BoLA-1:00901' "${temporary_root}/netmhcpan.out"
printf 'netMHCpan 4.2e: BoLA-I prediction check passed\n'

printf 'Checking NetMHCIIpan 4.3k with BoLA-DRB3_00101...\n'
"${BIN_ROOT}/netMHCIIpan" \
  -inptype 1 \
  -BA \
  -a BoLA-DRB3_00101 \
  -f "${temporary_root}/mhcii.peptides" \
  > "${temporary_root}/netmhciipan.out"
grep -Fq 'NetMHCIIpan version 4.3k' "${temporary_root}/netmhciipan.out"
grep -Fq 'BoLA-DRB3_00101' "${temporary_root}/netmhciipan.out"
printf 'netMHCIIpan 4.3k: BoLA-DRB3 prediction check passed\n'

"${BIN_ROOT}/datasets" version
"${BIN_ROOT}/dataformat" tsv virus-genome --help > /dev/null
printf 'dataformat: checksum-pinned NCBI Datasets companion (parser check passed)\n'
"${BIN_ROOT}/blastp" -version | head -1
"${BIN_ROOT}/mafft" --version 2>&1 | head -1
printf 'Stage 4 CPU tool verification passed.\n'
