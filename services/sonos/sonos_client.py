"""Local Sonos control via the LAN UPnP API (SoCo).

Used for Jarvis speech (play a short WAV the Amp can fetch) and for
volume. Spotify music is steered separately through the Spotify Web API
onto the Amp's Connect session — see spotify_client.py.
"""
from __future__ import annotations

import io
import os
import socket
import threading
import time
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]


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


def _mode() -> str:
    return os.environ.get("JARVIS_AUDIO_OUTPUT", "auto").strip().lower() or "auto"


def wanted() -> bool:
    """True when the user asked for Sonos, or auto and a speaker exists."""
    mode = _mode()
    if mode in {"0", "off", "no", "false", "local", "speakers", "mac"}:
        return False
    if mode in {"sonos", "amp", "1", "on", "yes", "true"}:
        return True
    return True  # auto — we'll no-op if nothing is on the LAN


def configured() -> bool:
    """True if a Sonos speaker is actually reachable."""
    return speaker() is not None


def lan_ip() -> str:
    override = os.environ.get("JARVIS_SONOS_HTTP_HOST", "").strip()
    if override:
        return override
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# ── discovery ────────────────────────────────────────────────────────

_speaker: Any = None
_speaker_lock = threading.Lock()
_speaker_checked_at = 0.0
_SPEAKER_TTL = 45.0


def _score(name: str) -> int:
    n = (name or "").strip().lower()
    want = os.environ.get("JARVIS_SONOS_NAME", "").strip().lower()
    if want and n == want:
        return 100
    if want and want in n:
        return 80
    if "amp" in n:
        return 60
    if "sonos" in n:
        return 40
    return 10


def _score_zone(zone) -> int:
    score = _score(getattr(zone, "player_name", ""))
    try:
        model = ((zone.get_speaker_info() or {}).get("model_name") or "").lower()
        if "amp" in model:
            score = max(score, 75)
    except Exception:
        pass
    return score


def _discover() -> Any:
    try:
        import soco
        from soco.discovery import by_name
    except ImportError:
        return None

    ip = os.environ.get("JARVIS_SONOS_IP", "").strip()
    if ip:
        try:
            sp = soco.SoCo(ip)
            _ = sp.player_name
            return sp
        except Exception:
            return None

    name = os.environ.get("JARVIS_SONOS_NAME", "").strip()
    if name:
        try:
            sp = by_name(name)
            if sp is not None:
                return sp
        except Exception:
            pass

    zones = None
    try:
        zones = soco.discover(timeout=3)
    except Exception:
        zones = None
    if not zones:
        try:
            zones = soco.discover(timeout=8, allow_network_scan=True)
        except Exception:
            zones = None
    if not zones:
        return None

    ranked = sorted(
        zones,
        key=lambda z: _score_zone(z),
        reverse=True,
    )
    pick = ranked[0]
    try:
        if not pick.is_coordinator:
            pick = pick.group.coordinator
    except Exception:
        pass
    return pick


def speaker() -> Any:
    """Cached SoCo instance, or None if Sonos isn't on the LAN / is disabled."""
    global _speaker, _speaker_checked_at
    if not wanted():
        return None
    now = time.time()
    with _speaker_lock:
        if _speaker is not None and (now - _speaker_checked_at) < _SPEAKER_TTL:
            return _speaker
        if _speaker is not None:
            try:
                _ = _speaker.player_name
                _speaker_checked_at = now
                return _speaker
            except Exception:
                _speaker = None
        found = _discover()
        _speaker = found
        _speaker_checked_at = now
        return _speaker


def player_name() -> str:
    sp = speaker()
    if sp is None:
        return ""
    try:
        return (sp.player_name or "").strip()
    except Exception:
        return ""


def spotify_hints() -> list[str]:
    """Names/substrings to match against Spotify Connect devices."""
    hints: list[str] = []
    env = os.environ.get("JARVIS_SONOS_NAME", "").strip()
    if env:
        hints.append(env.lower())
    name = player_name()
    if name:
        hints.append(name.lower())
    hints.append("sonos")
    hints.append("amp")
    # de-dupe, keep order
    out, seen = [], set()
    for h in hints:
        if h and h not in seen:
            seen.add(h)
            out.append(h)
    return out


# ── http file server (Amp fetches TTS from here) ─────────────────────

_AUDIO: dict[str, bytes] = {}
_AUDIO_LOCK = threading.Lock()
_http: ThreadingHTTPServer | None = None
_http_port = 0


