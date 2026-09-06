#!/usr/bin/env bash
# Start Jarvis: MQTT broker, Ollama, and all Python services.
#
# Usage:
#   ./start.sh            # Mac: Terminal windows per service; Linux: background + logs
#   ./start.sh --open     # force Terminal windows (macOS only)
#   ./start.sh --bg       # background + logs/*.log (stays attached to timing feed)
#   ./start.sh --detach   # background voice + phone panel, then exit (headless / systemd)
#   ./start.sh --control  # phone panel only (no voice)
#   ./start.sh --setup    # only create/update venvs, then exit
#   ./start.sh --spotify-login  # one-time OAuth for the built-in spotifyd speaker
#   ./start.sh --help
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/logs"
PID_DIR="$ROOT/.pids"
TIMING_LOG="$LOG_DIR/timing.log"
mkdir -p "$LOG_DIR" "$PID_DIR"
export JARVIS_TIMING_LOG="$TIMING_LOG"

# Load KEY=VALUE from .env without `source` (commas/spaces in values must not run as commands).
load_dotenv() {
  local path="$1" raw line key val
  [[ -f "$path" ]] || return 0
  while IFS= read -r raw || [[ -n "$raw" ]]; do
    line="${raw%$'\r'}"
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"
    val="${line#*=}"
    key="${key%"${key##*[![:space:]]}"}"
    key="${key#"${key%%[![:space:]]*}"}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    val="${val#"${val%%[![:space:]]*}"}"
    val="${val%"${val##*[![:space:]]}"}"
    if [[ ${#val} -ge 2 ]]; then
      if [[ "$val" == \"*\" ]]; then
        val="${val:1:${#val}-2}"
      elif [[ "$val" == \'*\' ]]; then
        val="${val:1:${#val}-2}"
      fi
    fi
    if [[ -z "${!key+x}" ]]; then
      export "$key=$val"
    fi
  done < "$path"
}

if [[ -f "$ROOT/.env" ]]; then
  load_dotenv "$ROOT/.env"
  echo "==> Loaded .env"
fi

# PipeWire/Pulse are invisible over SSH unless the user runtime dir is set.
if [[ -z "${XDG_RUNTIME_DIR:-}" ]]; then
  _rt="/run/user/$(id -u)"
  if [[ -d "$_rt" ]]; then
    export XDG_RUNTIME_DIR="$_rt"
  fi
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
    --detach|-d) MODE="detach" ;;
    --control|-c) MODE="control" ;;
    --setup|-s) MODE="setup" ;;
    --open|-o) MODE="open" ;;
    --spotify-login) MODE="spotify-login" ;;
    --help|-h)
      sed -n '2,12p' "$0"
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
      if [[ "$dir" == "mic-wakeword" ]]; then
        # openwakeword lists tflite-runtime on Linux; no wheel for Python 3.12.
        # We run ONNX only (wakeword.py sets inference_framework="onnx").
        "$venv/bin/pip" install -q sounddevice numpy paho-mqtt onnxruntime
        "$venv/bin/pip" install -q --no-deps "openwakeword>=0.6.0"
      else
        "$venv/bin/pip" install -q -r "$req"
      fi
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
  ensure_venv "control"
  if [[ "${JARVIS_CLIP_ENABLED:-1}" != "0" ]]; then
    ensure_venv "clipper"
  fi
}

pid_alive() {
  local name="$1"
  local pidfile="$PID_DIR/$name.pid"
  local pid
  [[ -f "$pidfile" ]] || return 1
  pid="$(cat "$pidfile")"
  kill -0 "$pid" 2>/dev/null
}

lan_ip() {
  if [[ "$OS" == "Darwin" ]]; then
    ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "localhost"
  else
    hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost"
  fi
}

print_control_url() {
  local port="${JARVIS_CONTROL_PORT:-8740}"
  local scheme="http"
  case "$(echo "${JARVIS_CONTROL_TLS:-1}" | tr '[:upper:]' '[:lower:]')" in
    0|off|no|false) scheme="http" ;;
    *) scheme="https" ;;
  esac
  echo
  echo "Phone panel:  $scheme://$(lan_ip):$port"
  echo "Avatar:       $scheme://$(lan_ip):$port/avatar"
  echo "Add that URL to your phone home screen for a full-screen app."
  if [[ "$scheme" == "https" ]]; then
    echo "The cert is self-signed — accept the browser warning once, then the"
    echo "hold-to-speak mic will work. http:// on the same port redirects here."
  fi
}

ensure_control() {
  if [[ "${JARVIS_SKIP_CONTROL:-}" == "1" ]]; then
    return
  fi
  ensure_venv "control"
  if pid_alive "control"; then
    echo "==> Control panel already running"
    print_control_url
    return
  fi
  start_service_bg "control" "control" "server.py"
  print_control_url
}

pin_linux_mic() {
  # Keep the USB webcam as Pulse default so wake/STT/clipper share it.
  [[ "$OS" == "Linux" ]] || return 0
  command -v pactl >/dev/null 2>&1 || return 0
  local src="${JARVIS_PULSE_SOURCE:-${JARVIS_CLIP_AUDIO_DEVICE:-}}"
  if [[ "$src" == alsa_input.* ]]; then
    pactl set-default-source "$src" >/dev/null 2>&1 || true
  fi
}

ensure_sonos_http() {
  # Linux UFW blocks host-bound 8742 (Sonos must fetch TTS WAVs). Docker
  # published ports punch that hole. macOS does not need this.
  [[ "$OS" == "Linux" ]] || return 0
  command -v docker >/dev/null 2>&1 || {
    echo "NOTE: docker not found — Sonos speech may be blocked by the firewall (port 8742)"
    return 0
  }
  local dir="${JARVIS_SONOS_HTTP_DIR:-$ROOT/.ttsbuf}"
  local port="${JARVIS_SONOS_HTTP_PORT:-8742}"
  mkdir -p "$dir"
  export JARVIS_SONOS_HTTP_DIR="$dir"
  export JARVIS_SONOS_HTTP_EXTERNAL=1
  export JARVIS_SONOS_HTTP_HOST="${JARVIS_SONOS_HTTP_HOST:-$(lan_ip)}"
  docker rm -f jarvis-port-probe >/dev/null 2>&1 || true
  if docker inspect -f '{{.State.Running}}' jarvis-sonos-http 2>/dev/null | grep -qx true; then
    echo "==> Sonos TTS HTTP already published on :$port"
    return 0
  fi
  docker rm -f jarvis-sonos-http >/dev/null 2>&1 || true
  if docker run -d --name jarvis-sonos-http --restart unless-stopped \
      -p "${port}:80" \
      -v "$dir:/usr/share/nginx/html:ro" \
      nginx:alpine >/dev/null; then
    echo "==> Sonos TTS HTTP published on :$port (UFW bypass)"
  else
    echo "WARNING: could not publish Sonos TTS HTTP on :$port"
    unset JARVIS_SONOS_HTTP_EXTERNAL
  fi
}

ensure_clipper() {
  if [[ "${JARVIS_CLIP_ENABLED:-1}" == "0" ]]; then
    echo "==> Clip recorder disabled (JARVIS_CLIP_ENABLED=0)"
    return
  fi
  if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "NOTE: ffmpeg not found — \"clip that\" is unavailable (brew install ffmpeg)"
    return
  fi
  ensure_venv "clipper"
  start_service_bg "clipper" "clipper" "clipper.py"
  echo "    rolling ${JARVIS_CLIP_MAX_SECONDS:-90}s A/V buffer → Clips/ on \"clip that\""
}

# ── headless Spotify Connect device (spotifyd) ──────────────────────
# The Web API can only steer an existing Spotify Connect device, so we
# download spotifyd into the repo and run it as a local speaker named
# JARVIS_SPOTIFY_DEVICE. Without it you'd have to leave the Spotify app open.
SPOTIFY_DIR="$ROOT/services/spotify"
SPOTIFYD_BIN="$SPOTIFY_DIR/bin/spotifyd"
SPOTIFYD_CONF="$SPOTIFY_DIR/spotifyd.conf"
SPOTIFYD_CACHE="$SPOTIFY_DIR/cache"
SPOTIFYD_VERSION="${JARVIS_SPOTIFYD_VERSION:-0.4.2}"

spotifyd_name() {
  echo "${JARVIS_SPOTIFY_DEVICE:-Jarvis}"
}

spotifyd_logged_in() {
  [[ -f "$SPOTIFYD_CACHE/oauth/credentials.json" ]]
}

write_spotifyd_conf() {
  local bitrate name
  name="$(spotifyd_name)"
  bitrate="${JARVIS_SPOTIFY_BITRATE:-160}"
  case "$bitrate" in
    96|160|320) ;;
    *) bitrate="160" ;;
  esac
  mkdir -p "$SPOTIFY_DIR" "$SPOTIFYD_CACHE"
  {
    echo "[global]"
    echo "device_name = \"${name}\""
    echo "device_type = \"computer\""
    echo "bitrate = ${bitrate}"
    echo "cache_path = \"${SPOTIFYD_CACHE}\""
    echo "no_audio_cache = true"
    if spotifyd_logged_in; then
      # Stay attached to this account; a phone picking the speaker would
      # otherwise steal the session and Web API playback would go quiet.
      echo "disable_discovery = true"
    fi
  } >"$SPOTIFYD_CONF"
}

