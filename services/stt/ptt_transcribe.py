from datetime import datetime
from pathlib import Path
import numpy as np
import sounddevice as sd
from pynput import keyboard
import threading
import paho.mqtt.client as mqtt
import time
import os

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

# VAD (whisper / local path only)
SILENCE_THRESHOLD = 0.01
SILENCE_DURATION = 0.7
MAX_RECORD_SECONDS = 10.0
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


def provider_name() -> str:
    return os.environ.get("JARVIS_STT_PROVIDER", "cartesia").strip().lower()


def is_real_utterance(text: str) -> bool:
    cleaned = "".join(ch for ch in text if ch.isalnum())
    return len(cleaned) >= 2


def chunk_loudness(chunk) -> float:
    if chunk is None or len(chunk) == 0:
        return 0.0
    samples = np.asarray(chunk, dtype=np.float32).flatten()
    return float(np.sqrt(np.mean(samples**2)))


def publish_text(full_text: str, *, elapsed: float, source: str = "wake"):
    if not is_real_utterance(full_text):
        ts(f"ignored non-speech in {elapsed:.2f}s: {full_text!r}")
        return
    topic = "jarvis/stt/followup" if source == "followup" else "jarvis/stt/text"
    ts(f"done in {elapsed:.2f}s [{source}] → {full_text!r}")
    mqtt_client.publish(topic, full_text)


mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.connect("localhost", 1883, keepalive=30)
mqtt_client.loop_start()

PROVIDER = provider_name()
ts(f"provider={PROVIDER}")

# ── backends ─────────────────────────────────────────────────────────
whisper_model = None
recorder = None
ink = None

if PROVIDER == "whisper":
    from faster_whisper import WhisperModel

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

        def audio_callback(self, indata, frames, time_info, status):
            if status:
                print(f"\nAudio error: {status}")
            if self._recording:
                self._frames.append(indata.copy())

        def stop(self):
            if not self._recording:
                return np.array([], dtype=np.float32)
            self._recording = False
            print(" [Stopped]")
            if self._frames:
                return np.concatenate(self._frames, axis=0).flatten()
            return np.array([], dtype=np.float32)

    ts("Loading Whisper model...")
    _t_load = time.perf_counter()
    whisper_model = WhisperModel("base.en", device="cpu", compute_type="int8")
    ts(f"Whisper ready in {time.perf_counter() - _t_load:.2f}s")
    recorder = PushToTalkRecorder()
elif PROVIDER == "cartesia":
    from cartesia_stt import CartesiaSTT

    ts("Connecting Cartesia Ink-2...")
    _t_load = time.perf_counter()
    ink = CartesiaSTT()
    ts(f"Ink-2 ready in {time.perf_counter() - _t_load:.2f}s")
else:
    raise SystemExit(
        f"Unknown JARVIS_STT_PROVIDER={PROVIDER!r} (expected cartesia|whisper)"
    )

# ── whisper helpers ──────────────────────────────────────────────────
is_space_pressed = False
_wake_busy = False
_ptt_stop = None  # threading.Event for cartesia PTT


def whisper_transcribe_async(audio):
    if len(audio) == 0:
        return
    audio_secs = len(audio) / recorder.sample_rate
    ts(f"transcribing {audio_secs:.2f}s of audio...")
    t0 = time.perf_counter()
    segments, _ = whisper_model.transcribe(
        audio, language="en", beam_size=1, vad_filter=True
    )
    elapsed = time.perf_counter() - t0
    full_text = " ".join(seg.text.strip() for seg in segments).strip()
    publish_text(full_text, elapsed=elapsed)


def cartesia_listen_wake():
    global _wake_busy
    try:
        ts("wake received — Ink listening for your turn...")
        t0 = time.perf_counter()

        def on_start():
            mqtt_client.publish("jarvis/tts/interrupt", "stop")

        text = ink.listen_turn(
            idle_timeout=None,
            max_seconds=MAX_RECORD_SECONDS,
            on_speech_start=on_start,
        )
        elapsed = time.perf_counter() - t0
        if not text:
            ts(f"no speech heard ({elapsed:.2f}s)")
            return
        publish_text(text, elapsed=elapsed)
    except Exception as e:
        ts(f"error: {e}")
    finally:
        _wake_busy = False


