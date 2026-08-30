from datetime import datetime
import queue
import sys
import threading
import time
import os
from pathlib import Path
import sounddevice as sd
import paho.mqtt.client as mqtt
from jarvis_tts import create_tts, provider_name


def ts(msg: str):
    stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{stamp}] TTS  | {msg}"
    print(line, flush=True)
    path = os.environ.get("JARVIS_TIMING_LOG")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


provider = provider_name()
ts(f"loading TTS provider={provider}...")
_t_load = time.perf_counter()
tts = create_tts()
try:
    shared = Path(__file__).resolve().parents[1] / "sonos"
    if str(shared) not in sys.path:
        sys.path.insert(0, str(shared))
    import sonos_client

    if sonos_client.tts_enabled():
        ts(f"audio → Sonos {sonos_client.player_name()!r}")
    elif sonos_client.configured():
        ts("audio → local speakers (Spotify stays on Sonos)")
    else:
        ts("audio → local speakers (no Sonos found)")
except Exception as e:
    ts(f"audio → local speakers ({e})")
# Warm-up / confirm audio path
tts.say("Loading.")
ts(f"ready in {time.perf_counter() - _t_load:.2f}s")

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.connect("localhost", 1883, keepalive=30)

speak_queue = queue.Queue()
_interrupt = threading.Event()


def clear_queue():
    while True:
        try:
            speak_queue.get_nowait()
            speak_queue.task_done()
        except queue.Empty:
            break


def speaker_loop():
    while True:
        text = speak_queue.get()
        if text is None:
            break
        try:
            if _interrupt.is_set():
                continue
            t0 = time.perf_counter()
            ts(f"speaking ← {text!r}")
            client.publish("jarvis/status/tts", "speaking")
            synth_s, play_s = tts.say(text)
            ts(
                f"spoke in {time.perf_counter() - t0:.2f}s "
                f"(synth {synth_s:.2f}s, play {play_s:.2f}s)"
            )
        except Exception as e:
            ts(f"error: {e}")
        finally:
            speak_queue.task_done()
            if speak_queue.empty() and not _interrupt.is_set():
                ts("queue empty → done")
                client.publish("jarvis/status/tts", "idle")
                client.publish("jarvis/tts/response", "done")


def on_interrupt(client, userdata, msg):
    ts("interrupt — stopping speech")
    _interrupt.set()
    clear_queue()
    tts.interrupt()
    try:
        sd.stop()
    except Exception:
        pass
    client.publish("jarvis/status/tts", "idle")
    _interrupt.clear()


def on_message(client, userdata, msg):
    text = msg.payload.decode().strip()
    if not text:
        return
    ts(f"queued ← {text!r}")
    speak_queue.put(text)


client.message_callback_add("jarvis/tts/interrupt", on_interrupt)
client.subscribe("jarvis/tts/interrupt")
client.on_message = on_message
client.subscribe("jarvis/brain/response")
client.loop_start()

threading.Thread(target=speaker_loop, daemon=True).start()

ts("listening...")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    ts("shutting down...")
    speak_queue.put(None)
    client.disconnect()
