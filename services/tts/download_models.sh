#!/usr/bin/env bash
# Download Kokoro ONNX model + voices (not stored in git — too large).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
MODEL_DIR="$ROOT/models"
mkdir -p "$MODEL_DIR"

BASE_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
MODEL="$MODEL_DIR/kokoro-v1.0.fp16.onnx"
VOICES="$MODEL_DIR/voices-v1.0.bin"

download() {
  local url="$1"
  local out="$2"
  if [[ -f "$out" ]]; then
    echo "==> already have $(basename "$out")"
    return
  fi
  echo "==> downloading $(basename "$out")..."
  curl -fL --progress-bar -o "$out" "$url"
}

download "$BASE_URL/kokoro-v1.0.fp16.onnx" "$MODEL"
download "$BASE_URL/voices-v1.0.bin" "$VOICES"
echo "TTS models ready in $MODEL_DIR"
