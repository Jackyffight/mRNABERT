#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="/data00/home/wangzhi.wit/models/design-flow-tools/stage4"
DOWNLOAD_ROOT="${INSTALL_ROOT}/downloads"
VERSION_ROOT="${INSTALL_ROOT}/versions"
BIN_ROOT="${INSTALL_ROOT}/bin"

DATASETS_VERSION="18.33.1"
BLAST_VERSION="2.17.0+"
MAFFT_VERSION="7.525"
NETMHCPAN_VERSION="4.2e"
NETMHCIIPAN_VERSION="4.3k"

DATASETS_URL="https://ftp.ncbi.nlm.nih.gov/pub/datasets/command-line/v2/linux-amd64/datasets"
DATAFORMAT_URL="https://ftp.ncbi.nlm.nih.gov/pub/datasets/command-line/v2/linux-amd64/dataformat"
BLAST_ARCHIVE="ncbi-blast-2.17.0+-x64-linux.tar.gz"
BLAST_URL="https://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/LATEST/${BLAST_ARCHIVE}"
MAFFT_ARCHIVE="mafft-7.525-without-extensions-src.tgz"
MAFFT_URL="https://mafft.cbrc.jp/alignment/software/${MAFFT_ARCHIVE}"
NETMHCPAN_ARCHIVE="/data00/home/wangzhi.wit/models/netMHCpan-4.2estatic.Linux.tar.gz"
NETMHCIIPAN_ARCHIVE="/data00/home/wangzhi.wit/models/netMHCIIpan-4.3kstatic.Linux.tar.gz"
NETMHCPAN_ROOT="/data00/home/wangzhi.wit/models/netMHCpan-4.2"
NETMHCIIPAN_ROOT="/data00/home/wangzhi.wit/models/netMHCIIpan-4.3"

DATASETS_SHA256="8459ef1e87433f7b1198f5703c8cc10b55f1904cd448cf7e996c0892b141cd1f"
DATAFORMAT_SHA256="8450cf7cbdb0ed7fece567405732cd1ff838b5352faa58abbccdeff56e1ff0e8"
BLAST_SHA256="3888112d8207831aa47371d93583c601f058f88b5db22dc782438b039a3a411b"
MAFFT_SHA256="edb34ae9b26d6b55328c18fa060ed741bca8cd599c2f4f8fad0e0871c8082265"
NETMHCPAN_SHA256="9270ddedfc55bce87f86d129c70a21f5e01db38e6a097eba96dca7c9581ec705"
NETMHCIIPAN_SHA256="e9b01db1a956e560d282bd608358f50158021129a83cfe1112a2d939e011382e"
NETMHCPAN_BINARY_SHA256="3e7d50f924ed3b9540a6742b2e6bf928d0741b6ba0cc4d5f82cb931c45c6e03d"
NETMHCIIPAN_BINARY_SHA256="6f40aa115abbef939f7aedef451578b3813ecb8b08d04cff93d4bb7c863a9c7f"

mkdir -p "${DOWNLOAD_ROOT}" "${VERSION_ROOT}" "${BIN_ROOT}"

temporary_blast=""
temporary_mafft=""

cleanup() {
  rm -rf \
    "${temporary_blast:-}" \
    "${temporary_mafft:-}"
}
trap cleanup EXIT

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

verify_local_archive() {
  local archive="$1"
  local expected_sha256="$2"
  local observed_sha256

  if [[ ! -f "${archive}" ]]; then
    return 1
  fi
  observed_sha256="$(sha256sum "${archive}" | awk '{print $1}')"
  if [[ "${observed_sha256}" != "${expected_sha256}" ]]; then
    printf 'SHA256 mismatch for %s: expected=%s observed=%s\n' \
      "${archive}" "${expected_sha256}" "${observed_sha256}" >&2
    exit 1
  fi
  return 0
}

