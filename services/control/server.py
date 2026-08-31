#!/usr/bin/env python3
"""Phone / LAN control panel for a headless Jarvis box.

Serves a PWA dashboard (start/stop/status/avatar/transcript) on
JARVIS_CONTROL_PORT (default 8740). The panel stays up when the voice stack
is stopped so you can bring him back from a phone.

It also acts as a remote mouth and ear: hold-to-talk audio recorded in the
browser is transcribed and injected onto `jarvis/stt/text`, so you can give
Jarvis orders from another room, and everything he hears or says is mirrored
into a live transcript.
"""
from __future__ import annotations

import json
import os
import queue
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parents[2]
WEB = Path(__file__).resolve().parent / "web"
CERT_DIR = Path(__file__).resolve().parent / "certs"
PID_DIR = ROOT / ".pids"
SNAP_SCRIPT = ROOT / "services" / "camera" / "snap.py"
START_SH = ROOT / "start.sh"
STOP_SH = ROOT / "stop.sh"


def _load_dotenv():
    path = ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv()

PHOTOS = Path(os.environ.get("JARVIS_PHOTOS_DIR") or ROOT / "Photos")
CLIPS = Path(os.environ.get("JARVIS_CLIPS_DIR") or ROOT / "Clips")


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "off", "no", "false"}


HOST = os.environ.get("JARVIS_CONTROL_HOST", "0.0.0.0").strip() or "0.0.0.0"
PORT = int(os.environ.get("JARVIS_CONTROL_PORT", "8740"))
PIN = os.environ.get("JARVIS_CONTROL_PIN", "").strip()
BROKER = os.environ.get("JARVIS_MQTT_HOST", "127.0.0.1").strip() or "127.0.0.1"
BROKER_PORT = int(os.environ.get("JARVIS_MQTT_PORT", "1883"))
USE_TLS = _flag("JARVIS_CONTROL_TLS", True)

GROQ_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_STT_MODEL = (
    os.environ.get("GROQ_STT_MODEL", "").strip() or "whisper-large-v3-turbo"
)
CARTESIA_KEY = os.environ.get("CARTESIA_API_KEY", "").strip()

VOICE_SERVICES = ("brain", "tts", "stt", "mic-wakeword")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
TRANSCRIPT_LIMIT = 120
# A streamed answer arrives as several sentences; fold them into one bubble.
MERGE_WINDOW_SEC = 12.0

_states = {"tts": "idle", "stt": "idle", "brain": "idle", "clipper": "idle"}
_sse_clients: list[queue.Queue] = []
_sse_lock = threading.Lock()
_action_lock = threading.Lock()
_transcript: deque[dict] = deque(maxlen=TRANSCRIPT_LIMIT)
_transcript_lock = threading.Lock()
_transcript_seq = 0
_clip_waiters: dict[str, queue.Queue] = {}
_clip_lock = threading.Lock()
_mqtt_ok = False


def ts(msg: str):
    stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{stamp}] CTRL | {msg}", flush=True)


def ui_state() -> str:
    if _states.get("tts") == "speaking":
        return "speaking"
    if _states.get("stt") == "listening":
        return "listening"
    if _states.get("brain") == "thinking":
        return "thinking"
    return "idle"


def broadcast(event: dict):
    payload = json.dumps(event)
    with _sse_lock:
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(payload)
            except Exception:
                dead.append(q)
        for q in dead:
            if q in _sse_clients:
                _sse_clients.remove(q)


# ── transcript ───────────────────────────────────────────────────────


def add_transcript(role: str, text: str, *, source: str = "voice") -> dict | None:
    """Append (or extend) a transcript line and push it to every browser."""
    global _transcript_seq
    text = (text or "").strip()
    if not text:
        return None

    with _transcript_lock:
        last = _transcript[-1] if _transcript else None
        mergeable = (
            last is not None
            and role == "jarvis"
            and last["role"] == "jarvis"
            and last["source"] == source
            and (time.time() - last["at"]) < MERGE_WINDOW_SEC
        )
        if mergeable:
            last["text"] = f"{last['text']} {text}".strip()
            last["at"] = time.time()
            entry = dict(last)
        else:
            _transcript_seq += 1
            entry = {
                "id": _transcript_seq,
                "role": role,
                "text": text,
                "source": source,
                "at": time.time(),
            }
            _transcript.append(dict(entry))

    broadcast({"transcript": entry})
    return entry