spotifyd_asset() {
  local os arch variant
  case "$OS" in
    Darwin) os="macos" ;;
    Linux) os="linux" ;;
    *)
      echo ""
      return 1
      ;;
  esac
  case "$(uname -m)" in
    arm64|aarch64) arch="aarch64" ;;
    x86_64|amd64) arch="x86_64" ;;
    armv7l|armv7) arch="armv7" ;;
    *)
      echo ""
      return 1
      ;;
  esac
  # macOS "default" is linked against Homebrew dbus. "slim" only needs
  # PortAudio (CoreAudio) and actually runs on a stock Mac.
  # Linux "full" covers Pulse/ALSA on a headless box.
  if [[ "$os" == "macos" ]]; then
    variant="slim"
  else
    variant="full"
  fi
  echo "spotifyd-${os}-${arch}-${variant}.tar.gz"
}

spotifyd_ok() {
  [[ -x "$SPOTIFYD_BIN" ]] || return 1
  "$SPOTIFYD_BIN" --version >/dev/null 2>&1
}

fix_spotifyd_libs() {
  # Official macOS builds are linked against Homebrew dylibs. Install any
  # that are missing so the downloaded binary can actually start.
  command -v brew >/dev/null 2>&1 || return 1
  local err formula
  err="$("$SPOTIFYD_BIN" --version 2>&1 || true)"
  [[ "$err" == *"Library not loaded"* ]] || return 1
  echo "==> spotifyd is missing Homebrew libraries — installing..."
  while read -r formula; do
    [[ -n "$formula" ]] || continue
    echo "    brew install $formula"
    brew install "$formula" >/dev/null || true
  done < <(echo "$err" | grep -oE '/opt/homebrew/opt/[^/]+' | sed 's|.*/||' | sort -u)
  spotifyd_ok
}

