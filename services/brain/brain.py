from datetime import datetime
import paho.mqtt.client as mqtt
import time
import os
from pathlib import Path

from commands import match_command
from commands.search import looks_like_fact_question
from llm import model_name, provider_name, stream_chat
import spotify_client
import tavily_client

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

When web search results are provided, answer ONLY from those facts. Do not invent.
If the facts are incomplete, say what you found — never go silent.
"""

SEARCH_MAX_TOKENS = int(os.environ.get("JARVIS_SEARCH_MAX_TOKENS", "120"))
EMPTY_FALLBACK = "I blanked on that. Try asking again."

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


def _trim_memory():
    # drop old turns, always keep system at [0]
    # each "turn" = user + assistant → keep last MAX_TURNS*2 messages + system
    max_msgs = 1 + MAX_TURNS * 2
    if len(messages) > max_msgs:
        messages[:] = [messages[0]] + messages[-(max_msgs - 1) :]


def _stream_and_publish(
    chat_messages: list[dict], *, t0: float, max_tokens: int | None = None
) -> str:
    buffer = ""
    full = []
    published_any = False
    try:
        for piece in stream_chat(chat_messages, max_tokens=max_tokens):
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
    except Exception as e:
        ts(f"stream error: {e}")
        return ""
    ts(f"stream done in {time.perf_counter() - t0:.2f}s")
    return "".join(full).strip()


def _speak_direct(text: str, *, t0: float) -> str:
    """Publish a non-streamed reply as one or more sentences."""
    published_any = False
    remaining = speakable(text).strip()
    while remaining:
        ends = [remaining.find(p) for p in ".!?" if p in remaining]
        if not ends:
            if publish_sentence(remaining, first=not published_any, t0=t0):
                published_any = True
            break
        idx = min(ends)
        sentence, remaining = remaining[: idx + 1], remaining[idx + 1 :].lstrip()
        if publish_sentence(sentence, first=not published_any, t0=t0):
            published_any = True
    return speakable(text).strip()


def _tavily_spoken_fallback(data: dict) -> str:
    """Prefer Tavily's answer; else first result title/snippet."""
    answer = speakable((data.get("answer") or "").strip())
    if answer:
        # Keep it one spoken beat.
        for p in ".!?":
            if p in answer:
                answer = answer[: answer.find(p) + 1]
                break
        return answer
    results = data.get("results") or []
    if results:
        title = speakable((results[0].get("title") or "").strip())
        snippet = speakable((results[0].get("content") or "").strip())
        if title and snippet:
            return f"{title}. {snippet[:160]}"
        return title or snippet[:180]
    return ""


def _handle_search(user_text: str, query: str, *, t0: float) -> str:
    ts(f"command=search query={query!r}")
    try:
        data = tavily_client.search(query)
    except tavily_client.TavilyError as e:
        ts(f"search error: {e}")
        return _speak_direct("I couldn't reach the web just now.", t0=t0)

    facts = tavily_client.format_for_llm(data)
    ts(f"search ok in {time.perf_counter() - t0:.2f}s ({len(facts)} chars)")

    # Ephemeral messages: don't pollute long-term memory with raw search dumps.
    summarize = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"User asked: {user_text}\n\n"
                f"Web search results for {query!r}:\n{facts}\n\n"
                "Reply in one short spoken sentence using only these facts. "
                "Never reply with an empty message."
            ),
        },
    ]
    reply = _stream_and_publish(summarize, t0=t0, max_tokens=SEARCH_MAX_TOKENS)
    if reply:
        return reply

    # gpt-oss sometimes burns the token budget on reasoning and returns "".
    fallback = _tavily_spoken_fallback(data) or EMPTY_FALLBACK
    ts(f"search LLM empty — speaking Tavily fallback")
    return _speak_direct(fallback, t0=t0)


def _handle_spotify(cmd, *, t0: float) -> str:
    try:
        if cmd.spotify_query:
            ts(f"command=spotify play query={cmd.spotify_query!r}")
            reply = spotify_client.play_query(cmd.spotify_query)
        elif cmd.spotify_action:
            ts(f"command=spotify action={cmd.spotify_action!r}")
            reply = spotify_client.control(cmd.spotify_action)
        else:
            reply = "I didn't catch what to play."
    except spotify_client.SpotifyError as e:
        ts(f"spotify error: {e}")
        msg = str(e)
        if "not configured" in msg.lower():
            reply = "Spotify isn't set up yet."
        elif "No Spotify devices" in msg or "Open Spotify" in msg:
            reply = "Open Spotify on a device first."
        elif "Premium" in msg:
            reply = "Spotify Premium is required to play music."
        else:
            reply = "Spotify didn't cooperate just now."
    return _speak_direct(reply, t0=t0)


def query_brain(user_text: str):
    t0 = time.perf_counter()
    cmd = match_command(user_text)

    if cmd is not None and cmd.direct_reply:
        ts(f"command={cmd.label} ← {user_text!r}")
        messages.append({"role": "user", "content": user_text})
        reply = _speak_direct(cmd.direct_reply, t0=t0)
        if reply:
            messages.append({"role": "assistant", "content": reply})
        _trim_memory()
        ts(f"command={cmd.label} done in {time.perf_counter() - t0:.2f}s")
        return

    if cmd is not None and (cmd.spotify_query or cmd.spotify_action):
        messages.append({"role": "user", "content": user_text})
        reply = _handle_spotify(cmd, t0=t0)
        if reply:
            messages.append({"role": "assistant", "content": reply})
        _trim_memory()
        return

    if cmd is not None and cmd.search_query:
        messages.append({"role": "user", "content": user_text})
        reply = _handle_search(user_text, cmd.search_query, t0=t0)
        if reply:
            messages.append({"role": "assistant", "content": reply})
        _trim_memory()
        return

    messages.append({"role": "user", "content": user_text})
    _trim_memory()

    ts(f"request start [{provider_name()}/{model_name()}] ← {user_text!r}")
    assistant_text = _stream_and_publish(messages, t0=t0)

    # Empty stream (common with tiny max_tokens + reasoning models) → web fallback
    # for fact-like questions, otherwise speak a short apology so we never go quiet.
    if not assistant_text:
        ts("empty LLM reply")
        if looks_like_fact_question(user_text) and tavily_client.api_key():
            ts("retrying via web search")
            # Pop the empty turn's user msg is already in memory; search path
            # will append its own user+assistant — replace by searching now.
            assistant_text = _handle_search(user_text, user_text, t0=t0)
        else:
            assistant_text = _speak_direct(EMPTY_FALLBACK, t0=t0)

    if assistant_text:
        # Avoid double-appending if search fallback already added assistant in
        # memory via _handle_search path... _handle_search does NOT append.
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
        try:
            _speak_direct("Something went wrong on my end.", t0=time.perf_counter())
        except Exception:
            pass


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
