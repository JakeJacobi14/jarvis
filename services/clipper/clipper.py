#!/usr/bin/env python3
"""Dashcam-style rolling A/V buffer.

ffmpeg records the camera + mic continuously into short segments under
`.clipbuf/`, keeping only the most recent JARVIS_CLIP_MAX_SECONDS of footage.
When "clip that" arrives on `jarvis/clip/save`, the newest segments are
concatenated and trimmed to the requested tail length into `clips/`.

Everything is stream-copied at save time, so a clip lands in well under a
second and nothing interrupts the recording.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parents[2]
BUFFER_DIR = Path(__file__).resolve().parent / ".clipbuf"
CLIPS_DIR = ROOT / "clips"
SEGMENT_GLOB = "seg-*.mp4"


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


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "")).strip() or default)
    except ValueError:
        return default


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name, "")).strip().lower()
    if not raw:
        return default
    return raw not in {"0", "off", "no", "false"}


BROKER = os.environ.get("JARVIS_MQTT_HOST", "127.0.0.1").strip() or "127.0.0.1"
BROKER_PORT = _env_int("JARVIS_MQTT_PORT", 1883)

SEGMENT_SECONDS = max(1, _env_int("JARVIS_CLIP_SEGMENT_SECONDS", 2))
CLIP_SECONDS = max(1, _env_int("JARVIS_CLIP_SECONDS", 30))
MAX_SECONDS = max(CLIP_SECONDS, _env_int("JARVIS_CLIP_MAX_SECONDS", 90))
FPS = _env_int("JARVIS_CLIP_FPS", 30)
VIDEO_BITRATE = os.environ.get("JARVIS_CLIP_VIDEO_BITRATE", "2500k").strip() or "2500k"
VIDEO_SIZE = os.environ.get("JARVIS_CLIP_VIDEO_SIZE", "").strip()
MAX_WIDTH = _env_int("JARVIS_CLIP_MAX_WIDTH", 1280)
WITH_AUDIO = _env_flag("JARVIS_CLIP_AUDIO", True)

# Keep a little more than the longest clip anyone can ask for.
KEEP_SEGMENTS = math.ceil(MAX_SECONDS / SEGMENT_SECONDS) + 3


def ts(msg: str):
    stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{stamp}] CLIP | {msg}"
    print(line, flush=True)
    path = os.environ.get("JARVIS_TIMING_LOG")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


def _which(name: str) -> str | None:
    return shutil.which(name)


def _camera_device() -> str:
    override = os.environ.get("JARVIS_CLIP_VIDEO_DEVICE", "").strip()
    if override:
        return override
    index = os.environ.get("JARVIS_CAMERA_INDEX", "0").strip() or "0"
    if sys.platform == "darwin":
        return index
    return os.environ.get("JARVIS_CAMERA_DEVICE", "").strip() or f"/dev/video{index}"


def _audio_device() -> str:
    override = os.environ.get("JARVIS_CLIP_AUDIO_DEVICE", "").strip()
    if override:
        return override
    return "0" if sys.platform == "darwin" else "default"


# ── recorder ─────────────────────────────────────────────────────────


def _input_args(*, size: str, fps: int, audio: bool) -> list[str]:
    """Capture-device flags for this platform, most→least constrained."""
    video = _camera_device()
    if sys.platform == "darwin":
        args = ["-f", "avfoundation"]
        if fps:
            args += ["-framerate", str(fps)]
        if size:
            args += ["-video_size", size]
        spec = f"{video}:{_audio_device()}" if audio else f"{video}:none"
        args += ["-i", spec]
        return args

    args = ["-f", "v4l2"]
    if fps:
        args += ["-framerate", str(fps)]
    if size:
        args += ["-video_size", size]
    args += ["-i", video]
    if audio:
        args += ["-f", "alsa", "-i", _audio_device()]
    return args


def _encode_args(*, audio: bool) -> list[str]:
    # VideoToolbox on macOS keeps continuous recording near-free on CPU.
    if sys.platform == "darwin":
        video = [
            "-c:v", "h264_videotoolbox",
            "-realtime", "true",
            "-b:v", VIDEO_BITRATE,
        ]
    else:
        video = [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-tune", "zerolatency",
            "-b:v", VIDEO_BITRATE,
        ]
    rate = max(1, FPS or 30)
    args = [
        "-vf", f"scale='min({MAX_WIDTH},iw)':-2",
        # Capture devices report a 1,000,000 fps timebase. Without a real output
        # rate the segment muxer never reaches a cut point and the buffer stalls
        # on one endless file, so this is load-bearing rather than cosmetic.
        "-fps_mode", "cfr",
        "-r", str(rate),
        *video,
        "-pix_fmt", "yuv420p",
        # 1s keyframe interval so tail-trimming lands close to the request.
        "-g", str(rate),
    ]
    if audio:
        args += ["-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-ac", "1"]
    return args


def _segment_args() -> list[str]:
    return [
        "-f", "segment",
        "-segment_time", str(SEGMENT_SECONDS),
        "-segment_format", "mp4",
        "-reset_timestamps", "1",
        str(BUFFER_DIR / "seg-%05d.mp4"),
    ]


def _attempts() -> list[tuple[str, int, bool]]:
    """(video_size, fps, audio) combos to try, in order of preference."""
    sizes = [VIDEO_SIZE, ""] if VIDEO_SIZE else [""]
    combos: list[tuple[str, int, bool]] = []
    for audio in ([True, False] if WITH_AUDIO else [False]):
        for size in sizes:
            combos.append((size, FPS, audio))
            if FPS:
                combos.append((size, 0, audio))
    # Drop duplicates while keeping order.
    seen: set[tuple[str, int, bool]] = set()
    unique = []
    for combo in combos:
        if combo not in seen:
            seen.add(combo)
            unique.append(combo)
    return unique


class Recorder:
    """Keeps one ffmpeg segmenter alive, restarting it if the camera drops."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.audio_ok = WITH_AUDIO
        self._stop = threading.Event()
        self._log = ROOT / "logs" / "clipper-ffmpeg.log"

    def _spawn(self, size: str, fps: int, audio: bool) -> subprocess.Popen:
        ffmpeg = _which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg not found — install it (brew install ffmpeg)")
        argv = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-loglevel", "warning",
            "-y",
            *_input_args(size=size, fps=fps, audio=audio),
            *_encode_args(audio=audio),
            *_segment_args(),
        ]
        self._log.parent.mkdir(parents=True, exist_ok=True)
        log = open(self._log, "a", buffering=1)
        log.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} {' '.join(argv)}\n")
        return subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            cwd=str(ROOT),
        )

    def start(self) -> bool:
        """Try capture configs until one actually produces closed segments."""
        for size, fps, audio in _attempts():
            label = (
                f"size={size or 'device default'} "
                f"fps={fps or 'device default'} "
                f"audio={'on' if audio else 'off'}"
            )
            ts(f"starting recorder ({label})")
            try:
                proc = self._spawn(size, fps, audio)
            except RuntimeError as e:
                ts(str(e))
                return False
            # A second segment file proves the muxer is really rotating; the
            # first one exists immediately and stays open, so it proves nothing.
            deadline = time.monotonic() + SEGMENT_SECONDS * 2 + 8
            healthy = False
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                if len(list(BUFFER_DIR.glob(SEGMENT_GLOB))) >= 2:
                    healthy = True
                    break
                time.sleep(0.2)
            if healthy:
                self.proc = proc
                self.audio_ok = audio
                ts(f"recording ({label}) → {BUFFER_DIR.name}/")
                return True
            if proc.poll() is None:
                ts(f"config produced no segments — see logs/clipper-ffmpeg.log")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            else:
                ts(f"config failed (exit {proc.returncode}) — see logs/clipper-ffmpeg.log")
            for leftover in BUFFER_DIR.glob(SEGMENT_GLOB):
                leftover.unlink(missing_ok=True)
        return False

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        self._stop.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def supervise(self):
        while not self._stop.is_set():
            time.sleep(2)
            if self._stop.is_set():
                break
            if not self.alive():
                ts("recorder died — restarting in 5s")
                publish_status("idle")
                time.sleep(5)
                if self.start():
                    publish_status("recording")