fetch_spotifyd_tarball() {
  local asset url tmp archive found
  asset="$(spotifyd_asset || true)"
  if [[ -z "$asset" ]]; then
    echo "NOTE: no spotifyd binary for $OS/$(uname -m)"
    return 1
  fi
  if ! command -v curl >/dev/null 2>&1; then
    echo "NOTE: curl not found — cannot download spotifyd"
    return 1
  fi
  url="https://github.com/Spotifyd/spotifyd/releases/download/v${SPOTIFYD_VERSION}/${asset}"
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-spotifyd.XXXXXX")"
  archive="$tmp/$asset"
  echo "==> Downloading spotifyd v${SPOTIFYD_VERSION} ($asset)..."
  if ! curl -fsSL --retry 3 --retry-delay 1 -o "$archive" "$url"; then
    echo "NOTE: spotifyd download failed ($url)"
    rm -rf "$tmp"
    return 1
  fi
  if ! tar -xzf "$archive" -C "$tmp"; then
    echo "NOTE: could not extract spotifyd archive"
    rm -rf "$tmp"
    return 1
  fi
  found="$(find "$tmp" -type f -name spotifyd -print -quit)"
  if [[ -z "$found" ]]; then
    echo "NOTE: spotifyd binary missing from archive"
    rm -rf "$tmp"
    return 1
  fi
  mkdir -p "$(dirname "$SPOTIFYD_BIN")"
  rm -f "$SPOTIFYD_BIN"
  mv "$found" "$SPOTIFYD_BIN"
  chmod +x "$SPOTIFYD_BIN"
  if [[ "$OS" == "Darwin" ]]; then
    xattr -d com.apple.quarantine "$SPOTIFYD_BIN" 2>/dev/null || true
  fi
  rm -rf "$tmp"
  echo "    installed $SPOTIFYD_BIN"
  return 0
}

link_system_spotifyd() {
  local src=""
  if command -v spotifyd >/dev/null 2>&1; then
    src="$(command -v spotifyd)"
  elif command -v brew >/dev/null 2>&1 && [[ -x "$(brew --prefix 2>/dev/null)/bin/spotifyd" ]]; then
    src="$(brew --prefix)/bin/spotifyd"
  fi
  [[ -n "$src" && -x "$src" ]] || return 1
  mkdir -p "$(dirname "$SPOTIFYD_BIN")"
  ln -sfn "$src" "$SPOTIFYD_BIN"
  echo "==> Using system spotifyd at $src"
  spotifyd_ok
}

