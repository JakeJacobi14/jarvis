#!/usr/bin/env python3
"""Phone / LAN control panel for a headless Jarvis box.

Serves a PWA dashboard (start/stop/status/avatar) on JARVIS_CONTROL_PORT
(default 8740). The panel stays up when the voice stack is stopped so you
can bring him back from a phone.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parents[2]
WEB = Path(__file__).resolve().parent / "web"
CAPTURES = ROOT / "captures"
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

HOST = os.environ.get("JARVIS_CONTROL_HOST", "0.0.0.0").strip() or "0.0.0.0"
PORT = int(os.environ.get("JARVIS_CONTROL_PORT", "8740"))
PIN = os.environ.get("JARVIS_CONTROL_PIN", "").strip()
BROKER = os.environ.get("JARVIS_MQTT_HOST", "127.0.0.1").strip() or "127.0.0.1"
BROKER_PORT = int(os.environ.get("JARVIS_MQTT_PORT", "1883"))

VOICE_SERVICES = ("brain", "tts", "stt", "mic-wakeword")

_states = {"tts": "idle", "stt": "idle", "brain": "idle"}
_sse_clients: list[queue.Queue] = []
_sse_lock = threading.Lock()
_action_lock = threading.Lock()
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
    import socket

    try:
        with socket.create_connection((BROKER, BROKER_PORT), timeout=0.4):
            return True
    except OSError:
        return False


def stack_status() -> dict:
    services = {name: pid_alive(name) for name in VOICE_SERVICES}
    return {
        "voice": all(services.values()),
        "broker": broker_up(),
        "mqtt": _mqtt_ok,
        "control": True,
        "services": services,
        "state": ui_state(),
        "pin_required": bool(PIN),
    }


def latest_snap() -> str | None:
    if not CAPTURES.is_dir():
        return None
    shots = sorted(CAPTURES.glob("snap-*.jpg"), key=lambda p: p.stat().st_mtime)
    if not shots:
        return None
    return f"/captures/{shots[-1].name}"


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
    CAPTURES.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = CAPTURES / f"snap-{stamp}.jpg"
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
    return True, "Photo saved.", f"/captures/{dest.name}"


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

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
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

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
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
        if path.startswith("/captures/"):
            if not self._pin_ok():
                self._json(401, {"ok": False, "error": "pin"})
                return
            name = Path(path).name
            if not name.endswith(".jpg") or "/" in name or ".." in name:
                self._send(400, b"bad path", "text/plain")
                return
            self._file(CAPTURES / name, "image/jpeg")
            return

        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        data = self._read_json()

        if path == "/api/login":
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
            self._json(401, {"ok": False, "error": "pin"})
            return

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

        self._json(404, {"ok": False, "error": "not found"})

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q: queue.Queue = queue.Queue(maxsize=32)
        with _sse_lock:
            _sse_clients.append(q)
        try:
            hello = json.dumps({"state": ui_state(), "status": stack_status()})
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


mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)


def _on_mqtt_message(client, userdata, msg):
    topic = msg.topic or ""
    payload = msg.payload.decode("utf-8", errors="replace").strip()
    source = topic.rsplit("/", 1)[-1]
    if source in _states and payload:
        _states[source] = payload
        broadcast({"state": ui_state(), "source": source, "raw": payload})


def _on_connect(client, userdata, flags, reason_code, properties=None):
    global _mqtt_ok
    _mqtt_ok = True
    client.subscribe("jarvis/status/#")
    ts("MQTT connected, subscribed jarvis/status/#")


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
    threading.Thread(target=mqtt_loop, daemon=True).start()
    httpd = Server((HOST, PORT), Handler)
    ts(f"control panel on http://{HOST}:{PORT}  (PIN={'on' if PIN else 'off'})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        ts("shutting down...")
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
