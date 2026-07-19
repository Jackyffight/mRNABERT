#!/usr/bin/env bash
set -Eeuo pipefail
shopt -s nullglob

usage() {
  cat <<'EOF' >&2
Usage: restore_bundle.sh BUNDLE_DIR TARGET_PREFIX

TARGET_PREFIX=/ restores original absolute paths. Git repositories must first be
cloned at the commits recorded under metadata/environment/git. The restore never
overwrites a normal archive target and uses tar --keep-old-files for git-state.
EOF
}

if [[ $# -ne 2 ]]; then
  usage
  exit 2
fi

BUNDLE_DIR=$1
TARGET_PREFIX=$2
[[ "$BUNDLE_DIR" == /* && "$TARGET_PREFIX" == /* ]] || {
  echo "Bundle and target-prefix paths must be absolute." >&2
  exit 2
}
BUNDLE_DIR=$(realpath "$BUNDLE_DIR")
TARGET_PREFIX=$(realpath -m "$TARGET_PREFIX")
"$BUNDLE_DIR/tools/verify_bundle.sh" "$BUNDLE_DIR"

prefixed_path() {
  local original=$1
  if [[ "$TARGET_PREFIX" == / ]]; then
    printf '%s' "$original"
  else
    printf '%s%s' "${TARGET_PREFIX%/}" "$original"
  fi
}

# Validate every destination before writing the first byte.
while IFS=$'\t' read -r archive_id archive_kind source_path source_parent member_name _bytes git_head _rest; do
  [[ "$archive_id" == archive_id ]] && continue
  target_parent=$(prefixed_path "$source_parent")
  target_path="$target_parent/$member_name"
  if [[ "$archive_kind" == git-state ]]; then
    [[ -d "$target_path/.git" ]] || {
      echo "Clone the Git repository before restore: $target_path" >&2
      exit 1
    }
    actual_head=$(git -C "$target_path" rev-parse HEAD)
    [[ "$actual_head" == "$git_head" ]] || {
      echo "Git HEAD mismatch for $target_path: expected $git_head, got $actual_head" >&2
      exit 1
    }
  elif [[ -e "$target_path" || -L "$target_path" ]]; then
    echo "Refusing to overwrite existing restore target: $target_path" >&2
    exit 1
  fi
done <"$BUNDLE_DIR/archives.tsv"

while IFS=$'\t' read -r archive_id archive_kind _source source_parent member_name _bytes _head part_glob _rest; do
  [[ "$archive_id" == archive_id ]] && continue
  parts=("$BUNDLE_DIR"/${part_glob})
  target_parent=$(prefixed_path "$source_parent")
  mkdir -p "$target_parent"
  echo "Restoring $archive_id -> $target_parent/$member_name"
  if [[ "$archive_kind" == git-state ]]; then
    cat "${parts[@]}" \
      | zstd --decompress --quiet \
      | tar --extract --file=- --directory="$target_parent" \
        --preserve-permissions --numeric-owner --keep-old-files
  else
    cat "${parts[@]}" \
      | zstd --decompress --quiet \
      | tar --extract --file=- --directory="$target_parent" \
        --preserve-permissions --numeric-owner
    [[ -e "$target_parent/$member_name" || -L "$target_parent/$member_name" ]] || {
      echo "Restore did not produce expected target: $target_parent/$member_name" >&2
      exit 1
    }
  fi
done <"$BUNDLE_DIR/archives.tsv"

# Reconstruct tracked-but-uncommitted index and worktree changes after all
# untracked/ignored files are restored. Empty patches are intentionally skipped.
while IFS= read -r -d '' repository_metadata; do
  metadata_dir=$(dirname -- "$repository_metadata")
  original_repository=$(awk -F $'\t' '$1 == "repository_path" {print $2}' "$repository_metadata")
  target_repository=$(prefixed_path "$original_repository")
  index_patch="$metadata_dir/index.patch"
  worktree_patch="$metadata_dir/worktree.patch"
  if [[ -s "$index_patch" ]]; then
    echo "Applying staged Git patch: $target_repository"
    git -C "$target_repository" apply --index "$index_patch"
  fi
  if [[ -s "$worktree_patch" ]]; then
    echo "Applying unstaged Git patch: $target_repository"
    git -C "$target_repository" apply "$worktree_patch"
  fi
done < <(find "$BUNDLE_DIR/metadata/environment/git" -name repository.tsv -print0 2>/dev/null | sort -z)

echo "Restore complete under prefix: $TARGET_PREFIX"
