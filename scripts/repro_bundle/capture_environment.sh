#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: capture_environment.sh OUTPUT_DIR RESOLVED_SOURCES_TSV

Capture a credential-free host, CUDA, Python, filesystem, and Git snapshot.
EOF
}

if [[ $# -ne 2 ]]; then
  usage >&2
  exit 2
fi

OUTPUT_DIR=$1
RESOLVED_SOURCES=$2
if [[ "$OUTPUT_DIR" != /* || "$RESOLVED_SOURCES" != /* ]]; then
  echo "Output and inventory paths must be absolute." >&2
  exit 2
fi
if [[ ! -f "$RESOLVED_SOURCES" ]]; then
  echo "Resolved source inventory not found: $RESOLVED_SOURCES" >&2
  exit 2
fi

mkdir -p "$OUTPUT_DIR/system" "$OUTPUT_DIR/python" "$OUTPUT_DIR/git" "$OUTPUT_DIR/storage"

capture() {
  local output=$1
  shift
  {
    printf '# command:'
    printf ' %q' "$@"
    printf '\n'
    "$@"
  } >"$output" 2>&1 || true
}

capture_shell() {
  local output=$1
  local command=$2
  {
    printf '# command: %s\n' "$command"
    bash -lc "$command"
  } >"$output" 2>&1 || true
}

capture_sanitized_urls() {
  local output=$1
  shift
  local temporary
  temporary=$(mktemp)
  capture "$temporary" "$@"
  sed -E \
    -e 's#(https?|ftp)://[^/@[:space:]]+@#\1://<redacted>@#g' \
    -e 's#([?&](token|access_token|password)=)[^&[:space:]]+#\1<redacted>#gI' \
    "$temporary" >"$output"
  rm -f -- "$temporary"
}

sanitize_url_text() {
  sed -E \
    -e 's#(https?|ftp)://[^/@[:space:]]+@#\1://<redacted>@#g' \
    -e 's#([?&](token|access_token|password)=)[^&[:space:]]+#\1<redacted>#gI'
}

slug_with_hash() {
  local value=$1
  local slug digest
  slug=$(printf '%s' "$value" | sed -E 's/[^A-Za-z0-9._-]+/_/g; s/^_+//; s/_+$//')
  [[ -n "$slug" ]] || slug=asset
  digest=$(printf '%s' "$value" | sha256sum | cut -c1-12)
  printf '%s-%s' "${slug:0:80}" "$digest"
}

{
  printf 'captured_at_utc\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'hostname\t%s\n' "$(hostname 2>/dev/null || true)"
  printf 'fqdn\t%s\n' "$(hostname -f 2>/dev/null || true)"
  printf 'user\t%s\n' "$(id -un)"
  printf 'uid\t%s\n' "$(id -u)"
  printf 'gid\t%s\n' "$(id -g)"
  printf 'working_directory\t%s\n' "$(pwd)"
} >"$OUTPUT_DIR/host.tsv"

capture "$OUTPUT_DIR/system/uname.txt" uname -a
capture "$OUTPUT_DIR/system/id.txt" id
capture "$OUTPUT_DIR/system/ulimit.txt" bash -lc 'ulimit -a'
capture "$OUTPUT_DIR/system/lscpu.txt" lscpu
capture "$OUTPUT_DIR/system/free.txt" free -h
capture "$OUTPUT_DIR/system/lsblk.txt" lsblk -f
capture "$OUTPUT_DIR/system/df.txt" df -hT
capture "$OUTPUT_DIR/system/mount.txt" mount
capture "$OUTPUT_DIR/system/findmnt.txt" findmnt
capture "$OUTPUT_DIR/system/os-release.txt" cat /etc/os-release
capture "$OUTPUT_DIR/system/gcc.txt" gcc --version
capture "$OUTPUT_DIR/system/gxx.txt" g++ --version
capture "$OUTPUT_DIR/system/ldconfig.txt" ldconfig -p
capture "$OUTPUT_DIR/system/nvidia-smi.txt" nvidia-smi -q
capture "$OUTPUT_DIR/system/nvidia-topology.txt" nvidia-smi topo -m
capture "$OUTPUT_DIR/system/nvcc.txt" nvcc --version
capture "$OUTPUT_DIR/system/cuda-version-json.txt" cat /usr/local/cuda/version.json
capture_shell "$OUTPUT_DIR/system/dpkg-packages.tsv" "dpkg-query -W -f='\${binary:Package}\t\${Version}\n' | sort"
capture_shell "$OUTPUT_DIR/system/rpm-packages.txt" 'rpm -qa | sort'

# Never capture the full process environment. This whitelist excludes tokens,
# credentials, proxy secrets, and shell history by construction.
{
  for name in PATH LD_LIBRARY_PATH LIBRARY_PATH CPATH CUDA_HOME CUDA_PATH \
    CUDA_VISIBLE_DEVICES PYTHONPATH NCCL_DEBUG NCCL_SOCKET_IFNAME \
    OMP_NUM_THREADS MKL_NUM_THREADS TOKENIZERS_PARALLELISM; do
    if [[ -v "$name" ]]; then
      printf '%s\t%s\n' "$name" "${!name}"
    fi
  done
} >"$OUTPUT_DIR/system/environment-whitelist.tsv"

{
  printf 'archive_id\tsource_path\tfilesystem\tmountpoint\toptions\n'
  tail -n +2 "$RESOLVED_SOURCES" | while IFS=$'\t' read -r archive_id _kind source_path _rest; do
    filesystem=$(findmnt -T "$source_path" -n -o FSTYPE 2>/dev/null || true)
    mountpoint=$(findmnt -T "$source_path" -n -o TARGET 2>/dev/null || true)
    options=$(findmnt -T "$source_path" -n -o OPTIONS 2>/dev/null || true)
    printf '%s\t%s\t%s\t%s\t%s\n' \
      "$archive_id" "$source_path" "$filesystem" "$mountpoint" "$options"
  done
} >"$OUTPUT_DIR/storage/source-mounts.tsv"

declare -A SEEN_PYTHONS=()

capture_python() {
  local python_path=$1
  local resolved key output_prefix
  resolved=$(readlink -f "$python_path" 2>/dev/null || printf '%s' "$python_path")
  [[ -x "$resolved" ]] || return 0
  [[ -z "${SEEN_PYTHONS[$resolved]:-}" ]] || return 0
  SEEN_PYTHONS[$resolved]=1
  key=$(slug_with_hash "$python_path")
  output_prefix="$OUTPUT_DIR/python/$key"

  {
    printf 'requested_path\t%s\n' "$python_path"
    printf 'resolved_path\t%s\n' "$resolved"
    "$resolved" - <<'PY'
import json
import platform
import site
import sys

payload = {
    "executable": sys.executable,
    "prefix": sys.prefix,
    "base_prefix": sys.base_prefix,
    "version": sys.version,
    "platform": platform.platform(),
    "site_packages": site.getsitepackages() if hasattr(site, "getsitepackages") else [],
}
try:
    import torch
except Exception as exc:
    payload["torch_import_error"] = repr(exc)
else:
    payload["torch"] = {
        "version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
        "cudnn_version": torch.backends.cudnn.version(),
        "device_count": torch.cuda.device_count(),
    }
print(json.dumps(payload, indent=2, sort_keys=True))
PY
  } >"$output_prefix.info.txt" 2>&1 || true
  capture_sanitized_urls "$output_prefix.pip-freeze.txt" "$resolved" -m pip freeze --all
  capture "$output_prefix.pip-list.json" "$resolved" -m pip list --format=json
}

for python_path in "$(command -v python3 2>/dev/null || true)" \
  /usr/bin/python3 /usr/local/bin/python3; do
  [[ -n "$python_path" ]] && capture_python "$python_path"
done

declare -A SEEN_REPOS=()
while IFS=$'\t' read -r _archive_id kind source_path _rest; do
  [[ "$kind" == archive_kind ]] && continue

  if [[ "$kind" == git-state && -d "$source_path/.git" ]]; then
    if [[ -z "${SEEN_REPOS[$source_path]:-}" ]]; then
      SEEN_REPOS[$source_path]=1
      repo_key=$(slug_with_hash "$source_path")
      repo_output="$OUTPUT_DIR/git/$repo_key"
      mkdir -p "$repo_output"
      capture "$repo_output/status.txt" git -C "$source_path" status --short --branch
      capture "$repo_output/head.txt" git -C "$source_path" show -s --format=fuller HEAD
      capture_sanitized_urls "$repo_output/remotes.txt" git -C "$source_path" remote -v
      capture "$repo_output/submodules.txt" git -C "$source_path" submodule status --recursive
      capture "$repo_output/untracked-files.txt" git -C "$source_path" ls-files --others --exclude-standard
      capture "$repo_output/ignored-files.txt" git -C "$source_path" ls-files --others --ignored --exclude-standard
      git -C "$source_path" diff --binary >"$repo_output/worktree.patch" \
        2>"$repo_output/worktree.patch.stderr" || true
      git -C "$source_path" diff --cached --binary >"$repo_output/index.patch" \
        2>"$repo_output/index.patch.stderr" || true
      {
        printf 'repository_path\t%s\n' "$source_path"
        printf 'head\t%s\n' "$(git -C "$source_path" rev-parse HEAD 2>/dev/null || true)"
        printf 'branch\t%s\n' "$(git -C "$source_path" branch --show-current 2>/dev/null || true)"
        printf 'origin\t%s\n' "$({ git -C "$source_path" remote get-url origin 2>/dev/null || true; } | sanitize_url_text)"
      } >"$repo_output/repository.tsv"
    fi
  fi

  case "$source_path" in
    */datasets|*/datasets/*|*/mrna_data|*/mrna_data/*|*/runs|*/runs/*|*/input|*/input/*|*/output|*/output/*)
      continue
      ;;
  esac
  if [[ -d "$source_path" ]]; then
    while IFS= read -r -d '' python_path; do
      capture_python "$python_path"
    done < <(find "$source_path" -maxdepth 6 \( -type f -o -type l \) \
      -path '*/bin/python' -print0 2>/dev/null | sort -z)
  fi
done <"$RESOLVED_SOURCES"
