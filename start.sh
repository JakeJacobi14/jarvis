#!/usr/bin/env bash
# Start Jarvis: MQTT broker, Ollama, and all Python services.
#
# Usage:
#   ./start.sh            # Mac: Terminal windows per service; Linux: background + logs
#   ./start.sh --open     # force Terminal windows (macOS only)
#   ./start.sh --bg       # background + logs/*.log
#   ./start.sh --setup    # only create/update venvs, then exit
#   ./start.sh --help
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/logs"
PID_DIR="$ROOT/.pids"
TIMING_LOG="$LOG_DIR/timing.log"
mkdir -p "$LOG_DIR" "$PID_DIR"
export JARVIS_TIMING_LOG="$TIMING_LOG"

# Load repo .env into this process (and children) without overriding existing exports.
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
  echo "==> Loaded .env"
fi

OS="$(uname -s)"
# Default: open Terminals on macOS, background everywhere else
if [[ "$OS" == "Darwin" ]]; then
  MODE="open"
else
  MODE="bg"
fi

for arg in "$@"; do
  case "$arg" in
    --bg|-b) MODE="bg" ;;
    --setup|-s) MODE="setup" ;;
    --open|-o) MODE="open" ;;
    --help|-h)
      sed -n '2,11p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg (try --help)"
      exit 1
      ;;
  esac
done

SERVICES=(
  "brain|brain|brain.py"
  "tts|tts|tts.py"
  "stt|stt|ptt_transcribe.py"
  "mic-wakeword|mic-wakeword|wakeword.py"
)

broker_up() {
  (echo > /dev/tcp/127.0.0.1/1883) >/dev/null 2>&1
}

ollama_up() {
  curl -sf http://localhost:11434/api/tags >/dev/null 2>&1
}

ensure_venv() {
  local dir="$1"
  local workdir="$ROOT/services/$dir"
  local venv="$workdir/.venv"
  local req="$workdir/requirements.txt"

  if [[ ! -d "$workdir" ]]; then
    echo "ERROR: missing service dir $workdir"
    exit 1
  fi

  if [[ ! -x "$venv/bin/python" ]]; then
    echo "==> Creating venv for $dir..."
    python3 -m venv "$venv"
  fi

  if [[ -f "$req" ]]; then
    # Cheap check: if requirements newer than a stamp, reinstall
    local stamp="$venv/.jarvis-reqs-stamp"
    if [[ ! -f "$stamp" ]] || [[ "$req" -nt "$stamp" ]]; then
      echo "==> Installing deps for $dir..."
      "$venv/bin/pip" install -q --upgrade pip
      "$venv/bin/pip" install -q -r "$req"
      touch "$stamp"
    fi
  fi
}

ensure_all_venvs() {
  local entry dir
  for entry in "${SERVICES[@]}"; do
    IFS='|' read -r _ dir _ <<<"$entry"
    ensure_venv "$dir"
  done
}

ensure_broker() {
  if broker_up; then
    echo "==> MQTT broker already running on :1883 — reusing it"
    return
  fi
  echo "==> Starting MQTT broker (Docker)..."
  if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker not found. Install Docker, then retry."
    exit 1
  fi
  # Wake Docker Desktop on macOS if the daemon isn't up yet
  if ! docker info >/dev/null 2>&1; then
    if [[ "$OS" == "Darwin" ]]; then
      echo "    Docker daemon not ready — opening Docker Desktop..."
      open -a Docker 2>/dev/null || true
    else
      echo "    Docker daemon not ready — start the Docker service, then retry."
    fi
    for _ in {1..60}; do
      docker info >/dev/null 2>&1 && break
      sleep 1
    done
    if ! docker info >/dev/null 2>&1; then
      echo "ERROR: Docker did not become ready."
      exit 1
    fi
  fi
  docker compose up -d
  for _ in {1..20}; do
    broker_up && break
    sleep 0.25
  done
  if ! broker_up; then
    echo "ERROR: MQTT broker did not come up on :1883"
    exit 1
  fi
}

ensure_ollama() {
  if ollama_up; then
    echo "==> Ollama already running on :11434"
    return
  fi
  echo "==> Starting Ollama..."
  if [[ "$OS" == "Darwin" ]] && [[ -d "/Applications/Ollama.app" ]]; then
    open -a Ollama
  elif command -v ollama >/dev/null 2>&1; then
    nohup ollama serve >>"$LOG_DIR/ollama.log" 2>&1 &
    echo $! >"$PID_DIR/ollama.pid"
  else
    echo "NOTE: Ollama not found. Brain will fail until you install/start it."
    return
  fi
  for _ in {1..30}; do
    ollama_up && break
    sleep 0.5
  done
  if ollama_up; then
    echo "    Ollama is up"
  else
    echo "NOTE: Ollama did not respond on :11434 yet — brain may need a moment."
  fi
}

ensure_llm() {
  local provider="${JARVIS_LLM_PROVIDER:-groq}"
  provider="$(echo "$provider" | tr '[:upper:]' '[:lower:]')"
  echo "==> LLM provider: $provider"
  if [[ "$provider" == "ollama" ]]; then
    ensure_ollama
    return
  fi
  if [[ "$provider" == "groq" ]]; then
    if [[ -z "${GROQ_API_KEY:-}" ]]; then
      echo "WARNING: GROQ_API_KEY is empty — set it in .env (see .env.example)"
    else
      echo "    Groq model: ${GROQ_MODEL:-openai/gpt-oss-20b}"
    fi
    return
  fi
  echo "WARNING: unknown JARVIS_LLM_PROVIDER=$provider (expected groq|ollama)"
}

