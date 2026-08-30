"""Speaker volume. Sonos Amp when present; else macOS osascript or Linux pactl/wpctl."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


class VolumeError(RuntimeError):
    pass


def _sonos():
    shared = Path(__file__).resolve().parents[1] / "sonos"
    if str(shared) not in sys.path:
        sys.path.insert(0, str(shared))
    import sonos_client

    return sonos_client


def _use_sonos() -> bool:
    try:
        return _sonos().configured()
    except Exception:
        return False


def _run(argv: list[str]) -> str:
    try:
        proc = subprocess.run(
            argv, check=False, capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise VolumeError(f"volume command failed: {e}") from e
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise VolumeError(err)
    return (proc.stdout or "").strip()


def _darwin_get() -> int:
    out = _run(
        ["osascript", "-e", "output volume of (get volume settings)"]
    )
    try:
        return max(0, min(100, int(float(out))))
    except ValueError as e:
        raise VolumeError(f"unexpected volume: {out!r}") from e


def _darwin_muted() -> bool:
    out = _run(["osascript", "-e", "output muted of (get volume settings)"])
    return out.strip().lower() == "true"


def _darwin_set(pct: int) -> None:
    _run(["osascript", "-e", f"set volume output volume {int(pct)}"])


def _darwin_set_muted(muted: bool) -> None:
    flag = "true" if muted else "false"
    _run(["osascript", "-e", f"set volume output muted {flag}"])


def _linux_backend() -> str:
    forced = os.environ.get("JARVIS_VOLUME_BACKEND", "").strip().lower()
    if forced:
        return forced
    if shutil.which("pactl"):
        return "pactl"
    if shutil.which("wpctl"):
        return "wpctl"
    raise VolumeError(
        "No volume tool found. Install pulseaudio-utils (pactl) or wireplumber (wpctl)."
    )


def _pactl_get() -> int:
    out = _run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
    found = re.findall(r"(\d+)%", out)
    if not found:
        raise VolumeError(f"could not parse pactl volume: {out!r}")
    return max(0, min(100, int(found[0])))


def _pactl_muted() -> bool:
    out = _run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
    return "yes" in out.lower()


def _wpctl_get() -> tuple[int, bool]:
    out = _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
    m = re.search(r"Volume:\s*([0-9.]+)", out)
    if not m:
        raise VolumeError(f"could not parse wpctl volume: {out!r}")
    pct = int(round(float(m.group(1)) * 100))
    muted = "[MUTED]" in out.upper()
    return max(0, min(100, pct)), muted


def get_percent() -> int:
    if _use_sonos():
        try:
            return _sonos().get_volume()
        except Exception as e:
            raise VolumeError(str(e)) from e
    if sys.platform == "darwin":
        return _darwin_get()
    backend = _linux_backend()
    if backend == "pactl":
        return _pactl_get()
    pct, _ = _wpctl_get()
    return pct


def is_muted() -> bool:
    if _use_sonos():
        try:
            return _sonos().is_muted()
        except Exception as e:
            raise VolumeError(str(e)) from e
    if sys.platform == "darwin":
        return _darwin_muted()
    backend = _linux_backend()
    if backend == "pactl":
        return _pactl_muted()
    _, muted = _wpctl_get()
    return muted


def set_percent(pct: int) -> str:
    pct = max(0, min(100, int(pct)))
    try:
        if _use_sonos():
            try:
                _sonos().set_volume(pct)
            except Exception as e:
                raise VolumeError(str(e)) from e
            return f"Volume is {pct} percent."
        if sys.platform == "darwin":
            _darwin_set(pct)
            if pct > 0:
                _darwin_set_muted(False)
        else:
            backend = _linux_backend()
            if backend == "pactl":
                _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"])
                if pct > 0:
                    _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"])
            else:
                _run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{pct / 100:.2f}"])
                if pct > 0:
                    _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"])
    except VolumeError:
        return "I couldn't change the volume."
    return f"Volume is {pct} percent."


def nudge(delta: int) -> str:
    try:
        current = get_percent()
    except VolumeError:
        return "I couldn't read the volume."
    return set_percent(current + delta)


def set_muted(muted: bool) -> str:
    try:
        if _use_sonos():
            try:
                _sonos().set_muted(muted)
            except Exception as e:
                raise VolumeError(str(e)) from e
            return "Muted." if muted else "Unmuted."
        if sys.platform == "darwin":
            _darwin_set_muted(muted)
        else:
            backend = _linux_backend()
            flag = "1" if muted else "0"
            if backend == "pactl":
                _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", flag])
            else:
                _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", flag])
    except VolumeError:
        return "I couldn't change mute."
    return "Muted." if muted else "Unmuted."


def status_reply() -> str:
    try:
        pct = get_percent()
        if is_muted():
            return f"Muted, otherwise {pct} percent."
        return f"Volume is {pct} percent."
    except VolumeError:
        return "I couldn't read the volume."
