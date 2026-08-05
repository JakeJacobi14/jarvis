from openwakeword.model import Model
import sounddevice as sd
import numpy as np

# Downloads the model on first run
model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
print("Listening for 'Hey Jarvis'... (Ctrl+C to stop)")

def callback(indata, frames, time_info, status):
    audio = (indata[:, 0] * 32767).astype(np.int16)  # openwakeword expects int16
    predictions = model.predict(audio)
    for wakeword, score in predictions.items():
        if score > 0.5:
            print(f"Detected: {wakeword} (score: {score:.2f})")

with sd.InputStream(samplerate=16000, channels=1, dtype="float32", callback=callback, blocksize=1280):
    while True:
        sd.sleep(100)