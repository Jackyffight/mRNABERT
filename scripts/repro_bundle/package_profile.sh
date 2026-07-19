#!/usr/bin/env bash
set -Eeuo pipefail
shopt -s nullglob

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

usage() {
  cat <<'EOF'
Usage:
  package_profile.sh PROFILE_TSV OUTPUT_DIR [--part-size SIZE] [--dry-run]

PROFILE_TSV columns (tab-separated):
  requirement  mode  label  absolute_path

requirement: required | optional
mode:
  entry                Archive one non-Git file or directory.
  git-state            Archive only untracked and meaningful ignored files.
  children-except-git  Archive every immediate child; direct Git children use
                       git-state automatically.

Committed Git objects and tracked files are never archived. Git remote, branch,
HEAD, dirty patches, and file lists are captured as metadata. Default parts: 16G.
EOF
}

if [[ $# -lt 2 ]]; then
  usage >&2
  exit 2
fi

PROFILE=$1
OUTPUT_DIR=$2
shift 2
PART_SIZE=16G
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --part-size)
      [[ $# -ge 2 ]] || { echo "--part-size requires a value" >&2; exit 2; }
      PART_SIZE=$2
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

for command in bash tar zstd split sha256sum du find sort realpath python3 numfmt stat git; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "Required command is unavailable: $command" >&2
    exit 1
  }
done

[[ "$PROFILE" == /* ]] || { echo "Profile path must be absolute: $PROFILE" >&2; exit 2; }
[[ "$OUTPUT_DIR" == /* ]] || { echo "Output path must be absolute: $OUTPUT_DIR" >&2; exit 2; }
[[ -f "$PROFILE" ]] || { echo "Profile not found: $PROFILE" >&2; exit 1; }

PROFILE=$(realpath "$PROFILE")
OUTPUT_DIR=$(realpath -m "$OUTPUT_DIR")
TMP_DIR=$(mktemp -d)
trap 'rm -rf -- "$TMP_DIR"' EXIT
mkdir -p "$TMP_DIR/filelists"
RESOLVED="$TMP_DIR/resolved-sources.tsv"
printf 'archive_id\tarchive_kind\tsource_path\tsource_parent\tmember_name\tsource_bytes\trequirement\tprofile_label\tgit_head\tfilelist\n' >"$RESOLVED"

declare -A ARCHIVE_IDS=()
TOTAL_BYTES=0

fail() {
  echo "vaxflow-repro: $*" >&2
  exit 1
}

slugify() {
  local value=$1
  local slug
  slug=$(printf '%s' "$value" | sed -E 's/[^A-Za-z0-9._-]+/_/g; s/^_+//; s/_+$//')
  [[ -n "$slug" ]] || slug=asset
  printf '%s' "${slug:0:96}"
}

reject_unsafe_field() {
  local name=$1
  local value=$2
  if [[ "$value" == *$'\t'* || "$value" == *$'\n'* ]]; then
    fail "$name contains a tab or newline, which is unsupported: $value"
  fi
}

output_is_inside() {
  local source=$1
  [[ "$OUTPUT_DIR" == "$source" || "$OUTPUT_DIR" == "$source/"* ]]
}

new_archive_id() {
  local label=$1
  local source=$2
  local path_hash
  path_hash=$(printf '%s' "$source" | sha256sum | cut -c1-12)
  NEW_ARCHIVE_ID="$(slugify "$label")-$path_hash"
  [[ -z "${ARCHIVE_IDS[$NEW_ARCHIVE_ID]:-}" ]] || fail "Duplicate archive id: $NEW_ARCHIVE_ID"
  ARCHIVE_IDS[$NEW_ARCHIVE_ID]=1
}

add_entry() {
  local requirement=$1
  local label=$2
  local source=$3
  local source_parent member source_bytes archive_id

  reject_unsafe_field source_path "$source"
  source=$(realpath -m "$source")
  [[ -e "$source" || -L "$source" ]] || fail "Resolved source does not exist: $source"
  output_is_inside "$source" && fail "Output directory cannot be inside a source: $source"
  if [[ -d "$source/.git" ]]; then
    fail "entry mode would duplicate committed Git files; use git-state: $source"
  fi

  source_parent=$(dirname -- "$source")
  member=$(basename -- "$source")
  source_bytes=$(du -sb -- "$source" | awk '{print $1}')
  new_archive_id "$label" "$source"
  archive_id=$NEW_ARCHIVE_ID
  TOTAL_BYTES=$((TOTAL_BYTES + source_bytes))
  printf '%s\tentry\t%s\t%s\t%s\t%s\t%s\t%s\t-\t-\n' \
    "$archive_id" "$source" "$source_parent" "$member" "$source_bytes" \
    "$requirement" "$label" >>"$RESOLVED"
}

git_file_is_disposable() {
  local relative=$1
  case "$relative" in
    __pycache__/*|*/__pycache__/*|*.pyc|*.pyo|*.pyd|.pytest_cache/*|*/.pytest_cache/*|.mypy_cache/*|*/.mypy_cache/*|.ruff_cache/*|*/.ruff_cache/*|*/build/*|build/*|*.egg-info/*|.git-credentials|*/.git-credentials|.netrc|*/.netrc|.huggingface/token|*/.huggingface/token|.huggingface/stored_tokens|*/.huggingface/stored_tokens|huggingface/token|*/huggingface/token|huggingface/stored_tokens|*/huggingface/stored_tokens)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

add_git_state() {
  local requirement=$1
  local label=$2
  local source=$3
  local source_parent member archive_id list_tmp list_rel git_head source_bytes

  source=$(realpath -m "$source")
  [[ -d "$source/.git" ]] || fail "git-state source is not a Git worktree: $source"
  output_is_inside "$source" && fail "Output directory cannot be inside a Git source: $source"
  source_parent=$(dirname -- "$source")
  member=$(basename -- "$source")
  new_archive_id "$label" "$source"
  archive_id=$NEW_ARCHIVE_ID
  list_tmp="$TMP_DIR/filelists/$archive_id.nul"
  list_rel="metadata/filelists/$archive_id.nul"
  git_head=$(git -C "$source" rev-parse HEAD)

  while IFS= read -r -d '' relative; do
    git_file_is_disposable "$relative" && continue
    printf '%s/%s\0' "$member" "$relative"
  done < <(
    {
      git -C "$source" ls-files --others --exclude-standard -z
      git -C "$source" ls-files --others --ignored --exclude-standard -z
    } | sort -zu
  ) >"$list_tmp"

  source_bytes=$(python3 - "$source_parent" "$list_tmp" <<'PY'
import os
import pathlib
import sys

parent = pathlib.Path(sys.argv[1])
items = pathlib.Path(sys.argv[2]).read_bytes().split(b"\0")
total = 0
for raw in items:
    if not raw:
        continue
    path = parent / os.fsdecode(raw)
    try:
        total += path.lstat().st_size
    except FileNotFoundError:
        raise SystemExit(f"Git-state file vanished during inventory: {path}")
print(total)
PY
  )
  TOTAL_BYTES=$((TOTAL_BYTES + source_bytes))
  printf '%s\tgit-state\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$archive_id" "$source" "$source_parent" "$member" "$source_bytes" \
    "$requirement" "$label" "$git_head" "$list_rel" >>"$RESOLVED"
}

while IFS=$'\t' read -r requirement mode label source_path extra; do
  [[ -z "$requirement" || "$requirement" == \#* ]] && continue
  [[ -z "${extra:-}" ]] || fail "Profile row has more than four fields: $label"
  reject_unsafe_field label "$label"
  case "$requirement" in required|optional) ;; *) fail "Invalid requirement: $requirement" ;; esac
  case "$mode" in entry|git-state|children-except-git) ;; *) fail "Invalid mode: $mode" ;; esac
  [[ "$source_path" == /* ]] || fail "Profile source path must be absolute: $source_path"
  source_path=$(realpath -m "$source_path")

  if [[ ! -e "$source_path" && ! -L "$source_path" ]]; then
    if [[ "$requirement" == required ]]; then
      fail "Required source is missing: $source_path"
    fi
    printf 'SKIP optional missing source: %s\n' "$source_path" >&2
    continue
  fi
  output_is_inside "$source_path" && fail "Output directory cannot be inside a profile root: $source_path"

  case "$mode" in
    entry)
      add_entry "$requirement" "$label" "$source_path"
      ;;
    git-state)
      add_git_state "$requirement" "$label" "$source_path"
      ;;
    children-except-git)
      [[ -d "$source_path" ]] || fail "children-except-git requires a directory: $source_path"
      child_count=0
      while IFS= read -r -d '' child; do
        child_label="$label--$(basename -- "$child")"
        if [[ -d "$child/.git" ]]; then
          add_git_state "$requirement" "$child_label" "$child"
        else
          add_entry "$requirement" "$child_label" "$child"
        fi
        child_count=$((child_count + 1))
      done < <(find "$source_path" -mindepth 1 -maxdepth 1 -print0 | sort -z)
      [[ $child_count -gt 0 ]] || fail "Required children root is empty: $source_path"
      ;;
  esac
done <"$PROFILE"

SOURCE_COUNT=$(($(wc -l <"$RESOLVED") - 1))
[[ $SOURCE_COUNT -gt 0 ]] || fail "Profile resolved to no sources"

printf 'Profile: %s\n' "$PROFILE"
printf 'Output: %s\n' "$OUTPUT_DIR"
printf 'Resolved archives: %s\n' "$SOURCE_COUNT"
printf 'Raw non-Git bytes: %s (%s)\n' "$TOTAL_BYTES" "$(numfmt --to=iec-i --suffix=B "$TOTAL_BYTES")"
column -t -s $'\t' "$RESOLVED" 2>/dev/null || cat "$RESOLVED"

if [[ $DRY_RUN -eq 1 ]]; then
  echo "Dry run complete; no bundle was written."
  exit 0
fi

[[ ! -e "$OUTPUT_DIR" ]] || fail "Output already exists; use a new directory: $OUTPUT_DIR"
mkdir -p "$(dirname -- "$OUTPUT_DIR")"
AVAILABLE_BYTES=$(df -PB1 "$(dirname -- "$OUTPUT_DIR")" | awk 'NR==2 {print $4}')
if [[ "$AVAILABLE_BYTES" =~ ^[0-9]+$ && $AVAILABLE_BYTES -lt $TOTAL_BYTES ]]; then
  printf 'WARNING: destination has %s free for %s raw non-Git input. Use a larger external disk if uncertain.\n' \
    "$(numfmt --to=iec-i --suffix=B "$AVAILABLE_BYTES")" \
    "$(numfmt --to=iec-i --suffix=B "$TOTAL_BYTES")" >&2
fi

umask 077
mkdir -p "$OUTPUT_DIR/archives" "$OUTPUT_DIR/logs" \
  "$OUTPUT_DIR/metadata/filelists" "$OUTPUT_DIR/tools"
cp "$PROFILE" "$OUTPUT_DIR/profile.tsv"
cp "$RESOLVED" "$OUTPUT_DIR/resolved-sources.tsv"
cp -a "$TMP_DIR/filelists/." "$OUTPUT_DIR/metadata/filelists/"
cp "$SCRIPT_DIR/capture_environment.sh" "$OUTPUT_DIR/tools/"
cp "$SCRIPT_DIR/package_profile.sh" "$OUTPUT_DIR/tools/"
cp "$SCRIPT_DIR/verify_bundle.sh" "$OUTPUT_DIR/tools/"
cp "$SCRIPT_DIR/restore_bundle.sh" "$OUTPUT_DIR/tools/"
chmod 700 "$OUTPUT_DIR/tools/"*.sh
printf 'building\n' >"$OUTPUT_DIR/STATE"

"$SCRIPT_DIR/capture_environment.sh" \
  "$OUTPUT_DIR/metadata/environment" "$OUTPUT_DIR/resolved-sources.tsv"

printf 'archive_id\tarchive_kind\tsource_path\tsource_parent\tmember_name\tsource_bytes\tgit_head\tpart_glob\tpart_count\tpacked_bytes\n' \
  >"$OUTPUT_DIR/archives.tsv"
: >"$OUTPUT_DIR/SHA256SUMS"

while IFS=$'\t' read -r archive_id archive_kind source_path source_parent member_name source_bytes _requirement _label git_head filelist; do
  [[ "$archive_id" == archive_id ]] && continue
  prefix="$OUTPUT_DIR/archives/$archive_id.tar.zst.part-"
  log="$OUTPUT_DIR/logs/$archive_id.log"
  printf '\n[%s] Packing %s state from %s (%s)\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$archive_kind" "$source_path" \
    "$(numfmt --to=iec-i --suffix=B "$source_bytes")"
  {
    printf 'started_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'source=%s\narchive_id=%s\narchive_kind=%s\npart_size=%s\n' \
      "$source_path" "$archive_id" "$archive_kind" "$PART_SIZE"
  } >"$log"

  set +e
  if [[ "$archive_kind" == git-state ]]; then
    tar --create --file=- --directory="$source_parent" --numeric-owner --sparse \
      --null --files-from="$OUTPUT_DIR/$filelist" 2>>"$log" \
      | zstd --threads=0 -1 --quiet 2>>"$log" \
      | split --bytes="$PART_SIZE" --numeric-suffixes=0 --suffix-length=4 - "$prefix" \
        2>>"$log"
  else
    tar --create --file=- --directory="$source_parent" --numeric-owner --sparse \
      --exclude-vcs --exclude='*/.git-credentials' --exclude='*/.netrc' \
      --exclude='*/.huggingface/token' --exclude='*/.huggingface/stored_tokens' \
      --exclude='*/huggingface/token' --exclude='*/huggingface/stored_tokens' \
      --exclude='huggingface/token' --exclude='huggingface/stored_tokens' \
      -- "$member_name" 2>>"$log" \
      | zstd --threads=0 -1 --quiet 2>>"$log" \
      | split --bytes="$PART_SIZE" --numeric-suffixes=0 --suffix-length=4 - "$prefix" \
        2>>"$log"
  fi
  pipeline_status=("${PIPESTATUS[@]}")
  set -e
  if [[ ${pipeline_status[0]} -ne 0 || ${pipeline_status[1]} -ne 0 || ${pipeline_status[2]} -ne 0 ]]; then
    printf 'FAILED tar=%s zstd=%s split=%s\n' "${pipeline_status[@]}" >>"$log"
    fail "Archive pipeline failed for $source_path; inspect $log"
  fi

  parts=("$prefix"*)
  [[ ${#parts[@]} -gt 0 ]] || fail "No archive parts produced for $source_path"
  packed_bytes=0
  for part in "${parts[@]}"; do
    part_bytes=$(stat -c '%s' "$part")
    packed_bytes=$((packed_bytes + part_bytes))
    (cd "$OUTPUT_DIR" && sha256sum "${part#$OUTPUT_DIR/}") >>"$OUTPUT_DIR/SHA256SUMS"
  done
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\tarchives/%s.tar.zst.part-*\t%s\t%s\n' \
    "$archive_id" "$archive_kind" "$source_path" "$source_parent" "$member_name" \
    "$source_bytes" "$git_head" "$archive_id" "${#parts[@]}" "$packed_bytes" \
    >>"$OUTPUT_DIR/archives.tsv"
  printf 'completed_at_utc=%s\npacked_bytes=%s\npart_count=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$packed_bytes" "${#parts[@]}" >>"$log"
done <"$RESOLVED"

python3 - "$OUTPUT_DIR" "$PROFILE" "$PART_SIZE" <<'PY'
import csv
import datetime as dt
import hashlib
import json
import pathlib
import socket
import sys

output = pathlib.Path(sys.argv[1])
profile = pathlib.Path(sys.argv[2])
with (output / "archives.tsv").open(encoding="utf-8", newline="") as handle:
    archives = list(csv.DictReader(handle, delimiter="\t"))
for row in archives:
    for key in ("source_bytes", "part_count", "packed_bytes"):
        row[key] = int(row[key])
payload = {
    "schema_version": 1,
    "scope": "non-git experiment state",
    "status": "complete",
    "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    "hostname": socket.gethostname(),
    "profile_source": str(profile),
    "profile_sha256": hashlib.sha256((output / "profile.tsv").read_bytes()).hexdigest(),
    "part_size": sys.argv[3],
    "archive_count": len(archives),
    "raw_source_bytes": sum(row["source_bytes"] for row in archives),
    "packed_bytes": sum(row["packed_bytes"] for row in archives),
    "archives": archives,
}
(output / "bundle.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY

cat >"$OUTPUT_DIR/README.txt" <<EOF
VaxFlow non-Git experiment-state bundle

This bundle contains environments, data, weights, runtime products, research
artifacts, and Git worktree state that is not committed. Committed Git files are
intentionally absent; clone the recorded commits before restoring git-state.

Verify:
  $OUTPUT_DIR/tools/verify_bundle.sh $OUTPUT_DIR

Deep verify (reads all tar streams):
  $OUTPUT_DIR/tools/verify_bundle.sh $OUTPUT_DIR --deep

Restore after cloning the recorded repositories at their exact commits:
  $OUTPUT_DIR/tools/restore_bundle.sh $OUTPUT_DIR /

Private archive: do not redistribute licensed NetMHC assets or supplied inputs.
EOF

printf 'complete\n' >"$OUTPUT_DIR/STATE"
(cd "$OUTPUT_DIR" && \
  find . -path './archives' -prune -o -type f \
    ! -name SHA256SUMS ! -name METADATA_SHA256SUMS -print0 \
    | sort -z | xargs -0 sha256sum) >"$OUTPUT_DIR/METADATA_SHA256SUMS"

printf '\nBundle complete: %s\n' "$OUTPUT_DIR"
printf 'Archive parts: %s\n' "$(find "$OUTPUT_DIR/archives" -type f | wc -l)"
printf 'Packed size: %s\n' "$(du -sh "$OUTPUT_DIR" | awk '{print $1}')"
printf 'Verify now:\n  %s/tools/verify_bundle.sh %s\n' "$OUTPUT_DIR" "$OUTPUT_DIR"