ensure_tts_models() {
  local provider
  provider="$(echo "${JARVIS_TTS_PROVIDER:-cartesia}" | tr '[:upper:]' '[:lower:]')"
  echo "==> TTS provider: $provider"
  if [[ "$provider" == "cartesia" ]]; then
    if [[ -z "${CARTESIA_API_KEY:-}" ]]; then
      echo "WARNING: CARTESIA_API_KEY is empty — set it in .env (see .env.example)"
    else
      echo "    Cartesia model: ${CARTESIA_TTS_MODEL:-sonic-3.5}"
    fi
    return
  fi
  if [[ "$provider" != "kokoro" ]]; then
    echo "WARNING: unknown JARVIS_TTS_PROVIDER=$provider (expected cartesia|kokoro)"
  fi
  local model="$ROOT/services/tts/models/kokoro-v1.0.fp16.onnx"
  local voices="$ROOT/services/tts/models/voices-v1.0.bin"
  if [[ -f "$model" && -f "$voices" ]]; then
    return
  fi
  echo "==> TTS models missing — downloading..."
  bash "$ROOT/services/tts/download_models.sh"
}

ensure_stt() {
  local provider
  provider="$(echo "${JARVIS_STT_PROVIDER:-cartesia}" | tr '[:upper:]' '[:lower:]')"
  echo "==> STT provider: $provider"
  if [[ "$provider" == "cartesia" ]]; then
    if [[ -z "${CARTESIA_API_KEY:-}" ]]; then
      echo "WARNING: CARTESIA_API_KEY is empty — needed for Ink-2 STT"
    else
      echo "    Cartesia model: ${CARTESIA_STT_MODEL:-ink-2}"
    fi
  elif [[ "$provider" != "whisper" ]]; then
    echo "WARNING: unknown JARVIS_STT_PROVIDER=$provider (expected cartesia|whisper)"
  fi
}

start_service_bg() {
  local name="$1"
  local dir="$2"
  local script="$3"
  local venv="$ROOT/services/$dir/.venv/bin/python"
  local workdir="$ROOT/services/$dir"
  local logfile="$LOG_DIR/$name.log"
  local pidfile="$PID_DIR/$name.pid"

  echo "==> Starting $name (background)..."
  : >"$logfile"
  (
    cd "$workdir"
    export JARVIS_TIMING_LOG="$TIMING_LOG"
    exec "$venv" -u "$script"
  ) >>"$logfile" 2>&1 &
  echo $! >"$pidfile"
  echo "    pid $(cat "$pidfile")  →  logs/$name.log"
}

open_service_terminal() {
  local name="$1"
  local dir="$2"
  local script="$3"
  local workdir="$ROOT/services/$dir"
  local venv_python="$workdir/.venv/bin/python"
  # Paths in this repo have no spaces/quotes; keep the AppleScript simple.
  # Source .env inside the new Terminal so Groq keys reach brain.py.
  osascript >/dev/null <<EOF
tell application "Terminal"
  activate
  do script "cd '$workdir'; clear; set -a; [ -f '$ROOT/.env' ] && source '$ROOT/.env'; set +a; export JARVIS_TIMING_LOG='$TIMING_LOG'; echo '======================================'; echo '  Jarvis · $name'; echo '  $script'; echo '======================================'; echo; '$venv_python' -u '$script'; echo; echo '[$name exited — press Return to close]'; read"
end tell
EOF
  echo "==> Opened Terminal for $name"
}

watch_timing() {
  echo
  echo "══════════════════════════════════════"
  echo "  Timing feed  (all services)"
  echo "  Ctrl-C stops this view only — services keep running"
  echo "  Stop all: ./stop.sh"
  echo "══════════════════════════════════════"
  echo
  # -F follows even if the file is recreated
  tail -n 0 -F "$TIMING_LOG"
}

# ── main ────────────────────────────────────────────────────────────

echo "==> Ensuring Python venvs..."
ensure_all_venvs
ensure_tts_models

if [[ "$MODE" == "setup" ]]; then
  echo "Setup complete. Venvs are ready under services/*/.venv"
  exit 0
fi

if [[ -f "$PID_DIR/services.pid" ]] && [[ "$MODE" == "bg" ]]; then
  echo "Jarvis already looks running (found $PID_DIR/services.pid)."
  echo "Run ./stop.sh first, or delete that file if it's stale."
  exit 1
fi

ensure_broker
ensure_llm
ensure_stt

# Fresh timing session for this run
: >"$TIMING_LOG"

if [[ "$MODE" == "open" ]]; then
  if ! command -v osascript >/dev/null 2>&1; then
    echo "osascript not available — falling back to --bg"
    MODE="bg"
  fi
fi

entry=""
name=""
dir=""
script=""

if [[ "$MODE" == "open" ]]; then
  echo "==> Opening a Terminal window per service..."
  for entry in "${SERVICES[@]}"; do
    IFS='|' read -r name dir script <<<"$entry"
    open_service_terminal "$name" "$dir" "$script"
    sleep 0.35
  done
  echo "open" >"$PID_DIR/services.pid"
  echo
  echo "All Terminals opened."
  echo "  Close a window (or Ctrl-C in it) to stop that service."
  echo "  Stop all:  ./stop.sh"
  echo "  Stop + broker: ./stop.sh --broker"
else
  for entry in "${SERVICES[@]}"; do
    IFS='|' read -r name dir script <<<"$entry"
    start_service_bg "$name" "$dir" "$script"
  done
  date >"$PID_DIR/services.pid"
  echo
  echo "All services started in background."
  echo "  Full logs:   tail -f logs/*.log"
  echo "  Stop all:    ./stop.sh"
  echo "  Stop + broker: ./stop.sh --broker"
fi

watch_timing
