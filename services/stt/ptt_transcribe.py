import numpy as np
import sounddevice as sd
from pynput import keyboard
from faster_whisper import WhisperModel
import threading

class PushToTalkRecorder:
    def __init__(self, sample_rate=16000):
        self.sample_rate = sample_rate
        self._frames = []
        self._recording = False
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            callback=self.audio_callback,
        )
        self._stream.start()

    def start(self):
        if not self._recording:
            self._frames = []
            self._recording = True
            print("\n[Recording...]", end="", flush=True)

    def audio_callback(self, indata, frames, time, status):
        if status:
            print(f"\nAudio error: {status}")
        if self._recording:
            # .copy() ensures sounddevice's memory buffer isn't overwritten
            self._frames.append(indata.copy())

    def stop(self):
        if not self._recording:
            return np.array([], dtype=np.float32)
        self._recording = False
        print(" [Stopped]")
        if self._frames:
            return np.concatenate(self._frames, axis=0).flatten()
        return np.array([], dtype=np.float32)

# Load Whisper Model
print("Loading Whisper model...")
model = WhisperModel("base.en", device="cpu", compute_type="int8")
recorder = PushToTalkRecorder()

# Track space key state to guard against OS key auto-repeat
is_space_pressed = False

def transcribe_async(audio):
    if len(audio) == 0:
        return
    print("Transcribing...")
    segments, _ = model.transcribe(audio, language="en", beam_size=1)
    for seg in segments:
        print(f"[{seg.start:.1f}s -> {seg.end:.1f}s] {seg.text}")

def on_press(key):
    global is_space_pressed
    if key == keyboard.Key.space and not is_space_pressed:
        is_space_pressed = True
        recorder.start()

def on_release(key):
    global is_space_pressed
    if key == keyboard.Key.space:
        is_space_pressed = False
        audio = recorder.stop()
        # Transcribe in a background thread so listener remains responsive
        threading.Thread(target=transcribe_async, args=(audio,), daemon=True).start()

    if key == keyboard.Key.esc:
        return False

print("Ready. Hold SPACE to talk, release to stop. Esc to quit.")
with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    listener.join()