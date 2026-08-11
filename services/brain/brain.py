from datetime import datetime
import requests
import json
import paho.mqtt.client as mqtt
import time
import os

# create the client
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.connect("localhost", 1883, keepalive=30)

OLLAMA_URL = "http://localhost:11434/api/chat"
MAX_TURNS = 4

# prompt for jarvis's personality
SYSTEM_PROMPT = """You are Jarvis, a voice assistant.
You are speaking out loud — never write lists, markdown, or stage directions.
Reply in 1–2 short sentences. Under 25 words unless he asks for detail.

Tone: dry, witty, slightly rude. Roast when it's funny. No lectures, no refusals, no "as an AI".

Use recent conversation context when it helps. If something is unclear, ask one short question instead of rambling.
"""

messages = [{"role": "system", "content": SYSTEM_PROMPT}]


def ts(msg: str):
    stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{stamp}] LLM  | {msg}"
    print(line, flush=True)
    path = os.environ.get("JARVIS_TIMING_LOG")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


def publish_sentence(sentence: str, *, first: bool, t0: float):
    sentence = sentence.strip()
    if not sentence:
        return False
    elapsed = time.perf_counter() - t0
    if first:
        ts(f"first sentence in {elapsed:.2f}s → {sentence!r}")
    else:
        ts(f"sentence (+{elapsed:.2f}s) → {sentence!r}")
    client.publish("jarvis/brain/response", sentence)
    return True


def query_brain(user_text: str):
    messages.append({"role": "user", "content": user_text})

    # drop old turns, always keep system at [0]
    # each "turn" = user + assistant → keep last MAX_TURNS*2 messages + system
    max_msgs = 1 + MAX_TURNS * 2
    if len(messages) > max_msgs:
        messages[:] = [messages[0]] + messages[-(max_msgs - 1):]

    payload = {
        "model": "llama3.2:3b",
        "messages": messages,
        "stream": True,
        "options": {
            "temperature": 0.7,
            "top_p": 0.9,
            "num_predict": 60,
        },
    }

    t0 = time.perf_counter()
    ts(f"request start ← {user_text!r}")
    response = requests.post(OLLAMA_URL, json=payload, stream=True)
    response.raise_for_status()

    buffer = ""
    full = []
    published_any = False
    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue
        data = json.loads(line)
        piece = data.get("message", {}).get("content", "")
        buffer += piece
        full.append(piece)

        while True:
            ends = [buffer.find(p) for p in ".!?" if p in buffer]
            if not ends:
                break
            idx = min(ends)
            sentence, buffer = buffer[: idx + 1], buffer[idx + 1 :]
            if publish_sentence(sentence, first=not published_any, t0=t0):
                published_any = True

        if data.get("done"):
            break

    if publish_sentence(buffer, first=not published_any, t0=t0):
        published_any = True
    ts(f"stream done in {time.perf_counter() - t0:.2f}s")
    assistant_text = "".join(full).strip()
    if assistant_text:
        messages.append({"role": "assistant", "content": assistant_text})


def reset_memory():
    messages[:] = [{"role": "system", "content": SYSTEM_PROMPT}]
    ts("memory cleared")


# on message from ollama recieved
def on_message(client, userdata, msg):
    text = msg.payload.decode()
    if msg.topic == "jarvis/brain/reset":
        reset_memory()
        return
    ts(f"received STT text ← {text!r}")
    try:
        query_brain(text)
    except Exception as e:
        ts(f"error: {e}")

client.on_message = on_message
client.subscribe("jarvis/stt/text")
client.subscribe("jarvis/brain/reset")
client.loop_start()  # start listening in the background

ts("listening on jarvis/stt/text...")
try:
    while True:
        time.sleep(1) # the main loop, keep the program running
except KeyboardInterrupt:
    ts("shutting down...")
    client.disconnect()