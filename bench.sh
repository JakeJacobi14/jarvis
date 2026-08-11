#!/usr/bin/env bash
# Benchmark LLM + TTS with fixed prompts (STT skipped via MQTT inject).
#
# Usage:
#   ./bench.sh                          # 5 prompts, label=baseline
#   ./bench.sh --label local
#   ./bench.sh --label api-llm --prompts 5
#   ./bench.sh --compare logs/benchmarks/A.json logs/benchmarks/B.json
#
# Requires broker + brain + TTS running (./start.sh). Mic/STT/wake optional.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PY="$ROOT/services/brain/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "ERROR: brain venv missing. Run ./start.sh --setup first."
  exit 1
fi

# Quick readiness checks (non-fatal warnings for compare-only)
if [[ "${1:-}" != "--compare" ]]; then
  if ! (echo > /dev/tcp/127.0.0.1/1883) >/dev/null 2>&1; then
    echo "ERROR: MQTT broker not up on :1883 — run ./start.sh first"
    exit 1
  fi
fi

exec "$PY" -u "$ROOT/services/benchmark/bench.py" "$@"
