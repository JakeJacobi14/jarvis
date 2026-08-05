import queue
import threading
import time
import sounddevice as sd
import paho.mqtt.client as mqtt
from jarvis_tts import JarvisTTS

tts = JarvisTTS(voice="bm_lewis")
tts.say("Loading.")

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
            tts.say(text)
        except Exception as e:
            print(f"Error in TTS: {e}", flush=True)
        finally:
            speak_queue.task_done()
            if speak_queue.empty() and not _interrupt.is_set():
                client.publish("jarvis/tts/response", "done")


def on_interrupt(client, userdata, msg):
    print("Interrupt received — stopping speech", flush=True)
    _interrupt.set()
    clear_queue()
    sd.stop()
    _interrupt.clear()


def on_message(client, userdata, msg):
    text = msg.payload.decode().strip()
    if not text:
        return
    print(f"Received: {text}", flush=True)
    speak_queue.put(text)


client.message_callback_add("jarvis/tts/interrupt", on_interrupt)
client.subscribe("jarvis/tts/interrupt")
client.on_message = on_message
client.subscribe("jarvis/brain/response")
client.loop_start()

threading.Thread(target=speaker_loop, daemon=True).start()

print("tts listening...")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Shutting down...")
    speak_queue.put(None)
    client.disconnect()
