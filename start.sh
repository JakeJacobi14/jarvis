#!/usr/bin/env bash
# Start the MQTT broker + all Jarvis Python services.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/logs"
PID_DIR="$ROOT/.pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

if [[ -f "$PID_DIR/services.pid" ]]; then
  echo "Jarvis already looks running (found $PID_DIR/services.pid)."
  echo "Run ./stop.sh first, or delete that file if it's stale."
  exit 1
fi

broker_up() {
  (echo > /dev/tcp/127.0.0.1/1883) >/dev/null 2>&1
}

if broker_up; then
  echo "==> MQTT broker already running on :1883 — reusing it"
else
  echo "==> Starting MQTT broker (Docker)..."
  docker compose up -d

  # Wait briefly for Mosquitto to accept connections
  for _ in {1..20}; do
    if broker_up; then
      break
    fi
    sleep 0.25
  done

  if ! broker_up; then
    echo "ERROR: MQTT broker did not come up on :1883"
    exit 1
  fi
fi

start_service() {
  local name="$1"
  local dir="$2"
  local script="$3"
  local venv="$ROOT/services/$dir/.venv/bin/python"
  local workdir="$ROOT/services/$dir"
  local logfile="$LOG_DIR/$name.log"
  local pidfile="$PID_DIR/$name.pid"

  if [[ ! -x "$venv" ]]; then
    echo "ERROR: missing venv for $name at $venv"
    echo "Create it with: python3 -m venv services/$dir/.venv && services/$dir/.venv/bin/pip install -r services/$dir/requirements.txt"
    exit 1
  fi

  echo "==> Starting $name..."
  (
    cd "$workdir"
    # Unbuffered so logs show up immediately
    exec "$venv" -u "$script"
  ) >>"$logfile" 2>&1 &
  echo $! >"$pidfile"
  echo "    pid $(cat "$pidfile")  →  logs/$name.log"
}

start_service brain        brain         brain.py
start_service tts          tts           tts.py
start_service stt          stt           ptt_transcribe.py
start_service mic-wakeword mic-wakeword  wakeword.py

# Marker file so stop.sh knows we started the stack
date >"$PID_DIR/services.pid"

echo
echo "All services started."
echo "  Tail logs:   tail -f logs/*.log"
echo "  Stop all:    ./stop.sh"
echo
if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "NOTE: Ollama does not appear to be running on :11434."
  echo "      Brain needs it — start with: ollama serve"
fi
