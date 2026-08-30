#!/usr/bin/env bash
# Stop Jarvis Python services (and optionally the MQTT broker).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="$ROOT/.pids"

SERVICES=(brain tts stt mic-wakeword clipper)
SCRIPTS=(brain.py tts.py ptt_transcribe.py wakeword.py clipper.py)

kill_pid() {
  local pid="$1"
  local name="$2"
  if ! kill -0 "$pid" 2>/dev/null; then
    return 1
  fi
  echo "==> Stopping $name (pid $pid)..."
  kill "$pid" 2>/dev/null || true
  for _ in {1..10}; do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.2
  done
  kill -9 "$pid" 2>/dev/null || true
  return 0
}

stop_one() {
  local name="$1"
  local script="$2"
  local pidfile="$PID_DIR/$name.pid"
  local found=0

  if [[ -f "$pidfile" ]]; then
    local pid
    pid="$(cat "$pidfile")"
    if kill_pid "$pid" "$name"; then
      found=1
    else
      echo "==> $name already stopped (stale pid $pid)"
      found=1
    fi
    rm -f "$pidfile"
  fi

  # Also catch Terminal/"open" mode processes (no pidfile)
  local pid
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    local cmd
    cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    # Only kill processes clearly tied to this repo's service script
    if [[ "$cmd" == *"$ROOT/services/"* && "$cmd" == *"$script"* ]]; then
      kill_pid "$pid" "$name" && found=1
    elif [[ "$cmd" == *"/$script"* || "$cmd" == *" $script"* ]]; then
      # python -u script.py launched from the service directory
      local cwd
      cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
      if [[ "$cwd" == "$ROOT/services/"* ]]; then
        kill_pid "$pid" "$name" && found=1
      fi
    fi
  done < <(pgrep -f "$script" 2>/dev/null || true)

  if [[ "$found" -eq 0 ]]; then
    echo "==> No running process for $name"
  fi
}

i=0
for name in "${SERVICES[@]}"; do
  stop_one "$name" "${SCRIPTS[$i]}"
  i=$((i + 1))
done

for extra in ollama spotifyd librespot; do
  if [[ -f "$PID_DIR/$extra.pid" ]]; then
    pid="$(cat "$PID_DIR/$extra.pid")"
    if ! kill_pid "$pid" "$extra"; then
      echo "==> $extra already stopped (stale pid $pid)"
    fi
    rm -f "$PID_DIR/$extra.pid"
  fi
done

rm -f "$PID_DIR/services.pid"

if [[ "${1:-}" == "--all" ]] || [[ "${1:-}" == "-a" ]]; then
  stop_one "control" "server.py"
fi

if [[ "${1:-}" == "--broker" ]] || [[ "${1:-}" == "-b" ]] || [[ "${1:-}" == "--all" ]] || [[ "${1:-}" == "-a" ]]; then
  echo "==> Stopping MQTT broker..."
  if command -v docker >/dev/null 2>&1; then
    if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx 'jarvis-broker'; then
      docker stop jarvis-broker >/dev/null 2>&1 || true
      docker rm jarvis-broker >/dev/null 2>&1 || true
    fi
    (cd "$ROOT" && docker compose down 2>/dev/null || true)
  fi
else
  echo "Broker left running. Pass --broker to stop it too."
  echo "Phone panel left running. Pass --all to stop it too."
fi

echo "Done."
