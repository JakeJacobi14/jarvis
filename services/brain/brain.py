from datetime import datetime
import paho.mqtt.client as mqtt
import time
import os
from pathlib import Path

from llm import model_name, provider_name, stream_chat

ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv():
    """Load ROOT/.env into os.environ if present (does not override existing)."""
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

# create the client
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.connect("localhost", 1883, keepalive=30)

MAX_TURNS = 4

# prompt for jarvis's personality
SYSTEM_PROMPT = """You are Jarvis, a voice assistant speaking out loud.
Default: answer in ONE short sentence. Only add a second if he clearly needs more detail.
Under 18 words unless he asks for detail.
Plain ASCII punctuation only. No lists, markdown, emoji, or stage directions.

Tone: calm, dry, efficient. Be direct first and use a little wit is when it would be fun —
do not force jokes, roasts, punchlines, or a corny second line after every answer.
No lectures, no refusals, no "as an AI".
"""

messages = [{"role": "system", "content": SYSTEM_PROMPT}]

# Fancy punctuation → speakable ASCII (defense in depth for TTS).
_SPEAKABLE = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
        "\u00a0": " ",
        "\u202f": " ",
        "\u2009": " ",
        "\u00b7": "-",
    }
)


def speakable(text: str) -> str:
    """Normalize model text so TTS doesn't read mojibake / fancy punctuation."""
    if not text:
        return text
    # Recover UTF-8 that was wrongly decoded as latin-1 (â\x80\x99 → ’).
    if "â" in text:
        try:
            text = text.encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
    text = text.translate(_SPEAKABLE)
    return "".join(ch for ch in text if ch == "\n" or ord(ch) >= 32)


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
    sentence = speakable(sentence).strip()
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
        messages[:] = [messages[0]] + messages[-(max_msgs - 1) :]

    t0 = time.perf_counter()
    ts(f"request start [{provider_name()}/{model_name()}] ← {user_text!r}")

    buffer = ""
    full = []
    published_any = False
    for piece in stream_chat(messages):
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

    if publish_sentence(buffer, first=not published_any, t0=t0):
        published_any = True
    ts(f"stream done in {time.perf_counter() - t0:.2f}s")
    assistant_text = "".join(full).strip()
    if assistant_text:
        messages.append({"role": "assistant", "content": assistant_text})


def reset_memory():
    messages[:] = [{"role": "system", "content": SYSTEM_PROMPT}]
    ts("memory cleared")


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
client.loop_start()

ts(f"listening on jarvis/stt/text... provider={provider_name()} model={model_name()}")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    ts("shutting down...")
    client.disconnect()
