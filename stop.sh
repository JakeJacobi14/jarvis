#!/usr/bin/env bash
# Stop Jarvis Python services (and optionally the MQTT broker).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="$ROOT/.pids"

stop_one() {
  local name="$1"
  local pidfile="$PID_DIR/$name.pid"
  if [[ -f "$pidfile" ]]; then
    local pid
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      echo "==> Stopping $name (pid $pid)..."
      kill "$pid" 2>/dev/null || true
      # Give it a moment, then force if needed
      for _ in {1..10}; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.2
      done
      if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null || true
      fi
    else
      echo "==> $name already stopped (stale pid $pid)"
    fi
    rm -f "$pidfile"
  else
    echo "==> No pid file for $name"
  fi
}

stop_one brain
stop_one tts
stop_one stt
stop_one mic-wakeword
rm -f "$PID_DIR/services.pid"

if [[ "${1:-}" == "--broker" ]] || [[ "${1:-}" == "-b" ]]; then
  echo "==> Stopping MQTT broker..."
  if docker ps -a --format '{{.Names}}' | grep -qx 'jarvis-broker'; then
    docker stop jarvis-broker >/dev/null
    docker rm jarvis-broker >/dev/null 2>&1 || true
  fi
  (cd "$ROOT" && docker compose down 2>/dev/null || true)
else
  echo "Broker left running. Pass --broker to stop it too."
fi

echo "Done."