download_spotifyd() {
  if spotifyd_ok; then
    return 0
  fi
  # Stale/broken previous download — try to satisfy its dylibs first.
  if [[ -x "$SPOTIFYD_BIN" ]] && ! [[ -L "$SPOTIFYD_BIN" ]]; then
    if fix_spotifyd_libs; then
      return 0
    fi
    echo "==> replacing unusable spotifyd binary..."
    rm -f "$SPOTIFYD_BIN"
  fi
  if link_system_spotifyd; then
    return 0
  fi
  if fetch_spotifyd_tarball; then
    if spotifyd_ok || fix_spotifyd_libs; then
      return 0
    fi
    echo "NOTE: downloaded spotifyd would not start"
  fi
  if command -v brew >/dev/null 2>&1; then
    echo "==> brew install spotifyd (fallback)..."
    if brew install spotifyd >/dev/null; then
      link_system_spotifyd && return 0
    fi
  fi
  return 1
}

spotify_login() {
  if ! download_spotifyd; then
    echo "ERROR: could not install spotifyd."
    exit 1
  fi
  write_spotifyd_conf
  echo "==> Signing in the built-in Spotify device \"$(spotifyd_name)\""
  echo "    A browser window will open. Log in, approve, then return here."
  echo
  "$SPOTIFYD_BIN" authenticate --config-path "$SPOTIFYD_CONF"
  if ! spotifyd_logged_in; then
    echo "ERROR: spotifyd login did not write credentials."
    echo "    Expected: $SPOTIFYD_CACHE/oauth/credentials.json"
    exit 1
  fi
  echo "==> Saved credentials. Next ./start.sh will play through this machine."
}

ensure_spotify_device() {
  local mode
  mode="$(echo "${JARVIS_SPOTIFY_LOCAL_DEVICE:-auto}" | tr '[:upper:]' '[:lower:]')"
  case "$mode" in
    0|off|no|false) return ;;
  esac
  if ! download_spotifyd; then
    if [[ "$mode" != "auto" ]]; then
      echo "NOTE: spotifyd unavailable — open the Spotify app for playback"
    fi
    return
  fi
  write_spotifyd_conf
  if ! spotifyd_logged_in; then
    if [[ "$MODE" != "detach" ]] && [[ -t 0 ]]; then
      echo "==> First-time Spotify login for the built-in speaker..."
      if "$SPOTIFYD_BIN" authenticate --config-path "$SPOTIFYD_CONF" && spotifyd_logged_in; then
        write_spotifyd_conf
      else
        echo "NOTE: skipped Spotify login. Run ./start.sh --spotify-login later."
        echo "      Until then, playback needs the Spotify app open on some device."
        return
      fi
    else
      echo "NOTE: built-in Spotify device not signed in yet."
      echo "      Run once:  ./start.sh --spotify-login"
      return
    fi
  fi
  if pid_alive "spotifyd"; then
    echo "==> Spotify device \"$(spotifyd_name)\" already running"
    return
  fi
  echo "==> Starting Spotify device \"$(spotifyd_name)\" (spotifyd)..."
  (
    exec "$SPOTIFYD_BIN" --no-daemon --config-path "$SPOTIFYD_CONF"
  ) >>"$LOG_DIR/spotifyd.log" 2>&1 &
  echo $! >"$PID_DIR/spotifyd.pid"
  echo "    pid $(cat "$PID_DIR/spotifyd.pid")  →  logs/spotifyd.log"
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

  if pid_alive "$name"; then
    echo "==> $name already running (pid $(cat "$pidfile"))"
    return
  fi

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

if [[ "$MODE" == "spotify-login" ]]; then
  spotify_login
  exit 0
fi

echo "==> Ensuring Python venvs..."
ensure_all_venvs
ensure_tts_models

if [[ "$MODE" == "setup" ]]; then
  echo "==> Fetching spotifyd (local Spotify speaker)..."
  download_spotifyd || echo "NOTE: spotifyd download skipped"
  echo "Setup complete. Venvs are ready under services/*/.venv"
  exit 0
fi

if [[ "$MODE" == "control" ]]; then
  ensure_control
  echo "Voice stack not started. Use ./start.sh --detach when you want him listening."
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
ensure_spotify_device
pin_linux_mic
ensure_sonos_http

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
  echo "  Stop + panel:  ./stop.sh --all"
fi

ensure_clipper
ensure_control

if [[ "$MODE" == "detach" ]]; then
  echo
  echo "Detached. Voice is running in the background."
  echo "  Stop voice:  ./stop.sh"
  echo "  Stop panel:  ./stop.sh --all"
  exit 0
fi

watch_timing
