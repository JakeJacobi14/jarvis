import numpy as np
import sounddevice as sd
from pynput import keyboard
from faster_whisper import WhisperModel
import threading
import paho.mqtt.client as mqtt

# create the client and connect to the broker
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.connect("localhost", 1883, keepalive=30)
mqtt_client.loop_start()

# Class for encapsulation and being able to actively listen while another one is transcribing
class PushToTalkRecorder:
    def __init__(self, sample_rate=16000):
        self.sample_rate = sample_rate
        # the array of 
        self._frames = []
        self._recording = False
        
        # Input stream to stream audio
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            callback=self.audio_callback,
        )
        self._stream.start()

    # start recording, reset the frames array
    def start(self):
        if not self._recording:
            self._frames = []
            self._recording = True
            print("\n[Recording...]", end="", flush=True)

    # function called by the input stream when it returns a sample
    def audio_callback(self, indata, frames, time, status):
        # if there's any error, print it
        if status:
            print(f"\nAudio error: {status}")
        # append the audio sample to the frames array
        if self._recording:
            # .copy() ensures sounddevice's memory buffer isn't overwritten
            self._frames.append(indata.copy())

    # stop recording
    def stop(self):
        if not self._recording:
            return np.array([], dtype=np.float32)
        self._recording = False
        print(" [Stopped]")
        # If there's any recorded audio, merge all the audio clips into one array 
        if self._frames:
            return np.concatenate(self._frames, axis=0).flatten()
        return np.array([], dtype=np.float32)

# Load the whisper model
print("Loading Whisper model...")
model = WhisperModel("base.en", device="cpu", compute_type="int8")
recorder = PushToTalkRecorder()

# state guard
is_space_pressed = False

def transcribe_async(audio):
    if len(audio) == 0:
        return
    print("Transcribing...")
    # throw away the "info" part of the return value
    segments, _ = model.transcribe(audio, language="en", beam_size=1)
    full_text = " ".join(seg.text.strip() for seg in segments).strip()
    if full_text:
        print(f"Transcribed: {full_text}")
        # publish the text to the broker
        mqtt_client.publish("jarvis/stt/text", full_text)

# If the user presses the key, start listening
def on_press(key):
    # tell python that is_space_pressed is the global version
    global is_space_pressed
    if key == keyboard.Key.space and not is_space_pressed:
        is_space_pressed = True
        recorder.start()

# When they let go of the key, send the audio to transcribe_async
def on_release(key):
    # tell python that is_space_pressed is the global version
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