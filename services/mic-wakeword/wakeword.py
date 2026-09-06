from datetime import datetime
from openwakeword.model import Model
import sounddevice as sd
import numpy as np
import paho.mqtt.client as mqtt
import time
import os
import sys


def resolve_mic_device():
    """Index or name substring from JARVIS_MIC_DEVICE; else OS default input."""
    raw = os.environ.get("JARVIS_MIC_DEVICE", "").strip()
    devices = sd.query_devices()
    if not raw:
        idx = sd.default.device[0]
        return None if idx is None or int(idx) < 0 else int(idx)
    try:
        idx = int(raw)
        info = devices[idx]
        if info["max_input_channels"] < 1:
            raise SystemExit(f"JARVIS_MIC_DEVICE={idx} has no inputs: {info['name']}")
        return idx
    except ValueError:
        needle = raw.lower()
        hits = [
            i
            for i, d in enumerate(devices)
            if d["max_input_channels"] > 0 and needle in d["name"].lower()
        ]
        if not hits:
            raise SystemExit(
                f"JARVIS_MIC_DEVICE={raw!r} not found. "
                "Run: services/mic-wakeword/.venv/bin/python -m sounddevice"
            )
        if len(hits) > 1:
            names = ", ".join(f"{i}:{devices[i]['name']}" for i in hits)
            raise SystemExit(f"JARVIS_MIC_DEVICE={raw!r} matches more than one device ({names})")
        return hits[0]


# Linux wheels often lack tflite-runtime; ONNX works on Mac + Ubuntu.
model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.connect("localhost", 1883, keepalive=30)
mqtt_client.loop_start()

# Music in the room knocks scores down; 0.65 was missing real wakes.
COOLDOWN_SEC = float(os.environ.get("JARVIS_WAKE_COOLDOWN", "2.0") or "2.0")
THRESHOLD = float(os.environ.get("JARVIS_WAKE_THRESHOLD", "0.48") or "0.48")
_last_detect = 0.0
_last_near = 0.0
_tts_speaking = False
_tts_guard_until = 0.0


def _on_tts_status(_client, _userdata, msg):
    global _tts_speaking, _tts_guard_until
    speaking = (msg.payload or b"").decode().strip().lower() == "speaking"
    if _tts_speaking and not speaking:
        # Tail of the clip still leaks into the mic after MQTT goes idle.
        _tts_guard_until = time.monotonic() + 2.0
    _tts_speaking = speaking


mqtt_client.message_callback_add("jarvis/status/tts", _on_tts_status)
mqtt_client.subscribe("jarvis/status/tts")


def ts(msg: str):
    stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{stamp}] WAKE | {msg}"
    print(line, flush=True)
    path = os.environ.get("JARVIS_TIMING_LOG")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


_mic = resolve_mic_device()
if _mic is not None:
    sd.default.device = (_mic, sd.default.device[1])
    ts(f"mic {_mic}: {sd.query_devices(_mic)['name']}")
else:
    ts("mic: OS default input")
ts("Listening for 'Hey Jarvis'...")

def callback(indata, frames, time_info, status):
    global _last_detect, _last_near
    try:
        audio = (indata[:, 0] * 32767).astype(np.int16)
        t0 = time.perf_counter()
        predictions = model.predict(audio)
        infer_ms = (time.perf_counter() - t0) * 1000
        now = time.monotonic()
        blocked = _tts_speaking or now < _tts_guard_until
        for wakeword, score in predictions.items():
            if blocked:
                if score > THRESHOLD and (now - _last_near) >= 4.0:
                    _last_near = now
                    ts(f"ignored '{wakeword}' score={score:.2f} (he's talking)")
                continue
            if score > THRESHOLD and (now - _last_detect) >= COOLDOWN_SEC:
                _last_detect = now
                ts(f"detected '{wakeword}' score={score:.2f} (infer {infer_ms:.1f}ms)")
                mqtt_client.publish("jarvis/wake/detected", "detected")
                mqtt_client.publish("jarvis/tts/interrupt", "stop")
                mqtt_client.publish("jarvis/status/stt", "listening")
            elif score > THRESHOLD * 0.75 and (now - _last_near) >= 15.0:
                _last_near = now
                ts(f"near-miss '{wakeword}' score={score:.2f} (need {THRESHOLD:.2f})")
    except Exception as e:
        ts(f"callback error: {e}")

_stream_kw = dict(
    samplerate=16000,
    channels=1,
    dtype="float32",
    callback=callback,
    blocksize=1280,
    latency="high",
)
if sys.platform == "win32" and hasattr(sd, "WasapiSettings"):
    _stream_kw["extra_settings"] = sd.WasapiSettings(exclusive=False)

with sd.InputStream(**_stream_kw):
    while True:
        sd.sleep(100)
