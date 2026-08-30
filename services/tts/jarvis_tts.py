"""TTS backends for Jarvis. create_tts() picks Cartesia (default) or Kokoro."""
from __future__ import annotations

import os
import time
from pathlib import Path

import sounddevice as sd
from kokoro_onnx import Kokoro

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
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv()


class JarvisTTS:
    """Local Kokoro ONNX TTS."""

    def __init__(
        self,
        model_path="models/kokoro-v1.0.fp16.onnx",
        voices_path="models/voices-v1.0.bin",
        voice="bm_george",
        speed=1.25,
        lang="en-gb",
    ):
        self.tts = Kokoro(
            model_path=model_path,
            voices_path=voices_path,
        )
        self.voice = voice
        self.speed = speed
        self.lang = lang

    def interrupt(self):
        try:
            sd.stop()
        except Exception:
            pass
        try:
            shared = Path(__file__).resolve().parents[1] / "sonos"
            import sys

            if str(shared) not in sys.path:
                sys.path.insert(0, str(shared))
            import sonos_client

            if sonos_client.tts_enabled():
                sonos_client.interrupt()
        except Exception:
            pass

    def say(self, text: str):
        """Synthesize and play. Returns (synth_seconds, play_seconds)."""
        t0 = time.perf_counter()
        samples, sample_rate = self.tts.create(
            text,
            voice=self.voice,
            speed=self.speed,
            lang=self.lang,
            trim=False,
        )
        synth_s = time.perf_counter() - t0

        t1 = time.perf_counter()
        played_sonos = False
        try:
            shared = Path(__file__).resolve().parents[1] / "sonos"
            import sys

            if str(shared) not in sys.path:
                sys.path.insert(0, str(shared))
            import sonos_client

            if sonos_client.tts_enabled():
                sonos_client.play_float32(samples, sample_rate)
                played_sonos = True
        except Exception:
            played_sonos = False
        if not played_sonos:
            sd.play(samples, sample_rate)
            sd.wait()
        play_s = time.perf_counter() - t1
        return synth_s, play_s


def provider_name() -> str:
    return os.environ.get("JARVIS_TTS_PROVIDER", "cartesia").strip().lower()


def create_tts():
    """Return a TTS engine with .say(text) -> (synth_s, play_s) and .interrupt()."""
    p = provider_name()
    if p == "cartesia":
        from cartesia_tts import CartesiaTTS

        return CartesiaTTS()
    if p == "kokoro":
        voice = os.environ.get("KOKORO_VOICE", "bm_lewis")
        return JarvisTTS(voice=voice)
    raise ValueError(
        f"Unknown JARVIS_TTS_PROVIDER={p!r} (expected 'cartesia' or 'kokoro')"
    )