verify_licensed_package() {
  local package_root="$1"
  local binary_relative="$2"
  local expected_binary_sha256="$3"
  local expected_version="$4"
  local binary_path="${package_root}/${binary_relative}"
  local version_path="${package_root}/data/version"
  local observed_binary_sha256

  if [[ ! -x "${binary_path}" ]]; then
    printf 'Missing licensed tool binary: %s\n' "${binary_path}" >&2
    exit 1
  fi
  if [[ ! -f "${version_path}" ]] || ! grep -Fq "${expected_version}" "${version_path}"; then
    printf 'Licensed tool version mismatch: %s (expected %s)\n' \
      "${version_path}" "${expected_version}" >&2
    exit 1
  fi
  observed_binary_sha256="$(sha256sum "${binary_path}" | awk '{print $1}')"
  if [[ "${observed_binary_sha256}" != "${expected_binary_sha256}" ]]; then
    printf 'Binary SHA256 mismatch for %s: expected=%s observed=%s\n' \
      "${binary_path}" "${expected_binary_sha256}" "${observed_binary_sha256}" >&2
    exit 1
  fi
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

NETMHCPAN_STATUS="manual_download_required"
if verify_local_archive "${NETMHCPAN_ARCHIVE}" "${NETMHCPAN_SHA256}"; then
  verify_licensed_package \
    "${NETMHCPAN_ROOT}" \
    "Linux_x86_64/bin/netMHCpan-4.2" \
    "${NETMHCPAN_BINARY_SHA256}" \
    "NetMHCpan version ${NETMHCPAN_VERSION}"
  mkdir -p "${INSTALL_ROOT}/tmp/netmhcpan"
  {
    printf '#!/usr/bin/env bash\n'
    printf 'set -euo pipefail\n'
    printf 'export NMHOME="%s"\n' "${NETMHCPAN_ROOT}"
    printf 'export TMPDIR="%s/tmp/netmhcpan"\n' "${INSTALL_ROOT}"
    printf 'export NETMHCpan="${NMHOME}/Linux_x86_64"\n'
    printf 'exec "${NETMHCpan}/bin/netMHCpan-4.2" "$@"\n'
  } > "${BIN_ROOT}/netMHCpan.partial"
  chmod 0755 "${BIN_ROOT}/netMHCpan.partial"
  mv "${BIN_ROOT}/netMHCpan.partial" "${BIN_ROOT}/netMHCpan"
  NETMHCPAN_STATUS="ready_in_place"
else
  rm -f "${BIN_ROOT}/netMHCpan"
  printf 'Licensed archive not found; NetMHCpan remains pending: %s\n' \
    "${NETMHCPAN_ARCHIVE}"
fi

NETMHCIIPAN_STATUS="manual_download_required"
if verify_local_archive "${NETMHCIIPAN_ARCHIVE}" "${NETMHCIIPAN_SHA256}"; then
  verify_licensed_package \
    "${NETMHCIIPAN_ROOT}" \
    "Linux_x86_64/bin/NetMHCIIpan-4.3" \
    "${NETMHCIIPAN_BINARY_SHA256}" \
    "NetMHCIIpan version ${NETMHCIIPAN_VERSION}"
  mkdir -p "${INSTALL_ROOT}/tmp/netmhciipan"
  {
    printf '#!/usr/bin/env bash\n'
    printf 'set -euo pipefail\n'
    printf 'export NMHOME="%s"\n' "${NETMHCIIPAN_ROOT}"
    printf 'export TMPDIR="%s/tmp/netmhciipan"\n' "${INSTALL_ROOT}"
    printf 'export NETMHCIIpan="${NMHOME}/Linux_x86_64"\n'
    printf 'exec "${NETMHCIIpan}/bin/NetMHCIIpan-4.3" "$@"\n'
  } > "${BIN_ROOT}/netMHCIIpan.partial"
  chmod 0755 "${BIN_ROOT}/netMHCIIpan.partial"
  mv "${BIN_ROOT}/netMHCIIpan.partial" "${BIN_ROOT}/netMHCIIpan"
  NETMHCIIPAN_STATUS="ready_in_place"
else
  rm -f "${BIN_ROOT}/netMHCIIpan"
  printf 'Licensed archive not found; NetMHCIIpan remains pending: %s\n' \
    "${NETMHCIIPAN_ARCHIVE}"
fi

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
  printf 'netmhcpan_version=%s\n' "${NETMHCPAN_VERSION}"
  printf 'netmhcpan_archive=%s\n' "${NETMHCPAN_ARCHIVE}"
  printf 'netmhcpan_archive_sha256=%s\n' "${NETMHCPAN_SHA256}"
  printf 'netmhcpan_package_root=%s\n' "${NETMHCPAN_ROOT}"
  printf 'netmhcpan_binary_sha256=%s\n' "${NETMHCPAN_BINARY_SHA256}"
  printf 'netmhcpan_status=%s\n' "${NETMHCPAN_STATUS}"
  printf 'netmhciipan_version=%s\n' "${NETMHCIIPAN_VERSION}"
  printf 'netmhciipan_archive=%s\n' "${NETMHCIIPAN_ARCHIVE}"
  printf 'netmhciipan_archive_sha256=%s\n' "${NETMHCIIPAN_SHA256}"
  printf 'netmhciipan_package_root=%s\n' "${NETMHCIIPAN_ROOT}"
  printf 'netmhciipan_binary_sha256=%s\n' "${NETMHCIIPAN_BINARY_SHA256}"
  printf 'netmhciipan_status=%s\n' "${NETMHCIIPAN_STATUS}"
  printf 'licensed_archives_redistributed=false\n'
} > "${manifest_path}"

printf 'Stage 4 CPU tools installed in %s\n' "${INSTALL_ROOT}"
printf 'Run the verifier: %s\n' \
  "/data00/home/wangzhi.wit/models/mRNABERT/design-flow/scripts/verify_stage4_cpu_tools.sh"
