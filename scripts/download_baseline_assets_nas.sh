#!/usr/bin/env bash
# Download pinned public mRNABERT assets and the smallest useful downstream sets.

set -euo pipefail

MODEL_REVISION="a1eb7df25804d23f08646e1cb996b234d7208a40"
MODEL_DIR="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_baselines/YYLY66-mRNABERT-$MODEL_REVISION"
DATA_DIR="/mnt/bn/neptune/mlx/users/wangzhi.wit/playground/models/mRNA/mrna_data/downstream/zenodo-17786045"
MODEL_BASE="https://huggingface.co/YYLY66/mRNABERT/resolve/$MODEL_REVISION"

mkdir -p "$MODEL_DIR" "$DATA_DIR"

download_file() {
  local URL="$1"
  local DESTINATION="$2"
  local EXPECTED_SIZE="${3:-}"
  local EXPECTED_DIGEST="${4:-}"
  local DIGEST_TYPE="${5:-}"

  verify_download() {
    local PATH_TO_VERIFY="$1"
    [ -s "$PATH_TO_VERIFY" ] || return 1
    if [ -n "$EXPECTED_SIZE" ] && [ "$(stat -c %s "$PATH_TO_VERIFY")" -ne "$EXPECTED_SIZE" ]; then
      return 1
    fi
    case "$DIGEST_TYPE" in
      "") return 0 ;;
      sha256) [ "$(sha256sum "$PATH_TO_VERIFY" | awk '{print $1}')" = "$EXPECTED_DIGEST" ] ;;
      md5) [ "$(md5sum "$PATH_TO_VERIFY" | awk '{print $1}')" = "$EXPECTED_DIGEST" ] ;;
      *) echo "Unsupported digest type: $DIGEST_TYPE" >&2; return 1 ;;
    esac
  }

  if verify_download "$DESTINATION"; then
    echo "ready: $DESTINATION"
    return
  fi

  # The Hugging Face Xet redirect can ignore Range on retry. Downloading into a
  # fresh temporary file prevents a complete response from being appended to a
  # partial checkpoint while still leaving the last verified file untouched.
  local TEMPORARY
  TEMPORARY=$(mktemp "${DESTINATION}.part.XXXXXX")
  if ! curl \
    -L \
    --fail \
    --show-error \
    --connect-timeout 30 \
    --speed-limit 65536 \
    --speed-time 60 \
    --retry 5 \
    --retry-delay 2 \
    --retry-all-errors \
    --output "$TEMPORARY" \
    "$URL"; then
    rm -f "$TEMPORARY"
    return 1
  fi
  if ! verify_download "$TEMPORARY"; then
    echo "Downloaded file failed size or checksum validation: $URL" >&2
    rm -f "$TEMPORARY"
    return 1
  fi
  mv -f "$TEMPORARY" "$DESTINATION"
}

MODEL_FILES=(
  README.md
  bert_layers.py
  bert_padding.py
  config.json
  configuration_bert.py
  flash_attn_triton.py
  generation_config.json
  pytorch_model.bin
  special_tokens_map.json
  tokenizer.json
  tokenizer_config.json
  vocab.txt
)

declare -A MODEL_FILE_SIZES=(
  [README.md]=3207
  [bert_layers.py]=40750
  [bert_padding.py]=6099
  [config.json]=949
  [configuration_bert.py]=993
  [flash_attn_triton.py]=42737
  [generation_config.json]=90
  [pytorch_model.bin]=455973118
  [special_tokens_map.json]=125
  [tokenizer.json]=4147
  [tokenizer_config.json]=579
  [vocab.txt]=297
)

if python -c "import huggingface_hub" >/dev/null 2>&1; then
  echo "Populate pinned model from the Hugging Face cache when available."
  if ! python - "$MODEL_DIR" "$MODEL_REVISION" <<'PY'
import sys

from huggingface_hub import snapshot_download

model_dir, revision = sys.argv[1:]
snapshot_download(
    repo_id="YYLY66/mRNABERT",
    revision=revision,
    local_dir=model_dir,
    local_dir_use_symlinks=False,
    local_files_only=True,
    allow_patterns=[
        "README.md",
        "bert_layers.py",
        "bert_padding.py",
        "config.json",
        "configuration_bert.py",
        "flash_attn_triton.py",
        "generation_config.json",
        "pytorch_model.bin",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.txt",
    ],
)
PY
  then
    echo "Hugging Face snapshot download failed; falling back to verified direct downloads." >&2
  fi
fi

for FILE in "${MODEL_FILES[@]}"; do
  echo "download model: $FILE"
  if [ "$FILE" = "pytorch_model.bin" ]; then
    download_file \
      "$MODEL_BASE/$FILE" \
      "$MODEL_DIR/$FILE" \
      "${MODEL_FILE_SIZES[$FILE]}" \
      cb2eb64831a494d4cac14acb5df908f734e088c4d62256ac3e42cada60c3bf75 \
      sha256
  else
    download_file "$MODEL_BASE/$FILE" "$MODEL_DIR/$FILE" "${MODEL_FILE_SIZES[$FILE]}"
  fi
done

MODEL_SIZE=$(stat -c %s "$MODEL_DIR/pytorch_model.bin")
if [ "$MODEL_SIZE" -ne 455973118 ]; then
  echo "Unexpected model weight size: $MODEL_SIZE (expected 455973118)" >&2
  exit 1
fi
echo "cb2eb64831a494d4cac14acb5df908f734e088c4d62256ac3e42cada60c3bf75  $MODEL_DIR/pytorch_model.bin" | sha256sum -c -

download_file \
  "https://zenodo.org/api/records/17786045/files/full_length.zip/content" \
  "$DATA_DIR/full_length.zip" \
  281158 \
  3652178c257341010800e2d241a9c258 \
  md5
download_file \
  "https://zenodo.org/api/records/17786045/files/te_ultra_full_length.zip/content" \
  "$DATA_DIR/te_ultra_full_length.zip" \
  32611876 \
  939b495793687db362d4b9464a5df570 \
  md5

echo "3652178c257341010800e2d241a9c258  $DATA_DIR/full_length.zip" | md5sum -c -
echo "939b495793687db362d4b9464a5df570  $DATA_DIR/te_ultra_full_length.zip" | md5sum -c -

mkdir -p "$DATA_DIR/full_length" "$DATA_DIR/te_ultra_full_length"
unzip -q -o "$DATA_DIR/full_length.zip" -d "$DATA_DIR/full_length"
unzip -q -o "$DATA_DIR/te_ultra_full_length.zip" -d "$DATA_DIR/te_ultra_full_length"

echo "model_dir: $MODEL_DIR"
echo "data_dir: $DATA_DIR"
