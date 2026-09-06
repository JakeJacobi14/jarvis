"""Cartesia Ink-2 streaming STT (auto turn detection)."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
from cartesia import Cartesia

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


def _float_to_pcm16(indata: np.ndarray) -> bytes:
    samples = np.asarray(indata, dtype=np.float32).reshape(-1)
    samples = np.clip(samples, -1.0, 1.0)
    return (samples * 32767.0).astype(np.int16).tobytes()


class CartesiaSTT:
    """Stream mic audio to Ink-2 until a user turn ends."""

    def __init__(self):
        api_key = os.environ.get("CARTESIA_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "CARTESIA_API_KEY is not set. Add it to .env (see .env.example)."
            )
        self.client = Cartesia(api_key=api_key)
        self.model = os.environ.get("CARTESIA_STT_MODEL", "ink-2")
        self.sample_rate = int(os.environ.get("CARTESIA_STT_SAMPLE_RATE", "16000"))
        # ~100ms blocks
        self.blocksize = max(320, self.sample_rate // 10)
        self.turn_kwargs = {}
        self._cancel = threading.Event()
        self._session = 0
        for env_key, arg in (
            ("CARTESIA_STT_START_THRESHOLD", "turn_start_threshold"),
            ("CARTESIA_STT_EAGER_END_THRESHOLD", "turn_eager_end_threshold"),
            ("CARTESIA_STT_END_THRESHOLD", "turn_end_threshold"),
            ("CARTESIA_STT_END_TIMEOUT_MS", "turn_end_timeout_ms"),
        ):
            raw = os.environ.get(env_key, "").strip()
            if raw:
                self.turn_kwargs[arg] = float(raw)

    def cancel(self):
        """Abort an in-progress listen_turn / listen_ptt (phone / barge-in)."""
        self._session += 1
        self._cancel.set()

    def listen_turn(
        self,
        *,
        idle_timeout: float | None = None,
        max_seconds: float = 15.0,
        on_speech_start=None,
    ) -> str | None:
        """
        Stream the mic until Ink emits turn.end.

        idle_timeout: if set, give up when no turn.start within that many seconds
                      (used for post-TTS follow-up windows).
        """
        transcript: str | None = None
        done = threading.Event()
        speech_started = threading.Event()
        stop_send = threading.Event()
        error_box: list[BaseException] = []
        self._cancel.clear()
        session = self._session

        with self.client.stt.auto_finalize.websocket(
            model=self.model,
            encoding="pcm_s16le",
            sample_rate=self.sample_rate,
            **self.turn_kwargs,
        ) as conn:

            def recv_loop():
                nonlocal transcript
                try:
                    for event in conn:
                        et = getattr(event, "type", None)
                        if et == "turn.start":
                            speech_started.set()
                            if on_speech_start:
                                try:
                                    on_speech_start()
                                except Exception:
                                    pass
                        elif et == "turn.end":
                            # Cumulative final transcript for the turn — use as-is.
                            transcript = getattr(event, "transcript", None) or ""
                            done.set()
                            stop_send.set()
                            break
                        elif et == "error":
                            msg = getattr(event, "message", None) or "ink-2 error"
                            error_box.append(RuntimeError(msg))
                            done.set()
                            stop_send.set()
                            break
                except Exception as e:
                    error_box.append(e)
                    done.set()
                    stop_send.set()

            recv_thread = threading.Thread(target=recv_loop, daemon=True)
            recv_thread.start()

            def audio_cb(indata, frames, time_info, status):
                if stop_send.is_set():
                    return
                if status:
                    # Non-fatal device glitches — keep going.
                    pass
                try:
                    conn.send_raw(_float_to_pcm16(indata))
                except Exception:
                    stop_send.set()

            t0 = time.monotonic()
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self.blocksize,
                callback=audio_cb,
            ):
                while not done.wait(0.05):
                    if self._cancel.is_set() or self._session != session:
                        break
                    elapsed = time.monotonic() - t0
                    if (
                        idle_timeout is not None
                        and not speech_started.is_set()
                        and elapsed >= idle_timeout
                    ):
                        break
                    if elapsed >= max_seconds:
                        break

                stop_send.set()
                try:
                    conn.send({"type": "close"})
                except Exception:
                    pass
                # Drain a final turn.end after close, if any.
                done.wait(2.0)

            recv_thread.join(timeout=1.0)

        if error_box:
            raise error_box[0]
        if transcript is None:
            return None
        # Do not strip — Cartesia docs say join/use verbatim; trim only ends for MQTT.
        text = transcript.strip()
        return text or None

    def listen_ptt(self, stop_event: threading.Event) -> str | None:
        """Stream while stop_event is clear; on set, close and return turn transcript."""
        transcript: str | None = None
        done = threading.Event()
        stop_send = threading.Event()
        error_box: list[BaseException] = []
        self._cancel.clear()

        with self.client.stt.auto_finalize.websocket(
            model=self.model,
            encoding="pcm_s16le",
            sample_rate=self.sample_rate,
            **self.turn_kwargs,
        ) as conn:

            def recv_loop():
                nonlocal transcript
                try:
                    for event in conn:
                        et = getattr(event, "type", None)
                        if et == "turn.end":
                            transcript = getattr(event, "transcript", None) or ""
                            done.set()
                            break
                        elif et == "error":
                            msg = getattr(event, "message", None) or "ink-2 error"
                            error_box.append(RuntimeError(msg))
                            done.set()
                            break
                except Exception as e:
                    error_box.append(e)
                    done.set()

            recv_thread = threading.Thread(target=recv_loop, daemon=True)
            recv_thread.start()

            def audio_cb(indata, frames, time_info, status):
                if stop_send.is_set():
                    return
                try:
                    conn.send_raw(_float_to_pcm16(indata))
                except Exception:
                    stop_send.set()

            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self.blocksize,
                callback=audio_cb,
            ):
                while not stop_event.wait(0.05):
                    if done.is_set() or self._cancel.is_set():
                        break
                stop_send.set()
                try:
                    conn.send({"type": "close"})
                except Exception:
                    pass
                done.wait(2.0)

            recv_thread.join(timeout=1.0)

        if error_box:
            raise error_box[0]
        if transcript is None:
            return None
        text = transcript.strip()
        return text or None