# ── buffer housekeeping ──────────────────────────────────────────────


def _segments() -> list[Path]:
    return sorted(BUFFER_DIR.glob(SEGMENT_GLOB))


def prune_loop():
    while True:
        time.sleep(SEGMENT_SECONDS)
        try:
            files = _segments()
            for stale in files[:-KEEP_SEGMENTS] if len(files) > KEEP_SEGMENTS else []:
                stale.unlink(missing_ok=True)
        except OSError:
            pass


# ── clip building ────────────────────────────────────────────────────


def _probe_duration(path: Path) -> float:
    ffprobe = _which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    try:
        return float((proc.stdout or "").strip())
    except ValueError:
        return 0.0


def _wait_for_segment_close(timeout: float) -> None:
    """Block until the in-progress segment is finalized and playable."""
    before = _segments()
    if not before:
        return
    newest = before[-1].name
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        files = _segments()
        if files and files[-1].name != newest:
            return
        time.sleep(0.1)


def build_clip(seconds: float, label: str | None = None) -> tuple[Path, float]:
    """Save the last `seconds` of footage. Returns (path, actual duration).

    The buffer may hold less than asked for, so the caller is told what it
    really got rather than what it requested.
    """
    seconds = max(1.0, min(float(seconds), float(MAX_SECONDS)))
    # The segment being written has no moov atom yet; give it a moment to land
    # so the clip includes the moment the request was actually spoken.
    _wait_for_segment_close(SEGMENT_SECONDS + 1.5)

    files = _segments()
    if len(files) < 2:
        raise RuntimeError("The buffer is still warming up.")
    complete = files[:-1]  # last one is mid-write
    want = math.ceil(seconds / SEGMENT_SECONDS) + 1
    chosen = complete[-want:]

    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # The label arrives over MQTT, so it must not be able to steer the path.
    tag = re.sub(r"[^A-Za-z0-9_-]", "", label or "")[:40]
    suffix = f"-{tag}" if tag else ""
    dest = CLIPS_DIR / f"clip-{stamp}{suffix}.mp4"
    scratch = CLIPS_DIR / f".joining-{stamp}.mp4"
    listing = CLIPS_DIR / f".joining-{stamp}.txt"

    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")

    try:
        listing.write_text(
            "".join(f"file '{p.as_posix()}'\n" for p in chosen), encoding="utf-8"
        )
        join = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "concat", "-safe", "0", "-i", str(listing),
                "-c", "copy", "-movflags", "+faststart",
                str(scratch),
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        if join.returncode != 0 or not scratch.is_file():
            raise RuntimeError((join.stderr or "concat failed").strip()[:200])

        total = _probe_duration(scratch)
        if total <= seconds + 0.5 or total <= 0:
            scratch.replace(dest)
            return dest, (total if total > 0 else seconds)

        trim = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-ss", f"{max(0.0, total - seconds):.3f}",
                "-i", str(scratch),
                "-c", "copy", "-movflags", "+faststart",
                str(dest),
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        if trim.returncode != 0 or not dest.is_file():
            # Trimming is a nicety; a slightly long clip beats no clip.
            scratch.replace(dest)
            return dest, total
        return dest, _probe_duration(dest) or seconds
    finally:
        listing.unlink(missing_ok=True)
        scratch.unlink(missing_ok=True)


