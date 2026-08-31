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


model = Model(wakeword_models=["hey_jarvis"])

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.connect("localhost", 1883, keepalive=30)
mqtt_client.loop_start()

COOLDOWN_SEC = 3.0
_last_detect = 0.0


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
    global _last_detect
    audio = (indata[:, 0] * 32767).astype(np.int16)
    t0 = time.perf_counter()
    predictions = model.predict(audio)
    infer_ms = (time.perf_counter() - t0) * 1000
    now = time.monotonic()
    for wakeword, score in predictions.items():
        if score > 0.65 and (now - _last_detect) >= COOLDOWN_SEC:
            _last_detect = now
            ts(f"detected '{wakeword}' score={score:.2f} (infer {infer_ms:.1f}ms)")
            mqtt_client.publish("jarvis/wake/detected", "detected")
            mqtt_client.publish("jarvis/tts/interrupt", "stop")
            mqtt_client.publish("jarvis/status/stt", "listening")

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
