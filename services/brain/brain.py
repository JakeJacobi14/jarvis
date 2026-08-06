import requests
import json
import paho.mqtt.client as mqtt
import time

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

def publish_sentence(sentence: str):
    sentence = sentence.strip()
    if not sentence:
        return
    print(f"Sentence: {sentence}")
    client.publish("jarvis/brain/response", sentence)


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

    response = requests.post(OLLAMA_URL, json=payload, stream=True)
    response.raise_for_status()

    buffer = ""
    full = []
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
            publish_sentence(sentence)

        if data.get("done"):
            break

    publish_sentence(buffer)
    assistant_text = "".join(full).strip()
    if assistant_text:
        messages.append({"role": "assistant", "content": assistant_text})


# on message from ollama recieved
def on_message(client, userdata, msg):
    text = msg.payload.decode()
    print(f"Received: {text}")
    try:
        response = query_brain(text)
    except Exception as e:
        print(f"Error calling brain: {e}")
        
client.on_message = on_message
client.subscribe("jarvis/stt/text")
client.loop_start()  # start listening in the background

print("Brain listening on jarvis/stt/text...")
try:
    while True:
        time.sleep(1) # the main loop, keep the program running
except KeyboardInterrupt:
    print("Shutting down...")
    client.disconnect()