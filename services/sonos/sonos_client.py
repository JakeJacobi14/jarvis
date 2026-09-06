"""Local Sonos control via the LAN UPnP API (SoCo).

Speech is a short WAV the Amp fetches over HTTP. Spotify is queued the
same way the Sonos app does it (ShareLink), so the Amp leaves HDMI/TV
instead of playing Connect in the background. Volume and transport
(pause/skip) also go through here when the Amp is the speaker.
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


def tts_mode() -> str:
    """overlay = duck music on the Amp; sonos = steal the Amp; local = PC speakers.

    Default is overlay so speech and Spotify share the same speakers.
    """
    mode = os.environ.get("JARVIS_TTS_OUTPUT", "overlay").strip().lower() or "overlay"
    if mode in {"local", "mac", "pc", "computer", "speakers"}:
        return "local"
    if mode in {"sonos", "amp", "1", "on", "yes", "true"}:
        return "sonos"
    if mode in {"overlay", "clip", "duck", "mix", "announce"}:
        return "overlay"
    return "overlay"


def wanted() -> bool:
    """True when the user asked for Sonos, or auto and a speaker exists."""
    mode = _mode()
    if mode in {"0", "off", "no", "false", "local", "speakers", "mac"}:
        return False
    if mode in {"sonos", "amp", "1", "on", "yes", "true"}:
        return True
    return True  # auto — we'll no-op if nothing is on the LAN


def tts_enabled() -> bool:
    """Spoken replies on the Amp (steal or overlay). Default is local PC speakers."""
    if tts_mode() not in {"sonos", "overlay"}:
        return False
    return configured()


def configured() -> bool:
    """True if a Sonos speaker is actually reachable (Spotify / volume)."""
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
_FETCHED: set[str] = set()
_AUDIO_LOCK = threading.Lock()
_http: ThreadingHTTPServer | None = None
_http_port = 0
_TTS_BUF = Path(os.environ.get("JARVIS_SONOS_HTTP_DIR") or ROOT / ".ttsbuf")


def _advertised_port() -> int:
    return int(os.environ.get("JARVIS_SONOS_HTTP_PORT", "8742") or "8742")


def _listen_port() -> int:
    raw = os.environ.get("JARVIS_SONOS_HTTP_LISTEN_PORT", "").strip()
    if raw:
        return int(raw)
    return _advertised_port()


def _external_http() -> bool:
    raw = os.environ.get("JARVIS_SONOS_HTTP_EXTERNAL", "").strip().lower()
    return raw in {"1", "on", "yes", "true"}


def _publish_wav(name: str, data: bytes) -> None:
    with _AUDIO_LOCK:
        _AUDIO[name] = data
        while len(_AUDIO) > 12:
            old = next(iter(_AUDIO))
            _AUDIO.pop(old, None)
            try:
                (_TTS_BUF / old).unlink(missing_ok=True)
            except OSError:
                pass
    try:
        _TTS_BUF.mkdir(parents=True, exist_ok=True)
        path = _TTS_BUF / name
        path.write_bytes(data)
        path.chmod(0o644)
    except OSError:
        pass


def _unpublish_wav(name: str) -> None:
    with _AUDIO_LOCK:
        _AUDIO.pop(name, None)
    try:
        (_TTS_BUF / name).unlink(missing_ok=True)
    except OSError:
        pass


class _AudioHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args):
        print(f"[sonos-http] {self.address_string()} {fmt % args}", flush=True)

    def do_GET(self):
        name = Path(unquote(urlparse(self.path).path)).name
        with _AUDIO_LOCK:
            data = _AUDIO.get(name)
            if data is not None:
                _FETCHED.add(name)
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
    if _external_http():
        _TTS_BUF.mkdir(parents=True, exist_ok=True)
        return _advertised_port()
    if _http is not None:
        return _http_port
    preferred = _listen_port()
    host = os.environ.get("JARVIS_SONOS_HTTP_BIND", "0.0.0.0").strip() or "0.0.0.0"
    last_err: Exception | None = None
    for port in (preferred, 0):
        try:
            httpd = _AudioServer((host, port), _AudioHandler)
            _http = httpd
            _http_port = int(httpd.server_address[1])
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            return _advertised_port() if _http_port == preferred else _http_port
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
    # Slight digital gain so the clip is audible over ducked music
    # without sending Sonos a player-volume field.
    gain = float(os.environ.get("JARVIS_TTS_GAIN", "1.45") or "1.45")
    clipped = np.clip(arr * max(0.5, min(2.5, gain)), -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2").tobytes()
    return _wav_bytes(pcm, sample_rate, 1)


# ── playback ─────────────────────────────────────────────────────────

_play_lock = threading.Lock()
_stop = threading.Event()
_clip_id: str | None = None
_clip_lock = threading.Lock()
_API_KEY = "123e4567-e89b-12d3-a456-426655440000"


def _ssl_ctx():
    import ssl

    ctx = ssl._create_unverified_context()
    return ctx


def _player_uid(sp) -> str:
    uid = (getattr(sp, "uid", None) or "").strip()
    if uid:
        return uid
    info = {}
    try:
        info = sp.get_speaker_info() or {}
    except Exception:
        pass
    return str(info.get("uid") or info.get("serial_number") or "").strip()


def _clip_url(sp, suffix: str = "") -> str:
    uid = _player_uid(sp)
    if not uid:
        raise RuntimeError("Sonos player id missing")
    ip = getattr(sp, "ip_address", None) or os.environ.get("JARVIS_SONOS_IP", "").strip()
    if not ip:
        raise RuntimeError("Sonos IP missing")
    return f"https://{ip}:1443/api/v1/players/{uid}/audioClip{suffix}"


def _http_json(url: str, *, method: str = "POST", body: dict | None = None) -> dict:
    import json
    import urllib.error
    import urllib.request

    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-Sonos-Api-Key": _API_KEY,
        },
    )
    try:
        with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=8) as resp:
            raw = resp.read() or b"{}"
            return json.loads(raw.decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"Sonos clip {e.code}: {detail or e.reason}") from e


def _cancel_clip() -> None:
    global _clip_id
    with _clip_lock:
        clip_id = _clip_id
        _clip_id = None
    if not clip_id:
        return
    try:
        sp = speaker()
        if sp is not None:
            _http_json(_clip_url(sp, f"/{clip_id}"), method="DELETE", body=None)
    except Exception:
        pass


def _clip_fetched(name: str, *, since_s: float) -> bool:
    with _AUDIO_LOCK:
        if name in _FETCHED:
            return True
    if not _external_http():
        return False
    try:
        import subprocess

        out = subprocess.check_output(
            [
                "docker",
                "logs",
                "--since",
                f"{max(5, int(since_s) + 3)}s",
                "jarvis-sonos-http",
            ],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=1.5,
        )
    except Exception:
        return False
    if name in out:
        with _AUDIO_LOCK:
            _FETCHED.add(name)
        return True
    return False


def interrupt() -> None:
    _stop.set()
    if tts_mode() == "overlay":
        _cancel_clip()
        return
    sp = _speaker
    if sp is None:
        return
    try:
        sp.stop()
    except Exception:
        pass


def _play_overlay(data: bytes, *, seconds: float) -> None:
    """Duck current music and speak over it (AudioClip). Spotify keeps playing."""
    global _clip_id
    sp = speaker()
    if sp is None:
        raise RuntimeError("No Sonos speaker found")
    port = _ensure_http()
    name = f"tts-{uuid.uuid4().hex}.wav"
    _publish_wav(name, data)
    url = f"http://{lan_ip()}:{port}/{name}"
    body: dict[str, Any] = {
        "name": "Jarvis",
        "appId": "com.jarvis.tts",
        "clipType": "CUSTOM",
        "streamUrl": url,
    }
    vol = os.environ.get("JARVIS_TTS_CLIP_VOLUME", "").strip()
    if vol:
        try:
            body["volume"] = max(1, min(99, int(vol)))
        except ValueError:
            pass
    _stop.clear()
    t_post = time.perf_counter()
    with _play_lock:
        _cancel_clip()
        try:
            reply = _http_json(_clip_url(sp), body=body)
        except Exception:
            _unpublish_wav(name)
            raise
        post_s = time.perf_counter() - t_post
        clip_id = str(reply.get("id") or "").strip() or None
        with _clip_lock:
            _clip_id = clip_id
        # Sonos often accepts the POST immediately and GETs the WAV much
        # later. Wait for that GET, then hold for the clip length.
        t_fetch = time.monotonic()
        fetched = False
        while time.monotonic() - t_fetch < 12.0 and not _stop.is_set():
            if _clip_fetched(name, since_s=time.monotonic() - t_fetch + 4):
                fetched = True
                break
            time.sleep(0.2)
        fetch_s = time.monotonic() - t_fetch
        remain = max(0.4, seconds) + 0.45
        print(
            f"[sonos] overlay {url} ({seconds:.2f}s, post {post_s:.2f}s, "
            f"get {'%.2f' % fetch_s if fetched else 'timeout'}s)",
            flush=True,
        )
        deadline = time.monotonic() + remain
        try:
            while time.monotonic() < deadline and not _stop.is_set():
                time.sleep(0.12)
        finally:
            with _clip_lock:
                _clip_id = None
            threading.Timer(20.0, _unpublish_wav, args=(name,)).start()


def play_wav_bytes(data: bytes, *, seconds: float) -> None:
    """Play a WAV on the Amp and block until it finishes or is interrupted."""
    if tts_mode() == "overlay":
        _play_overlay(data, seconds=seconds)
        return
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
    _publish_wav(name, data)

    url = f"http://{lan_ip()}:{port}/{name}"
    print(f"[sonos] play {url} ({seconds:.2f}s)", flush=True)
    _stop.clear()
    with _play_lock:
        try:
            sp.play_uri(url)
        except Exception as e:
            _unpublish_wav(name)
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

    _unpublish_wav(name)


def play_float32(samples, sample_rate: int) -> float:
    """Render float32 mono PCM to the Amp. Returns play seconds."""
    import numpy as np

    arr = np.asarray(samples, dtype=np.float32).reshape(-1)
    seconds = float(arr.size) / float(sample_rate or 1)
    play_wav_bytes(float32_to_wav(arr, sample_rate), seconds=seconds)
    return seconds


def _coordinator():
    sp = speaker()
    if sp is None:
        return None
    try:
        if not sp.is_coordinator:
            return sp.group.coordinator
    except Exception:
        pass
    return sp


def on_tv() -> bool:
    """True when the Amp is in HDMI/TV (or line-in) mode instead of music."""
    sp = _coordinator()
    if sp is None:
        return False
    try:
        if bool(getattr(sp, "is_playing_tv", False)) or bool(
            getattr(sp, "is_playing_line_in", False)
        ):
            return True
    except Exception:
        pass
    try:
        uri = ((sp.get_current_media_info() or {}).get("uri") or "").lower()
    except Exception:
        uri = ""
    return uri.startswith("x-sonos-htastream:") or ":spdif" in uri or ":optical" in uri


def _xml_escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _leave_tv(sp) -> None:
    """Point the Amp at its music queue so HDMI/ARC is no longer selected."""
    try:
        if bool(getattr(sp, "is_playing_tv", False)):
            sp.stop()
    except Exception:
        pass
    try:
        queue_uri = f"x-rincon-queue:{sp.uid}#0"
        sp.avTransport.SetAVTransportURI(
            [
                ("InstanceID", 0),
                ("CurrentURI", queue_uri),
                ("CurrentURIMetaData", ""),
            ]
        )
    except Exception:
        pass


def play_spotify_uri(uri: str, title: str = "") -> bool:
    """Play a Spotify track through the Amp, switching it off TV if needed.

    Uses the same Sonos music-service queue path as the official app, which
    is what actually takes HDMI/ARC away from the TV. Spotify Connect via
    the Web API often starts in the background and leaves the Amp on TV.
    """
    sp = _coordinator()
    if sp is None:
        return False
    raw = (uri or "").strip()
    if raw.startswith("https://open.spotify.com/"):
        pass
    elif raw.startswith("spotify:"):
        pass
    else:
        return False

    try:
        from soco.plugins.sharelink import ShareLinkPlugin
    except ImportError:
        return False

    plugin = ShareLinkPlugin(sp)
    if not plugin.is_share_link(raw):
        # Web API gives spotify:track:id; some SoCo builds want the https form.
        parts = raw.split(":")
        if len(parts) >= 3 and parts[0] == "spotify":
            raw = f"https://open.spotify.com/{parts[1]}/{parts[2]}"
        if not plugin.is_share_link(raw):
            return False
    try:
        if on_tv():
            _leave_tv(sp)
        pos = plugin.add_share_link_to_queue(
            raw, dc_title=_xml_escape(title or "Spotify")
        )
        idx = max(0, int(pos) - 1)
        sp.play_from_queue(idx)
        return True
    except Exception as e:
        print(f"sonos play_spotify_uri failed: {e}", flush=True)
        return False


def pause() -> None:
    sp = _coordinator()
    if sp is None:
        raise RuntimeError("No Sonos speaker found")
    sp.pause()


def resume() -> None:
    sp = _coordinator()
    if sp is None:
        raise RuntimeError("No Sonos speaker found")
    sp.play()


def next_track() -> None:
    sp = _coordinator()
    if sp is None:
        raise RuntimeError("No Sonos speaker found")
    sp.next()


def previous_track() -> None:
    sp = _coordinator()
    if sp is None:
        raise RuntimeError("No Sonos speaker found")
    sp.previous()


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
    pct = max(0, min(100, int(pct)))
    try:
        group = sp.group
        if group is not None:
            group.volume = pct
    except Exception:
        pass
    sp.volume = pct
    if pct > 0:
        sp.mute = False


def set_muted(muted: bool) -> None:
    sp = speaker()
    if sp is None:
        raise RuntimeError("No Sonos speaker found")
    sp.mute = bool(muted)
