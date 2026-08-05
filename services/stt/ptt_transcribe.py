import numpy as np
import sounddevice as sd
from pynput import keyboard
from faster_whisper import WhisperModel
import threading
import paho.mqtt.client as mqtt
import time

# VAD
SILENCE_THRESHOLD = 0.01 # volume cuttoff for "silence"
SILENCE_DURATION = 0.7 # how long silence is needed to end the listening
MAX_RECORD_SECONDS = 10.0 # max time to record for

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
    segments, _ = model.transcribe(audio, language="en", beam_size=1)
    full_text = " ".join(seg.text.strip() for seg in segments).strip()
    if not is_real_utterance(full_text):
        print(f"Ignoring non-speech transcript: {full_text!r}")
        return
    print(f"Transcribed: {full_text}")
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

_wake_busy = False

def _record_after_wake():
    global _wake_busy
    try:
        print("Wake word triggered! Listening until you finish speaking...")
        recorder.start()

        heard_speech = False
        silence_started_at = None
        started_at = time.monotonic()

        while True:
            time.sleep(0.2)  # check for end of speech 5 times a second

            # nothing captured yet
            if not recorder._frames:
                continue

            loudness = chunk_loudness(recorder._frames[-1])
            now = time.monotonic()

            if loudness >= SILENCE_THRESHOLD:
                heard_speech = True
                silence_started_at = None # reset the timestamp of silence
            elif heard_speech:
                # we already heard talking, and now it's quiet
                if silence_started_at is None:
                    silence_started_at = now
                elif (now - silence_started_at) >= SILENCE_DURATION:
                    print(" Silence detected, stopping.")
                    break

            # max recording length check
            if (now - started_at) >= MAX_RECORD_SECONDS:
                print(" Max record time reached, stopping.")
                break
        if not heard_speech:
            print("No speech heard, ignoring.")
            recorder.stop()
            return

        audio = recorder.stop()
        transcribe_async(audio)
    finally:
        _wake_busy = False

# check if the input is a real sentence or just emptiness
def is_real_utterance(text: str) -> bool:
    cleaned = "".join(ch for ch in text if ch.isalnum())
    return len(cleaned) >= 2

def on_wake_message(client, userdata, msg):
    global _wake_busy
    if msg.payload.decode() != "detected":
        return
    if _wake_busy or recorder._recording:
        return
    _wake_busy = True
    threading.Thread(target=_record_after_wake, daemon=True).start()

# find loudness of an audio chunk
def chunk_loudness(chunk) -> float:
    if chunk is None or len(chunk) == 0:
        return 0.0
    samples = np.asarray(chunk, dtype=np.float32).flatten()
    # square root of the average of all the samples squared
    return float(np.sqrt(np.mean(samples ** 2)))


mqtt_client.on_message = on_wake_message
mqtt_client.subscribe("jarvis/wake/detected")

print("Ready. Hold SPACE to talk, release to stop. Esc to quit.")
print("Also listening for wake word on jarvis/wake/detected")
with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    listener.join()