def transcript_snapshot() -> list[dict]:
    with _transcript_lock:
        return [dict(e) for e in _transcript]


# ── status ───────────────────────────────────────────────────────────


def pid_alive(name: str) -> bool:
    pidfile = PID_DIR / f"{name}.pid"
    if not pidfile.is_file():
        return False
    try:
        pid = int(pidfile.read_text().strip())
    except ValueError:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def broker_up() -> bool:
    try:
        with socket.create_connection((BROKER, BROKER_PORT), timeout=0.4):
            return True
    except OSError:
        return False


def stt_relay_ready() -> bool:
    return bool(GROQ_KEY or CARTESIA_KEY)


def stack_status() -> dict:
    services = {name: pid_alive(name) for name in VOICE_SERVICES}
    services["clipper"] = pid_alive("clipper")
    return {
        "voice": all(services[name] for name in VOICE_SERVICES),
        "broker": broker_up(),
        "mqtt": _mqtt_ok,
        "control": True,
        "services": services,
        "state": ui_state(),
        "clipper": _states.get("clipper", "idle"),
        "pin_required": bool(PIN),
        "mic_relay": stt_relay_ready(),
        "tls": USE_TLS,
        "https_url": f"https://{lan_ip()}:{PORT}",
    }


def latest_snap() -> str | None:
    if not PHOTOS.is_dir():
        return None
    shots = sorted(
        (p for p in PHOTOS.glob("*.jpg") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
    )
    if not shots:
        return None
    return f"/photos/{shots[-1].name}"


def list_clips(limit: int = 12) -> list[dict]:
    if not CLIPS.is_dir():
        return []
    files = sorted(
        (p for p in CLIPS.glob("clip-*.mp4") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    out = []
    for path in files[:limit]:
        stat = path.stat()
        out.append(
            {
                "name": path.name,
                "url": f"/clips/{path.name}",
                "size_mb": round(stat.st_size / 1_048_576, 1),
                "when": datetime.fromtimestamp(stat.st_mtime).strftime("%b %d %H:%M"),
            }
        )
    return out


# ── actions ──────────────────────────────────────────────────────────


def run_stack(action: str) -> tuple[bool, str]:
    if action == "start":
        argv = [str(START_SH), "--detach"]
        env = {**os.environ, "JARVIS_SKIP_CONTROL": "1"}
    elif action == "stop":
        argv = [str(STOP_SH)]
        env = os.environ.copy()
    else:
        return False, "unknown action"
    try:
        proc = subprocess.run(
            argv,
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return False, f"{action} timed out"
    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        return False, out[-800:] or f"{action} failed"
    return True, out[-800:] or f"{action} ok"


def take_snap() -> tuple[bool, str, str | None]:
    sys.path.insert(0, str(SNAP_SCRIPT.parent))
    from snap import unique_media_path  # type: ignore

    dest = unique_media_path(PHOTOS, "photo", "jpg")
    try:
        proc = subprocess.run(
            [sys.executable, str(SNAP_SCRIPT), str(dest)],
            capture_output=True,
            text=True,
            timeout=25,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        return False, "Camera timed out.", None
    if proc.returncode != 0 or not dest.is_file():
        err = (proc.stderr or proc.stdout or "camera failed").strip()
        return False, err, None
    return True, "Photo saved.", f"/photos/{dest.name}"


def request_clip(seconds: int | None = None) -> tuple[bool, str, dict | None]:
    """Ask the clipper for a clip and wait for it to land."""
    if not pid_alive("clipper"):
        return False, "The clip recorder isn't running.", None
    request_id = uuid.uuid4().hex[:12]
    inbox: queue.Queue = queue.Queue(maxsize=1)
    with _clip_lock:
        _clip_waiters[request_id] = inbox
    payload: dict = {"id": request_id}
    if seconds:
        payload["seconds"] = int(seconds)
    mqtt_client.publish("jarvis/clip/save", json.dumps(payload))
    try:
        result = inbox.get(timeout=20)
    except queue.Empty:
        return False, "The clip recorder didn't answer.", None
    finally:
        with _clip_lock:
            _clip_waiters.pop(request_id, None)
    if result.get("error"):
        return False, str(result["error"]), None
    return True, f"Saved {result.get('name', 'clip')}.", result


def send_command(text: str, *, source: str = "phone") -> bool:
    """Inject a command as if it had been spoken into the room."""
    text = (text or "").strip()
    if not text:
        return False
    # Cut him off mid-sentence like the wake word does.
    mqtt_client.publish("jarvis/tts/interrupt", "stop")
    mqtt_client.publish("jarvis/stt/text", text)
    add_transcript("user", text, source=source)
    ts(f"command from {source} → {text!r}")
    return True


# ── phone mic → text ─────────────────────────────────────────────────

_EXT_BY_TYPE = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "aac",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}


def _audio_filename(content_type: str) -> str:
    base = (content_type or "").split(";")[0].strip().lower()
    return f"speech.{_EXT_BY_TYPE.get(base, 'webm')}"


def transcribe(audio: bytes, content_type: str) -> tuple[str, str]:
    """Return (text, error). Groq Whisper first, Cartesia as a fallback."""
    try:
        import requests
    except ImportError:
        return "", "requests is not installed in the control venv"

    filename = _audio_filename(content_type)
    mime = (content_type or "application/octet-stream").split(";")[0].strip()

    if GROQ_KEY:
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {GROQ_KEY}"},
                files={"file": (filename, audio, mime)},
                data={
                    "model": GROQ_STT_MODEL,
                    "language": "en",
                    "response_format": "json",
                    "temperature": "0",
                },
                timeout=45,
            )
            if resp.status_code < 400:
                return ((resp.json() or {}).get("text") or "").strip(), ""
            ts(f"groq stt {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            ts(f"groq stt failed: {e}")

    if CARTESIA_KEY:
        try:
            resp = requests.post(
                "https://api.cartesia.ai/stt",
                headers={
                    "X-API-Key": CARTESIA_KEY,
                    "Cartesia-Version": "2025-04-16",
                },
                files={"file": (filename, audio, mime)},
                data={"model": "ink-whisper", "language": "en"},
                timeout=45,
            )
            if resp.status_code < 400:
                return ((resp.json() or {}).get("text") or "").strip(), ""
            ts(f"cartesia stt {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            ts(f"cartesia stt failed: {e}")

    if not stt_relay_ready():
        return "", "No transcription key set (GROQ_API_KEY or CARTESIA_API_KEY)."
    return "", "Transcription failed."


# ── http ─────────────────────────────────────────────────────────────

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args):
        ts(f"{self.address_string()} {fmt % args}")

    def _pin_ok(self) -> bool:
        if not PIN:
            return True
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        if cookie.get("jarvis_auth") and cookie["jarvis_auth"].value == PIN:
            return True
        header = self.headers.get("X-Jarvis-Pin", "")
        return header == PIN

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return b""
        if length > MAX_UPLOAD_BYTES:
            return b""
        return self.rfile.read(length)

    def _read_json(self) -> dict:
        raw = self._body()
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _send(self, code: int, body: bytes, content_type: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, payload: dict, extra: dict | None = None):
        body = json.dumps(payload).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8", extra)

    def _file(self, path: Path, content_type: str):
        if not path.is_file():
            self._send(404, b"not found", "text/plain")
            return
        data = path.read_bytes()
        extra = {"Cache-Control": "no-cache"} if path.suffix in {".html", ".css"} else None
        self._send(200, data, content_type, extra)

    def _video(self, path: Path):
        """Serve mp4 with Range support so mobile Safari will play it."""
        if not path.is_file():
            self._send(404, b"not found", "text/plain")
            return
        size = path.stat().st_size
        rng = self.headers.get("Range", "")
        start, end = 0, size - 1
        partial = False
        match = re.match(r"bytes=(\d*)-(\d*)", rng or "")
        if match and (match.group(1) or match.group(2)):
            if match.group(1):
                start = int(match.group(1))
                if match.group(2):
                    end = min(int(match.group(2)), size - 1)
            else:  # suffix range: last N bytes
                start = max(0, size - int(match.group(2)))
            if start > end or start >= size:
                self._send(416, b"bad range", "text/plain", {"Content-Range": f"bytes */{size}"})
                return
            partial = True

        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _is_https(self) -> bool:
        return isinstance(self.connection, ssl.SSLSocket)

    def _redirect_https(self) -> bool:
        """Send browsers from http://LAN-IP to https so the mic is allowed."""
        if not USE_TLS or self._is_https():
            return False
        host = self.headers.get("Host") or f"{lan_ip()}:{PORT}"
        loc = f"https://{host}{self.path}"
        self.send_response(301)
        self.send_header("Location", loc)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        return True

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if self._redirect_https():
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/api/status":
            payload = stack_status()
            payload["latest_snap"] = latest_snap()
            self._json(200, payload)
            return

        if path == "/api/logs":
            if not self._pin_ok():
                self._json(401, {"ok": False, "error": "pin"})
                return
            log = ROOT / "logs" / "timing.log"
            lines = []
            if log.is_file():
                lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
            self._json(200, {"lines": lines})
            return

        if path == "/api/transcript":
            if not self._pin_ok():
                self._json(401, {"ok": False, "error": "pin"})
                return
            self._json(200, {"entries": transcript_snapshot()})
            return

        if path == "/api/clips":
            if not self._pin_ok():
                self._json(401, {"ok": False, "error": "pin"})
                return
            self._json(200, {"clips": list_clips()})
            return

        if path == "/api/events":
            if not self._pin_ok():
                self._json(401, {"ok": False, "error": "pin"})
                return
            self._sse()
            return

        if path in {"/", "/index.html"}:
            self._file(WEB / "index.html", "text/html; charset=utf-8")
            return
        if path in {"/avatar", "/avatar.html"}:
            self._file(WEB / "avatar.html", "text/html; charset=utf-8")
            return
        if path == "/app.css":
            self._file(WEB / "app.css", "text/css; charset=utf-8")
            return
        if path == "/manifest.webmanifest":
            self._file(WEB / "manifest.webmanifest", "application/manifest+json")
            return
        if path.startswith("/character/"):
            name = Path(path).name
            if name in {"idle.png", "talk.png", "idle.webp", "talk.webp"}:
                self._file(WEB / "character" / name, "image/png")
                return
            self._send(404, b"not found", "text/plain")
            return
        if path.startswith("/photos/") or path.startswith("/captures/"):
            if not self._pin_ok():
                self._json(401, {"ok": False, "error": "pin"})
                return
            name = Path(path).name
            if not name.endswith(".jpg") or not _SAFE_NAME.match(name):
                self._send(400, b"bad path", "text/plain")
                return
            self._file(PHOTOS / name, "image/jpeg")
            return
        if path.startswith("/clips/"):
            if not self._pin_ok():
                self._json(401, {"ok": False, "error": "pin"})
                return
            name = Path(path).name
            if not name.endswith(".mp4") or not _SAFE_NAME.match(name):
                self._send(400, b"bad path", "text/plain")
                return
            self._video(CLIPS / name)
            return

        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self._redirect_https():
            return
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/login":
            data = self._read_json()
            pin = str(data.get("pin") or "")
            if PIN and pin != PIN:
                self._json(401, {"ok": False, "error": "bad pin"})
                return
            extra = {
                "Set-Cookie": f"jarvis_auth={PIN or 'open'}; Path=/; HttpOnly; SameSite=Lax"
            }
            self._json(200, {"ok": True}, extra)
            return

        if not self._pin_ok():
            # Still drain the body so the connection stays usable.
            self._body()
            self._json(401, {"ok": False, "error": "pin"})
            return

        if path == "/api/voice":
            self._handle_voice(parsed)
            return

        data = self._read_json()

        if path in {"/api/start", "/api/stop"}:
            action = "start" if path.endswith("start") else "stop"
            with _action_lock:
                ok, detail = run_stack(action)
            self._json(200 if ok else 500, {"ok": ok, "detail": detail, **stack_status()})
            return

        if path == "/api/snap":
            ok, msg, url = take_snap()
            self._json(
                200 if ok else 500,
                {"ok": ok, "message": msg, "url": url, **stack_status()},
            )
            return

        if path == "/api/clip":
            try:
                raw = data.get("seconds")
                seconds = int(float(raw)) if raw else None
            except (TypeError, ValueError):
                self._json(400, {"ok": False, "error": "seconds must be a number"})
                return
            if seconds is not None and not 1 <= seconds <= 3600:
                self._json(400, {"ok": False, "error": "seconds out of range"})
                return
            ok, msg, clip = request_clip(seconds)
            self._json(
                200 if ok else 500,
                {"ok": ok, "message": msg, "clip": clip, "clips": list_clips()},
            )
            return

        if path == "/api/say":
            text = str(data.get("text") or "").strip()
            if not text:
                self._json(400, {"ok": False, "error": "empty"})
                return
            if not pid_alive("brain"):
                self._json(
                    503, {"ok": False, "error": "Jarvis isn't running. Press Start."}
                )
                return
            send_command(text, source="typed")
            self._json(200, {"ok": True, "text": text})
            return

        self._json(404, {"ok": False, "error": "not found"})

    def _handle_voice(self, parsed):
        content_type = self.headers.get("Content-Type", "application/octet-stream")
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            self._json(400, {"ok": False, "error": "no audio"})
            return
        if length > MAX_UPLOAD_BYTES:
            self.rfile.read(length)
            self._json(413, {"ok": False, "error": "recording too long"})
            return
        audio = self.rfile.read(length)

        # preview=1 transcribes without dispatching — used to sanity-check the mic.
        dry_run = parse_qs(parsed.query or "").get("preview", ["0"])[0] == "1"
        if not dry_run and not pid_alive("brain"):
            self._json(503, {"ok": False, "error": "Jarvis isn't running. Press Start."})
            return

        text, err = transcribe(audio, content_type)
        if err:
            self._json(502, {"ok": False, "error": err})
            return
        if not text or len(re.sub(r"[^A-Za-z0-9]", "", text)) < 2:
            self._json(200, {"ok": True, "text": "", "message": "I didn't catch that."})
            return

        if not dry_run:
            send_command(text, source="phone")
        self._json(200, {"ok": True, "text": text})

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q: queue.Queue = queue.Queue(maxsize=64)
        with _sse_lock:
            _sse_clients.append(q)
        try:
            hello = json.dumps(
                {
                    "state": ui_state(),
                    "status": stack_status(),
                    "transcript_all": transcript_snapshot(),
                }
            )
            self.wfile.write(f"data: {hello}\n\n".encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    payload = q.get(timeout=20)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _sse_lock:
                if q in _sse_clients:
                    _sse_clients.remove(q)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, ssl_ctx=None):
        super().__init__(addr, handler)
        self.ssl_ctx = ssl_ctx

    def get_request(self):
        sock, addr = self.socket.accept()
        if self.ssl_ctx is None:
            return sock, addr
        try:
            peek = sock.recv(1, socket.MSG_PEEK)
        except OSError:
            peek = b""
        if peek[:1] == b"\x16":
            sock = self.ssl_ctx.wrap_socket(sock, server_side=True)
        return sock, addr


# ── tls (needed for phone mic access over LAN) ───────────────────────


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def ensure_cert() -> tuple[Path, Path] | None:
    """Self-signed cert so browsers treat the panel as a secure context.

    getUserMedia is blocked on plain http origins, which is the only reason
    this exists. Browsers will warn once about the unknown issuer.
    """
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    cert, key = CERT_DIR / "cert.pem", CERT_DIR / "key.pem"
    san = f"DNS:localhost,IP:127.0.0.1,IP:{lan_ip()}"
    stamp = CERT_DIR / "san.txt"
    fresh = (
        cert.is_file()
        and key.is_file()
        and stamp.is_file()
        and stamp.read_text(encoding="utf-8").strip() == san
    )
    if fresh:
        return cert, key

    openssl = "openssl"
    try:
        proc = subprocess.run(
            [
                openssl, "req", "-x509", "-newkey", "rsa:2048", "-sha256",
                "-days", "3650", "-nodes",
                "-keyout", str(key), "-out", str(cert),
                "-subj", "/CN=jarvis",
                "-addext", f"subjectAltName={san}",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        ts(f"could not generate TLS cert ({e}) — falling back to http")
        return None
    if proc.returncode != 0 or not cert.is_file():
        ts(f"openssl failed: {(proc.stderr or '').strip()[:200]} — falling back to http")
        return None
    stamp.write_text(san + "\n", encoding="utf-8")
    ts(f"generated self-signed cert for {san}")
    return cert, key


# ── mqtt ─────────────────────────────────────────────────────────────

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

SUBSCRIPTIONS = (
    "jarvis/status/#",
    "jarvis/stt/text",
    "jarvis/stt/followup",
    "jarvis/brain/response",
    "jarvis/clip/saved",
    "jarvis/clip/error",
)


def _on_clip_result(topic: str, payload: str):
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return
    if topic == "jarvis/clip/saved":
        name = str(data.get("name") or "clip")
        add_transcript("system", f"Saved {name}", source="clip")
        broadcast({"clips": list_clips()})
    request_id = str(data.get("id") or "")
    if not request_id:
        return
    with _clip_lock:
        inbox = _clip_waiters.get(request_id)
    if inbox is None:
        return
    try:
        inbox.put_nowait(data)
    except queue.Full:
        pass


def _on_mqtt_message(client, userdata, msg):
    topic = msg.topic or ""
    payload = msg.payload.decode("utf-8", errors="replace").strip()

    if topic.startswith("jarvis/clip/"):
        _on_clip_result(topic, payload)
        return

    if topic in {"jarvis/stt/text", "jarvis/stt/followup"}:
        # Phone/typed commands are logged when injected, so skip the echo.
        with _transcript_lock:
            last = _transcript[-1] if _transcript else None
            echo = (
                last is not None
                and last["role"] == "user"
                and last["source"] in {"phone", "typed"}
                and last["text"] == payload
                and (time.time() - last["at"]) < 10
            )
        if not echo:
            add_transcript("user", payload, source="voice")
        return

    if topic == "jarvis/brain/response":
        add_transcript("jarvis", payload)
        return

    source = topic.rsplit("/", 1)[-1]
    if source in _states and payload:
        _states[source] = payload
        broadcast({"state": ui_state(), "source": source, "raw": payload})


def _on_connect(client, userdata, flags, reason_code, properties=None):
    global _mqtt_ok
    _mqtt_ok = True
    for topic in SUBSCRIPTIONS:
        client.subscribe(topic)
    ts("MQTT connected, mirroring status + transcript")


def _on_disconnect(client, userdata, flags, reason_code, properties=None):
    global _mqtt_ok
    _mqtt_ok = False


def mqtt_loop():
    mqtt_client.on_message = _on_mqtt_message
    mqtt_client.on_connect = _on_connect
    mqtt_client.on_disconnect = _on_disconnect
    while True:
        try:
            mqtt_client.connect(BROKER, BROKER_PORT, keepalive=30)
            mqtt_client.loop_forever()
        except Exception as e:
            ts(f"MQTT retry in 2s ({e})")
            time.sleep(2)


def main() -> int:
    WEB.mkdir(parents=True, exist_ok=True)
    (WEB / "character").mkdir(parents=True, exist_ok=True)
    PHOTOS.mkdir(parents=True, exist_ok=True)
    CLIPS.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=mqtt_loop, daemon=True).start()

    ctx = None
    scheme = "http"
    if USE_TLS:
        pair = ensure_cert()
        if pair:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(certfile=str(pair[0]), keyfile=str(pair[1]))
            scheme = "https"
        else:
            ts("TLS cert unavailable — serving plain http (mic will be blocked on LAN IPs)")

    httpd = Server((HOST, PORT), Handler, ssl_ctx=ctx)
    ts(f"control panel on {scheme}://{HOST}:{PORT}  (PIN={'on' if PIN else 'off'})")
    if scheme == "https":
        ts(f"open https://{lan_ip()}:{PORT}  — accept the certificate warning once for the mic")
        ts(f"http://{lan_ip()}:{PORT} redirects there")
    if not stt_relay_ready():
        ts("no GROQ_API_KEY/CARTESIA_API_KEY — phone mic will fall back to typing")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        ts("shutting down...")
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
