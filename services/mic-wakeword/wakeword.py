from datetime import datetime
from openwakeword.model import Model
import sounddevice as sd
import numpy as np
import paho.mqtt.client as mqtt
import time
import os

model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")

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

with sd.InputStream(samplerate=16000, channels=1, dtype="float32", callback=callback, blocksize=1280):
    while True:
        sd.sleep(100)