# ── mqtt ─────────────────────────────────────────────────────────────

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
recorder = Recorder()
_saving = threading.Lock()


def publish_status(state: str):
    mqtt_client.publish("jarvis/status/clipper", state)


def _handle_request(payload: str):
    request_id = ""
    seconds = float(CLIP_SECONDS)
    label = None
    text = (payload or "").strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
            request_id = str(data.get("id") or "")
            if data.get("seconds"):
                seconds = float(data["seconds"])
            label = data.get("label") or None
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    elif text:
        try:
            seconds = float(text)
        except ValueError:
            pass

    def fail(reason: str):
        ts(f"clip failed: {reason}")
        mqtt_client.publish(
            "jarvis/clip/error", json.dumps({"id": request_id, "error": reason})
        )

    if not recorder.alive():
        fail("The camera recorder isn't running.")
        return
    if not _saving.acquire(blocking=False):
        fail("Already saving a clip.")
        return
    try:
        publish_status("saving")
        t0 = time.perf_counter()
        dest, actual = build_clip(seconds, label)
        took = time.perf_counter() - t0
        size_mb = dest.stat().st_size / 1_048_576
        ts(f"saved {dest.name} ({actual:.1f}s, {size_mb:.1f} MB) in {took:.2f}s")
        mqtt_client.publish(
            "jarvis/clip/saved",
            json.dumps(
                {
                    "id": request_id,
                    "name": dest.name,
                    "path": str(dest),
                    "seconds": round(actual),
                    "requested": round(seconds),
                    "audio": recorder.audio_ok,
                }
            ),
        )
    except Exception as e:
        fail(str(e) or "clip failed")
    finally:
        publish_status("recording" if recorder.alive() else "idle")
        _saving.release()


def on_message(client, userdata, msg):
    if msg.topic != "jarvis/clip/save":
        return
    payload = msg.payload.decode("utf-8", errors="replace")
    ts(f"clip requested ← {payload!r}")
    threading.Thread(target=_handle_request, args=(payload,), daemon=True).start()


def on_connect(client, userdata, flags, reason_code, properties=None):
    client.subscribe("jarvis/clip/save")
    publish_status("recording" if recorder.alive() else "idle")
    ts("MQTT connected, subscribed jarvis/clip/save")


def main() -> int:
    if not _env_flag("JARVIS_CLIP_ENABLED", True):
        ts("disabled (JARVIS_CLIP_ENABLED=0) — exiting")
        return 0

    BUFFER_DIR.mkdir(parents=True, exist_ok=True)
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    # Segment numbering restarts at 0 each run, so clear stale footage.
    for old in BUFFER_DIR.glob(SEGMENT_GLOB):
        old.unlink(missing_ok=True)

    ts(
        f"buffer {MAX_SECONDS}s in {SEGMENT_SECONDS}s segments, "
        f"clip default {CLIP_SECONDS}s, audio={'on' if WITH_AUDIO else 'off'}"
    )

    mqtt_client.on_message = on_message
    mqtt_client.on_connect = on_connect
    try:
        mqtt_client.connect(BROKER, BROKER_PORT, keepalive=30)
    except OSError as e:
        ts(f"MQTT connect failed: {e}")
        return 1
    mqtt_client.loop_start()

    if not recorder.start():
        ts("could not open the camera — no configuration worked")
        publish_status("idle")
    else:
        publish_status("recording")

    threading.Thread(target=prune_loop, daemon=True).start()
    threading.Thread(target=recorder.supervise, daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        ts("shutting down...")
    finally:
        recorder.stop()
        publish_status("idle")
        mqtt_client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
