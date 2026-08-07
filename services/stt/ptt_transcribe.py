from datetime import datetime
import numpy as np
import sounddevice as sd
from pynput import keyboard
from faster_whisper import WhisperModel
import threading
import paho.mqtt.client as mqtt
import time
import os

# VAD
SILENCE_THRESHOLD = 0.01 # volume cuttoff for "silence"
SILENCE_DURATION = 0.7 # how long silence is needed to end the listening
MAX_RECORD_SECONDS = 10.0 # max time to record for
FOLLOWUP_WINDOW = 3.0


def ts(msg: str):
    stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{stamp}] STT  | {msg}"
    print(line, flush=True)
    path = os.environ.get("JARVIS_TIMING_LOG")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

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
ts("Loading Whisper model...")
_t_load = time.perf_counter()
model = WhisperModel("base.en", device="cpu", compute_type="int8")
ts(f"Whisper ready in {time.perf_counter() - _t_load:.2f}s")
recorder = PushToTalkRecorder()


# state guard
is_space_pressed = False

def transcribe_async(audio):
    if len(audio) == 0:
        return
    audio_secs = len(audio) / recorder.sample_rate
    ts(f"transcribing {audio_secs:.2f}s of audio...")
    t0 = time.perf_counter()
    segments, _ = model.transcribe(audio, language="en", beam_size=1, vad_filter=True)
    elapsed = time.perf_counter() - t0

    full_text = " ".join(seg.text.strip() for seg in segments).strip()
    if not is_real_utterance(full_text):
        ts(f"ignored non-speech in {elapsed:.2f}s: {full_text!r}")
        return
    ts(f"done in {elapsed:.2f}s → {full_text!r}")
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
        ts("wake received — listening until you finish speaking...")
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
                    ts(f"silence detected — recorded {now - started_at:.2f}s")
                    break

            # max recording length check
            if (now - started_at) >= MAX_RECORD_SECONDS:
                ts("max record time reached, stopping")
                break
        if not heard_speech:
            ts("no speech heard, ignoring")
            recorder.stop()
            return

        audio = recorder.stop()
        transcribe_async(audio)
    finally:
        _wake_busy = False

def _record_followup():
    global _wake_busy
    try:
        ts(f"follow-up window open ({FOLLOWUP_WINDOW}s)...")
        recorder.start()

        heard_speech = False
        silence_started_at = None
        started_at = time.monotonic()

        while True:
            time.sleep(0.2)

            if not recorder._frames:
                if (time.monotonic() - started_at) >= FOLLOWUP_WINDOW:
                    ts("no follow-up — back to wake word")
                    break
                continue

            loudness = chunk_loudness(recorder._frames[-1])
            now = time.monotonic()

            if loudness >= SILENCE_THRESHOLD:
                heard_speech = True
                silence_started_at = None
            elif heard_speech:
                if silence_started_at is None:
                    silence_started_at = now
                elif (now - silence_started_at) >= SILENCE_DURATION:
                    ts(f"silence detected — recorded {now - started_at:.2f}s")
                    break
            else:
                # still waiting for first speech
                if (now - started_at) >= FOLLOWUP_WINDOW:
                    ts("no follow-up — back to wake word")
                    break

            if (now - started_at) >= MAX_RECORD_SECONDS:
                ts("max record time reached, stopping")
                break

        if not heard_speech:
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


# find loudness of an audio chunk
def chunk_loudness(chunk) -> float:
    if chunk is None or len(chunk) == 0:
        return 0.0
    samples = np.asarray(chunk, dtype=np.float32).flatten()
    # square root of the average of all the samples squared
    return float(np.sqrt(np.mean(samples ** 2)))

def on_mqtt_message(client, userdata, msg):
    global _wake_busy
    topic = msg.topic
    payload = msg.payload.decode()

    if topic == "jarvis/wake/detected" and payload != "detected":
        return
    if topic == "jarvis/tts/response" and payload != "done":
        return
    if _wake_busy or recorder._recording:
        return

    _wake_busy = True
    if topic == "jarvis/tts/response":
        threading.Thread(target=_record_followup, daemon=True).start()
    else:
        threading.Thread(target=_record_after_wake, daemon=True).start()


mqtt_client.on_message = on_mqtt_message
mqtt_client.subscribe("jarvis/wake/detected")
mqtt_client.subscribe("jarvis/tts/response")

ts("Ready. Hold SPACE to talk, release to stop. Esc to quit.")
ts("Also listening for wake word on jarvis/wake/detected")
with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    listener.join()