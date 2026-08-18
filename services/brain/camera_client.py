"""One-shot camera capture + optional Groq vision description."""
from __future__ import annotations

import base64
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
SNAP_SCRIPT = ROOT / "services" / "camera" / "snap.py"
CAPTURE_DIR = ROOT / "captures"

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class CameraError(RuntimeError):
    pass


def capture_path() -> Path:
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = CAPTURE_DIR / f"snap-{stamp}.jpg"
    env = os.environ.copy()
    try:
        proc = subprocess.run(
            [sys.executable, str(SNAP_SCRIPT), str(dest)],
            check=False,
            capture_output=True,
            text=True,
            timeout=25,
            env=env,
        )
    except subprocess.TimeoutExpired as e:
        raise CameraError("Camera timed out.") from e
    if proc.returncode != 0 or not dest.is_file():
        err = (proc.stderr or proc.stdout or "camera failed").strip()
        raise CameraError(err)
    return dest


def describe(path: Path) -> str | None:
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        return None
    model = os.environ.get(
        "GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"
    )
    raw = path.read_bytes()
    if len(raw) > 4_000_000:
        return None
    b64 = base64.standard_b64encode(raw).decode("ascii")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Describe this photo in ONE short spoken sentence "
                            "for a voice assistant. Plain ASCII. No lists."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ],
            }
        ],
        "max_completion_tokens": 60,
        "temperature": 0.4,
    }
    try:
        r = requests.post(
            os.environ.get("GROQ_BASE_URL", GROQ_URL),
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        text = (
            ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
            or ""
        ).strip()
        return text or None
    except requests.RequestException:
        return None


def snap_and_maybe_describe(describe_scene: bool = False) -> str:
    try:
        path = capture_path()
    except CameraError as e:
        msg = str(e)
        if "No camera tool" in msg or "Could not open" in msg:
            return "I couldn't reach the camera."
        return "The camera didn't cooperate."

    if describe_scene:
        seen = describe(path)
        if seen:
            return seen
        return "I took the photo but couldn't describe it."
    seen = describe(path)
    if seen:
        return f"Got it. {seen}"
    return "Photo saved."
