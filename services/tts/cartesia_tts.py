"""Cartesia Sonic TTS — streams PCM, plays locally or through Sonos."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
from cartesia import Cartesia


def _sonos():
    shared = Path(__file__).resolve().parents[1] / "sonos"
    if str(shared) not in sys.path:
        sys.path.insert(0, str(shared))
    import sonos_client

    return sonos_client

# Archie — en-GB male (closest to prior Kokoro bm_lewis vibe)
DEFAULT_VOICE_ID = "ef191366-f52f-447a-a398-ed8c0f2943a1"


class CartesiaTTS:
    def __init__(self):
        api_key = os.environ.get("CARTESIA_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "CARTESIA_API_KEY is not set. Add it to .env (see .env.example)."
            )
        self.client = Cartesia(api_key=api_key)
        self.model_id = os.environ.get("CARTESIA_TTS_MODEL", "sonic-3.5")
        self.voice_id = os.environ.get("CARTESIA_VOICE_ID", DEFAULT_VOICE_ID)
        self.sample_rate = int(os.environ.get("CARTESIA_SAMPLE_RATE", "44100"))
        self.language = os.environ.get("CARTESIA_LANGUAGE", "en")
        # 1.0 = natural; up to 1.5. Mild bump shortens playtime without sounding rushed.
        self.speed = float(os.environ.get("CARTESIA_SPEED", "1.15"))
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._ws_cm = None
        self._ws = None

    def interrupt(self):
        self._stop.set()
        try:
            sd.stop()
        except Exception:
            pass
        try:
            client = _sonos()
            if client.tts_enabled():
                client.interrupt()
        except Exception:
            pass

    def _ensure_ws(self):
        if self._ws is not None:
            return self._ws
        self._ws_cm = self.client.tts.websocket_connect()
        self._ws = self._ws_cm.__enter__()
        return self._ws

    def _drop_ws(self):
        cm = self._ws_cm
        self._ws = None
        self._ws_cm = None
        if cm is None:
            return
        try:
            cm.__exit__(None, None, None)
        except Exception:
            pass

    def say(self, text: str):
        """Stream synth → speakers. Returns (ttfb_seconds, play_seconds)."""
        text = (text or "").strip()
        if not text:
            return 0.0, 0.0

        self._stop.clear()
        t0 = time.perf_counter()
        first_audio_at: float | None = None
        gen_cfg = {"speed": self.speed} if self.speed and self.speed != 1.0 else None

        last_err: Exception | None = None
        with self._lock:
            for attempt in range(2):
                try:
                    ws = self._ensure_ws()
                    ctx_kwargs = dict(
                        model_id=self.model_id,
                        voice={"mode": "id", "id": self.voice_id},
                        output_format={
                            "container": "raw",
                            "encoding": "pcm_f32le",
                            "sample_rate": self.sample_rate,
                        },
                        language=self.language,
                    )
                    if gen_cfg:
                        ctx_kwargs["generation_config"] = gen_cfg
                    ctx = ws.context(**ctx_kwargs)
                    ctx.push(text)
                    ctx.no_more_inputs()

                    chunks: list[np.ndarray] = []
                    sonos = None
                    want_sonos = False
                    try:
                        client = _sonos()
                        want_sonos = client.tts_mode() in {"sonos", "overlay"}
                        if client.tts_enabled():
                            sonos = client
                    except Exception:
                        sonos = None
                    if want_sonos and sonos is None:
                        raise RuntimeError(
                            "Sonos speech is required but no speaker was found"
                        )

                    def _ingest(audio_bytes: bytes):
                        nonlocal first_audio_at
                        samples = np.frombuffer(audio_bytes, dtype=np.float32)
                        if not samples.size:
                            return
                        if first_audio_at is None:
                            first_audio_at = time.perf_counter()
                        if sonos is None:
                            stream.write(samples.reshape(-1, 1))
                        else:
                            chunks.append(samples.copy())

                    if sonos is None:
                        with sd.OutputStream(
                            samplerate=self.sample_rate,
                            channels=1,
                            dtype="float32",
                        ) as stream:
                            for response in ctx.receive():
                                if self._stop.is_set():
                                    break
                                rtype = getattr(response, "type", None)
                                if rtype == "chunk":
                                    audio_bytes = getattr(response, "audio", None)
                                    if audio_bytes:
                                        _ingest(audio_bytes)
                                elif rtype == "error":
                                    msg = (
                                        getattr(response, "message", None)
                                        or getattr(response, "title", None)
                                        or "cartesia error"
                                    )
                                    raise RuntimeError(msg)
                                elif rtype == "done":
                                    break
                    else:
                        for response in ctx.receive():
                            if self._stop.is_set():
                                break
                            rtype = getattr(response, "type", None)
                            if rtype == "chunk":
                                audio_bytes = getattr(response, "audio", None)
                                if audio_bytes:
                                    _ingest(audio_bytes)
                            elif rtype == "error":
                                msg = (
                                    getattr(response, "message", None)
                                    or getattr(response, "title", None)
                                    or "cartesia error"
                                )
                                raise RuntimeError(msg)
                            elif rtype == "done":
                                break
                        if chunks and not self._stop.is_set():
                            sonos.play_float32(np.concatenate(chunks), self.sample_rate)
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    self._drop_ws()
                    idle = "idle timeout" in str(e).lower() or "1000" in str(e)
                    if attempt == 0 and idle and not self._stop.is_set():
                        continue
                    raise
            if last_err is not None:
                raise last_err

        t_end = time.perf_counter()
        ttfb = (first_audio_at or t_end) - t0
        play = t_end - (first_audio_at or t0)
        return ttfb, play
