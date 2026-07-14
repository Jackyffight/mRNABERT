#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="/data00/home/wangzhi.wit/models/design-flow-tools/stage4"
DOWNLOAD_ROOT="${INSTALL_ROOT}/downloads"
VERSION_ROOT="${INSTALL_ROOT}/versions"
BIN_ROOT="${INSTALL_ROOT}/bin"

DATASETS_VERSION="18.33.1"
BLAST_VERSION="2.17.0+"
MAFFT_VERSION="7.525"

DATASETS_URL="https://ftp.ncbi.nlm.nih.gov/pub/datasets/command-line/v2/linux-amd64/datasets"
DATAFORMAT_URL="https://ftp.ncbi.nlm.nih.gov/pub/datasets/command-line/v2/linux-amd64/dataformat"
BLAST_ARCHIVE="ncbi-blast-2.17.0+-x64-linux.tar.gz"
BLAST_URL="https://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/LATEST/${BLAST_ARCHIVE}"
MAFFT_ARCHIVE="mafft-7.525-without-extensions-src.tgz"
MAFFT_URL="https://mafft.cbrc.jp/alignment/software/${MAFFT_ARCHIVE}"

DATASETS_SHA256="8459ef1e87433f7b1198f5703c8cc10b55f1904cd448cf7e996c0892b141cd1f"
DATAFORMAT_SHA256="8450cf7cbdb0ed7fece567405732cd1ff838b5352faa58abbccdeff56e1ff0e8"
BLAST_SHA256="3888112d8207831aa47371d93583c601f058f88b5db22dc782438b039a3a411b"
MAFFT_SHA256="edb34ae9b26d6b55328c18fa060ed741bca8cd599c2f4f8fad0e0871c8082265"

mkdir -p "${DOWNLOAD_ROOT}" "${VERSION_ROOT}" "${BIN_ROOT}"

download_and_verify() {
  local url="$1"
  local destination="$2"
  local expected_sha256="$3"
  local observed_sha256

  if [[ -f "${destination}" ]]; then
    observed_sha256="$(sha256sum "${destination}" | awk '{print $1}')"
    if [[ "${observed_sha256}" == "${expected_sha256}" ]]; then
      printf 'Using verified cache: %s\n' "${destination}"
      return
    fi
    rm -f "${destination}"
  fi

  curl --fail --location --retry 3 --output "${destination}.partial" "${url}"
  observed_sha256="$(sha256sum "${destination}.partial" | awk '{print $1}')"
  if [[ "${observed_sha256}" != "${expected_sha256}" ]]; then
    rm -f "${destination}.partial"
    printf 'SHA256 mismatch for %s: expected=%s observed=%s\n' \
      "${url}" "${expected_sha256}" "${observed_sha256}" >&2
    exit 1
  fi
  mv "${destination}.partial" "${destination}"
}

download_and_verify "${DATASETS_URL}" "${DOWNLOAD_ROOT}/datasets" "${DATASETS_SHA256}"
download_and_verify "${DATAFORMAT_URL}" "${DOWNLOAD_ROOT}/dataformat" "${DATAFORMAT_SHA256}"
download_and_verify "${BLAST_URL}" "${DOWNLOAD_ROOT}/${BLAST_ARCHIVE}" "${BLAST_SHA256}"
download_and_verify "${MAFFT_URL}" "${DOWNLOAD_ROOT}/${MAFFT_ARCHIVE}" "${MAFFT_SHA256}"

NCBI_ROOT="${VERSION_ROOT}/ncbi-datasets-${DATASETS_VERSION}"
mkdir -p "${NCBI_ROOT}/bin"
install -m 0755 "${DOWNLOAD_ROOT}/datasets" "${NCBI_ROOT}/bin/datasets"
install -m 0755 "${DOWNLOAD_ROOT}/dataformat" "${NCBI_ROOT}/bin/dataformat"

BLAST_ROOT="${VERSION_ROOT}/ncbi-blast-${BLAST_VERSION}"
if [[ ! -x "${BLAST_ROOT}/bin/blastp" ]]; then
  temporary_blast="$(mktemp -d "${INSTALL_ROOT}/.blast.XXXXXX")"
  trap 'rm -rf "${temporary_blast:-}" "${temporary_mafft:-}"' EXIT
  tar -xzf "${DOWNLOAD_ROOT}/${BLAST_ARCHIVE}" -C "${temporary_blast}"
  rm -rf "${BLAST_ROOT}"
  mv "${temporary_blast}/ncbi-blast-${BLAST_VERSION}" "${BLAST_ROOT}"
  rmdir "${temporary_blast}"
  temporary_blast=""
fi

MAFFT_ROOT="${VERSION_ROOT}/mafft-${MAFFT_VERSION}"
if [[ ! -x "${MAFFT_ROOT}/bin/mafft" ]] \
  || ! grep -Fq "${MAFFT_ROOT}/libexec/mafft" "${MAFFT_ROOT}/bin/mafft"; then
  temporary_mafft="$(mktemp -d "${INSTALL_ROOT}/.mafft.XXXXXX")"
  trap 'rm -rf "${temporary_blast:-}" "${temporary_mafft:-}"' EXIT
  tar -xzf "${DOWNLOAD_ROOT}/${MAFFT_ARCHIVE}" -C "${temporary_mafft}"
  source_root="${temporary_mafft}/mafft-${MAFFT_VERSION}-without-extensions"
  make -C "${source_root}/core" -j "$(nproc)" PREFIX="${MAFFT_ROOT}"
  rm -rf "${MAFFT_ROOT}"
  make -C "${source_root}/core" install PREFIX="${MAFFT_ROOT}"
  rm -rf "${temporary_mafft}"
  temporary_mafft=""
fi

ln -sfn "${NCBI_ROOT}/bin/datasets" "${BIN_ROOT}/datasets"
ln -sfn "${NCBI_ROOT}/bin/dataformat" "${BIN_ROOT}/dataformat"
for command_name in blastp makeblastdb blastdbcmd; do
  ln -sfn "${BLAST_ROOT}/bin/${command_name}" "${BIN_ROOT}/${command_name}"
done
ln -sfn "${MAFFT_ROOT}/bin/mafft" "${BIN_ROOT}/mafft"

manifest_path="${INSTALL_ROOT}/toolchain-manifest.txt"
{
  printf 'install_root=%s\n' "${INSTALL_ROOT}"
  printf 'ncbi_datasets_version=%s\n' "${DATASETS_VERSION}"
  printf 'ncbi_datasets_sha256=%s\n' "${DATASETS_SHA256}"
  printf 'dataformat_sha256=%s\n' "${DATAFORMAT_SHA256}"
  printf 'blast_version=%s\n' "${BLAST_VERSION}"
  printf 'blast_archive_sha256=%s\n' "${BLAST_SHA256}"
  printf 'mafft_version=%s\n' "${MAFFT_VERSION}"
  printf 'mafft_archive_sha256=%s\n' "${MAFFT_SHA256}"
  printf 'netmhcpan_status=manual_download_required\n'
  printf 'netmhciipan_status=manual_download_required\n'
} > "${manifest_path}"

printf 'Stage 4 CPU tools installed in %s\n' "${INSTALL_ROOT}"
printf 'Run the verifier: %s\n' \
  "/data00/home/wangzhi.wit/models/mRNABERT/design-flow/scripts/verify_stage4_cpu_tools.sh"
