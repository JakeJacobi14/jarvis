import sounddevice as sd
from faster_whisper import WhisperModel

print("Loading model (first run downloads it, ~140MB)...")
model = WhisperModel("base.en", device="cpu", compute_type="int8")

print("Recording 3 seconds... talk now")
audio = sd.rec(int(3 * 16000), samplerate=16000, channels=1, dtype="float32")
sd.wait()
audio = audio.flatten()  # faster-whisper wants a 1D array, sd.rec gives 2D

print("Transcribing...")
segments, info = model.transcribe(audio, language="en", beam_size=1)
for seg in segments:
    print(f"[{seg.start:.1f}s -> {seg.end:.1f}s] {seg.text}")
