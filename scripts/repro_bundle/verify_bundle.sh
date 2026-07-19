#!/usr/bin/env bash
set -Eeuo pipefail
shopt -s nullglob

usage() {
  echo "Usage: verify_bundle.sh BUNDLE_DIR [--deep]" >&2
}

if [[ $# -lt 1 || $# -gt 2 ]]; then
  usage
  exit 2
fi

BUNDLE_DIR=$1
DEEP=0
if [[ ${2:-} == --deep ]]; then
  DEEP=1
elif [[ -n ${2:-} ]]; then
  usage
  exit 2
fi

[[ "$BUNDLE_DIR" == /* ]] || { echo "Bundle path must be absolute." >&2; exit 2; }
BUNDLE_DIR=$(realpath "$BUNDLE_DIR")
[[ -f "$BUNDLE_DIR/STATE" && $(<"$BUNDLE_DIR/STATE") == complete ]] || {
  echo "Bundle is absent or incomplete: $BUNDLE_DIR" >&2
  exit 1
}
for file in SHA256SUMS METADATA_SHA256SUMS archives.tsv bundle.json; do
  [[ -f "$BUNDLE_DIR/$file" ]] || { echo "Missing bundle file: $file" >&2; exit 1; }
done

echo "Verifying archive-part checksums..."
(cd "$BUNDLE_DIR" && sha256sum -c SHA256SUMS)
echo "Verifying metadata checksums..."
(cd "$BUNDLE_DIR" && sha256sum -c METADATA_SHA256SUMS)

while IFS=$'\t' read -r archive_id _kind _source _parent _member _bytes _head part_glob expected_parts _packed; do
  [[ "$archive_id" == archive_id ]] && continue
  parts=("$BUNDLE_DIR"/${part_glob})
  [[ ${#parts[@]} -eq $expected_parts ]] || {
    echo "Part-count mismatch for $archive_id: expected $expected_parts, got ${#parts[@]}" >&2
    exit 1
  }
  echo "Testing zstd stream: $archive_id (${#parts[@]} parts)"
  cat "${parts[@]}" | zstd --test --quiet
  if [[ $DEEP -eq 1 ]]; then
    echo "Testing tar stream: $archive_id"
    cat "${parts[@]}" | zstd --decompress --quiet | tar --list --file=- >/dev/null
  fi
done <"$BUNDLE_DIR/archives.tsv"

echo "Bundle verification passed: $BUNDLE_DIR"
