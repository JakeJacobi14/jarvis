#!/usr/bin/env python3
"""Take one still from the default camera. No third-party imports required.

Tries, in order: the clipper's rolling buffer (when it already holds the
camera), imagesnap (macOS), fswebcam (Linux), ffmpeg, then OpenCV if it
happens to be installed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def _camera_index() -> str:
    return os.environ.get("JARVIS_CAMERA_INDEX", "0").strip() or "0"


def _device() -> str:
    override = os.environ.get("JARVIS_CAMERA_DEVICE", "").strip()
    if override:
        return override
    if sys.platform == "darwin":
        return _camera_index()
    return f"/dev/video{_camera_index()}"


def _run(argv: list[str]) -> bool:
    try:
        proc = subprocess.run(
            argv, check=False, capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def dated_stamp() -> str:
    """Aug-30-2026-9-43pm — same scheme as clips."""
    when = datetime.now()
    hour = when.hour % 12 or 12
    ampm = "am" if when.hour < 12 else "pm"
    return (
        f"{_MONTHS[when.month - 1]}-{when.day}-{when.year}-"
        f"{hour}-{when.minute:02d}{ampm}"
    )


def unique_media_path(folder: Path, prefix: str, ext: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{prefix}-{dated_stamp()}"
    dest = folder / f"{stem}.{ext}"
    n = 2
    while dest.exists():
        dest = folder / f"{stem}-{n}.{ext}"
        n += 1
    return dest


CLIP_BUFFER = Path(__file__).resolve().parents[1] / "clipper" / ".clipbuf"


def _live_buffer_segment() -> Path | None:
    """Newest finished segment, only while the clipper is actively recording."""
    if not CLIP_BUFFER.is_dir():
        return None
    segments = sorted(CLIP_BUFFER.glob("seg-*.mp4"))
    if len(segments) < 2:
        return None
    try:
        # Stale leftovers from a dead ffmpeg must not count as "live".
        if time.time() - segments[-1].stat().st_mtime > 4:
            return None
    except OSError:
        return None
    return segments[-2]


def _from_buffer(dest: Path) -> bool:
    """Pull a frame out of the rolling clip buffer instead of the device.

    The clipper holds the camera continuously, so opening it a second time
    would either fail or fight for the device.
    """
    segment = _live_buffer_segment()
    ffmpeg = shutil.which("ffmpeg")
    if segment is None or not ffmpeg:
        return False
    base = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    tail = ["-frames:v", "1", "-q:v", "2", str(dest)]
    # Prefer the freshest frame in the segment, else settle for the first.
    for seek in (["-sseof", "-0.4"], []):
        if _run([*base, *seek, "-i", str(segment), *tail]) and dest.is_file():
            return True
    return False


def capture(dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    device = _device()

    if _from_buffer(dest):
        return

    if sys.platform == "darwin" and shutil.which("imagesnap"):
        if _run(["imagesnap", "-w", "0.8", str(dest)]) and dest.is_file():
            return

    if sys.platform != "darwin" and shutil.which("fswebcam"):
        if (
            _run(
                [
                    "fswebcam",
                    "-d",
                    device,
                    "-r",
                    "1280x720",
                    "--no-banner",
                    "-S",
                    "3",
                    str(dest),
                ]
            )
            and dest.is_file()
        ):
            return

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        if sys.platform == "darwin":
            argv = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "avfoundation",
                "-framerate",
                "30",
                "-i",
                f"{_camera_index()}:none",
                "-frames:v",
                "1",
                "-vf",
                "scale='min(1280,iw)':-1",
                str(dest),
            ]
        else:
            argv = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "v4l2",
                "-i",
                device,
                "-frames:v",
                "1",
                "-vf",
                "scale='min(1280,iw)':-1",
                str(dest),
            ]
        if _run(argv) and dest.is_file():
            return

    try:
        import cv2  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "No camera tool found. Install ffmpeg, or imagesnap (macOS), "
            "or fswebcam (Linux)."
        ) from e

    idx = int(_camera_index())
    cap = cv2.VideoCapture(idx)
    if not cap.isOpened():
        cap.release()
        raise RuntimeError(f"Could not open camera index {idx}.")
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        raise RuntimeError("Camera opened but returned no frame.")
    if not cv2.imwrite(str(dest), frame):
        raise RuntimeError("Failed to write snapshot.")
    if not dest.is_file():
        raise RuntimeError("Snapshot was not written.")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: snap.py OUTPUT.jpg", file=sys.stderr)
        return 2
    dest = Path(sys.argv[1]).expanduser()
    try:
        capture(dest)
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    print(str(dest.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