class _AudioHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args):
        return

    def do_GET(self):
        name = Path(unquote(urlparse(self.path).path)).name
        with _AUDIO_LOCK:
            data = _AUDIO.get(name)
        if not data:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_HEAD(self):
        name = Path(unquote(urlparse(self.path).path)).name
        with _AUDIO_LOCK:
            data = _AUDIO.get(name)
        if not data:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()


class _AudioServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _ensure_http() -> int:
    global _http, _http_port
    if _http is not None:
        return _http_port
    preferred = int(os.environ.get("JARVIS_SONOS_HTTP_PORT", "8742") or "8742")
    host = os.environ.get("JARVIS_SONOS_HTTP_BIND", "0.0.0.0").strip() or "0.0.0.0"
    last_err: Exception | None = None
    for port in (preferred, 0):
        try:
            httpd = _AudioServer((host, port), _AudioHandler)
            _http = httpd
            _http_port = int(httpd.server_address[1])
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            return _http_port
        except OSError as e:
            last_err = e
            continue
    raise RuntimeError(f"could not bind TTS HTTP server: {last_err}")


def _wav_bytes(pcm16: bytes, sample_rate: int, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm16)
    return buf.getvalue()


def float32_to_wav(samples, sample_rate: int) -> bytes:
    import numpy as np

    arr = np.asarray(samples, dtype=np.float32).reshape(-1)
    clipped = np.clip(arr, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2").tobytes()
    return _wav_bytes(pcm, sample_rate, 1)


# ── playback ─────────────────────────────────────────────────────────

_play_lock = threading.Lock()
_stop = threading.Event()


def interrupt() -> None:
    _stop.set()
    sp = _speaker
    if sp is None:
        return
    try:
        sp.stop()
    except Exception:
        pass


def play_wav_bytes(data: bytes, *, seconds: float) -> None:
    """Play a WAV on the Amp and block until it finishes or is interrupted."""
    sp = speaker()
    if sp is None:
        raise RuntimeError("No Sonos speaker found")
    try:
        if not sp.is_coordinator:
            sp = sp.group.coordinator
    except Exception:
        pass

    port = _ensure_http()
    name = f"tts-{uuid.uuid4().hex}.wav"
    with _AUDIO_LOCK:
        _AUDIO[name] = data
        while len(_AUDIO) > 12:
            _AUDIO.pop(next(iter(_AUDIO)), None)

    url = f"http://{lan_ip()}:{port}/{name}"
    _stop.clear()
    with _play_lock:
        try:
            sp.play_uri(url)
        except Exception as e:
            with _AUDIO_LOCK:
                _AUDIO.pop(name, None)
            raise RuntimeError(f"Sonos play failed: {e}") from e

        deadline = time.monotonic() + max(0.4, seconds) + 4.0
        # Give the Amp a moment to go from STOPPED → PLAYING.
        started = time.monotonic()
        while time.monotonic() < started + 2.5 and not _stop.is_set():
            try:
                state = (sp.get_current_transport_info() or {}).get(
                    "current_transport_state", ""
                )
            except Exception:
                state = ""
            if state == "PLAYING":
                break
            time.sleep(0.12)

        while time.monotonic() < deadline and not _stop.is_set():
            try:
                state = (sp.get_current_transport_info() or {}).get(
                    "current_transport_state", ""
                )
            except Exception:
                break
            if state in {"STOPPED", "PAUSED_PLAYBACK", "NO_MEDIA_PRESENT"}:
                break
            time.sleep(0.15)

    with _AUDIO_LOCK:
        _AUDIO.pop(name, None)


def play_float32(samples, sample_rate: int) -> float:
    """Render float32 mono PCM to the Amp. Returns play seconds."""
    import numpy as np

    arr = np.asarray(samples, dtype=np.float32).reshape(-1)
    seconds = float(arr.size) / float(sample_rate or 1)
    play_wav_bytes(float32_to_wav(arr, sample_rate), seconds=seconds)
    return seconds


# ── volume ───────────────────────────────────────────────────────────


def get_volume() -> int:
    sp = speaker()
    if sp is None:
        raise RuntimeError("No Sonos speaker found")
    return max(0, min(100, int(sp.volume)))


def is_muted() -> bool:
    sp = speaker()
    if sp is None:
        raise RuntimeError("No Sonos speaker found")
    return bool(sp.mute)


def set_volume(pct: int) -> None:
    sp = speaker()
    if sp is None:
        raise RuntimeError("No Sonos speaker found")
    sp.volume = max(0, min(100, int(pct)))
    if pct > 0:
        sp.mute = False


def set_muted(muted: bool) -> None:
    sp = speaker()
    if sp is None:
        raise RuntimeError("No Sonos speaker found")
    sp.mute = bool(muted)