def cartesia_listen_followup():
    global _wake_busy
    try:
        ts(f"follow-up window open ({FOLLOWUP_WINDOW}s) via Ink...")
        t0 = time.perf_counter()
        text = ink.listen_turn(
            idle_timeout=FOLLOWUP_WINDOW,
            max_seconds=MAX_RECORD_SECONDS,
        )
        elapsed = time.perf_counter() - t0
        if not text:
            ts("no follow-up — back to wake word")
            return
        publish_text(text, elapsed=elapsed, source="followup")
    except Exception as e:
        ts(f"error: {e}")
    finally:
        _wake_busy = False


def whisper_record_after_wake():
    global _wake_busy
    try:
        ts("wake received — listening until you finish speaking...")
        recorder.start()

        heard_speech = False
        silence_started_at = None
        started_at = time.monotonic()

        while True:
            time.sleep(0.2)
            if not recorder._frames:
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

            if (now - started_at) >= MAX_RECORD_SECONDS:
                ts("max record time reached, stopping")
                break
        if not heard_speech:
            ts("no speech heard, ignoring")
            recorder.stop()
            return

        audio = recorder.stop()
        whisper_transcribe_async(audio)
    finally:
        _wake_busy = False


def whisper_record_followup():
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
        # Follow-up uses a tagged publish path
        if len(audio) == 0:
            return
        audio_secs = len(audio) / recorder.sample_rate
        ts(f"transcribing {audio_secs:.2f}s of audio [followup]...")
        t0 = time.perf_counter()
        segments, _ = whisper_model.transcribe(
            audio, language="en", beam_size=1, vad_filter=True
        )
        elapsed = time.perf_counter() - t0
        full_text = " ".join(seg.text.strip() for seg in segments).strip()
        publish_text(full_text, elapsed=elapsed, source="followup")
    finally:
        _wake_busy = False


# ── keyboard (PTT) ───────────────────────────────────────────────────
def on_press(key):
    global is_space_pressed, _ptt_stop
    if key != keyboard.Key.space or is_space_pressed:
        return
    is_space_pressed = True
    if PROVIDER == "whisper":
        recorder.start()
        return
    # Cartesia: stream live until release
    _ptt_stop = threading.Event()
    stop_event = _ptt_stop

    def _run():
        print("\n[Recording...]", end="", flush=True)
        t0 = time.perf_counter()
        try:
            text = ink.listen_ptt(stop_event)
        except Exception as e:
            ts(f"error: {e}")
            return
        finally:
            print(" [Stopped]")
        elapsed = time.perf_counter() - t0
        if text:
            publish_text(text, elapsed=elapsed)
        else:
            ts(f"no speech in PTT ({elapsed:.2f}s)")

    threading.Thread(target=_run, daemon=True).start()


def on_release(key):
    global is_space_pressed, _ptt_stop
    if key == keyboard.Key.space:
        is_space_pressed = False
        if PROVIDER == "whisper":
            audio = recorder.stop()
            threading.Thread(
                target=whisper_transcribe_async, args=(audio,), daemon=True
            ).start()
        elif _ptt_stop is not None:
            _ptt_stop.set()
            _ptt_stop = None

    if key == keyboard.Key.esc:
        return False


def on_mqtt_message(client, userdata, msg):
    global _wake_busy
    topic = msg.topic
    payload = msg.payload.decode()

    if topic == "jarvis/wake/detected" and payload != "detected":
        return
    if topic == "jarvis/tts/response" and payload != "done":
        return
    if _wake_busy:
        return
    if PROVIDER == "whisper" and recorder._recording:
        return

    _wake_busy = True
    if PROVIDER == "cartesia":
        target = (
            cartesia_listen_followup
            if topic == "jarvis/tts/response"
            else cartesia_listen_wake
        )
    else:
        target = (
            whisper_record_followup
            if topic == "jarvis/tts/response"
            else whisper_record_after_wake
        )
    threading.Thread(target=target, daemon=True).start()


mqtt_client.on_message = on_mqtt_message
mqtt_client.subscribe("jarvis/wake/detected")
mqtt_client.subscribe("jarvis/tts/response")

ts("Ready. Hold SPACE to talk, release to stop. Esc to quit.")
ts("Also listening for wake word on jarvis/wake/detected")
with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    listener.join()
