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

    if sonos_client.tts_mode() == "overlay":
        if not sonos_client.tts_enabled():
            raise RuntimeError("Sonos overlay requested but no speaker found")
        ts(f"audio → Sonos overlay on {sonos_client.player_name()!r} (music ducks)")
    elif sonos_client.tts_mode() == "sonos":
        if not sonos_client.tts_enabled():
            raise RuntimeError("Sonos speech requested but no speaker found")
        ts(f"audio → Sonos {sonos_client.player_name()!r} (interrupts music)")
    elif sonos_client.configured():
        ts("audio → local speakers (Spotify stays on Sonos)")
    else:
        ts("audio → local speakers (no Sonos found)")
except Exception as e:
    ts(f"Sonos audio setup failed: {e}")
    raise
# Warm the TTS connection only. Playing "Loading." on Sonos queues an
# AudioClip and the next real sentence can sit ~20s before the speaker
# even fetches the WAV.
try:
    if hasattr(tts, "_ensure_ws"):
        tts._ensure_ws()
except Exception as e:
    ts(f"TTS warmup skipped ({e})")
ts(f"ready in {time.perf_counter() - _t_load:.2f}s")

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.connect("localhost", 1883, keepalive=30)
client.publish("jarvis/status/tts", "idle")

speak_queue = queue.Queue()
_interrupt = threading.Event()
_in_say = False
_gen = 0
_gen_lock = threading.Lock()


def _current_gen() -> int:
    with _gen_lock:
        return _gen


def _bump_gen() -> int:
    global _gen
    with _gen_lock:
        _gen += 1
        return _gen


def clear_queue():
    while True:
        try:
            speak_queue.get_nowait()
            speak_queue.task_done()
        except queue.Empty:
            break


def _gather_reply(text: str, gen: int) -> str:
    """Brain streams one sentence at a time; one Sonos clip per reply."""
    parts = [text]
    deadline = time.monotonic() + 0.45
    while time.monotonic() < deadline:
        try:
            nxt = speak_queue.get(timeout=max(0.02, deadline - time.monotonic()))
        except queue.Empty:
            break
        if nxt is None:
            speak_queue.put(None)
            break
        nxt_gen, nxt_text = nxt
        speak_queue.task_done()
        if nxt_gen != gen:
            continue
        nxt_text = (nxt_text or "").strip()
        if nxt_text:
            parts.append(nxt_text)
    return " ".join(parts)


def speaker_loop():
    global _in_say
    while True:
        item = speak_queue.get()
        if item is None:
            break
        gen, text = item
        finished_ok = False
        try:
            if _interrupt.is_set() or gen != _current_gen():
                continue
            text = _gather_reply(text, gen)
            t0 = time.perf_counter()
            ts(f"speaking ← {text!r}")
            client.publish("jarvis/status/tts", "speaking")
            _in_say = True
            synth_s, play_s = tts.say(text)
            finished_ok = gen == _current_gen()
            ts(
                f"spoke in {time.perf_counter() - t0:.2f}s "
                f"(synth {synth_s:.2f}s, play {play_s:.2f}s)"
            )
        except Exception as e:
            ts(f"error: {e}")
        finally:
            _in_say = False
            speak_queue.task_done()
            # Barge-in mid-sentence must not publish "done". A false wake
            # *after* he finished used to steal this and skip follow-up.
            if speak_queue.empty() and finished_ok:
                ts("queue empty → done")
                client.publish("jarvis/status/tts", "idle")
                client.publish("jarvis/tts/response", "done")


def on_interrupt(client, userdata, msg):
    ts("interrupt — stopping speech")
    _interrupt.set()
    if _in_say:
        _bump_gen()
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
    speak_queue.put((_current_gen(), text))


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
