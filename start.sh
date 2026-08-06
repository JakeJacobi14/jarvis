#!/usr/bin/env bash
# Start Jarvis: MQTT broker, Ollama, and all Python services.
#
# Usage:
#   ./start.sh            # open each service in its own Terminal window (Mac)
#   ./start.sh --bg       # old style: background + logs/*.log
#   ./start.sh --setup    # only create/update venvs, then exit
#   ./start.sh --help
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/logs"
PID_DIR="$ROOT/.pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

MODE="open"   # open | bg | setup
for arg in "$@"; do
  case "$arg" in
    --bg|-b) MODE="bg" ;;
    --setup|-s) MODE="setup" ;;
    --open|-o) MODE="open" ;;
    --help|-h)
      sed -n '2,10p' "$0"
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
    echo "ERROR: docker not found. Install Docker Desktop, then retry."
    exit 1
  fi
  # Wake Docker Desktop if the daemon isn't up yet
  if ! docker info >/dev/null 2>&1; then
    echo "    Docker daemon not ready — opening Docker Desktop..."
    open -a Docker 2>/dev/null || true
    for _ in {1..60}; do
      docker info >/dev/null 2>&1 && break
      sleep 1
    done
    if ! docker info >/dev/null 2>&1; then
      echo "ERROR: Docker did not become ready. Open Docker Desktop and retry."
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
  if [[ -d "/Applications/Ollama.app" ]]; then
    open -a Ollama
  elif command -v ollama >/dev/null 2>&1; then
    # Fall back to CLI serve in the background
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
  osascript >/dev/null <<EOF
tell application "Terminal"
  activate
  do script "cd '$workdir'; clear; echo '======================================'; echo '  Jarvis · $name'; echo '  $script'; echo '======================================'; echo; '$venv_python' -u '$script'; echo; echo '[$name exited — press Return to close]'; read"
end tell
EOF
  echo "==> Opened Terminal for $name"
}

# ── main ────────────────────────────────────────────────────────────

echo "==> Ensuring Python venvs..."
ensure_all_venvs

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
ensure_ollama

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
  echo "  Tail logs:   tail -f logs/*.log"
  echo "  Stop all:    ./stop.sh"
  echo "  Stop + broker: ./stop.sh --broker"
fi
