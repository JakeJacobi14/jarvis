from datetime import datetime
import json
import paho.mqtt.client as mqtt
import threading
import time
import os
from pathlib import Path

from commands import match_command
from commands.search import looks_like_fact_question
from llm import model_name, provider_name, stream_chat
import camera_client
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

Tone: calm, dry, efficient. Be direct first. A little wit is fine when it lands —
do not force jokes, roasts, punchlines, or a corny second line after every answer.
No lectures, no refusals, no "as an AI".

Never mention being a robot, an AI, a system, software, operating status, or
"levels". Talk like a person. If he asks how you are: "I'm good, sir."

You can play Spotify, search the web, check the weather, control lights,
turn the TV and air conditioner on or off, take a photo, save a clip of the
last thirty seconds, set timers, and change speaker volume. Never claim you
cannot do those. If a request is unclear, ask one short clarifying question.

When web search results are provided, answer ONLY from those facts. Do not invent.
If the facts are incomplete, say what you found — never go silent.
"""

SEARCH_MAX_TOKENS = int(os.environ.get("JARVIS_SEARCH_MAX_TOKENS", "120"))
EMPTY_FALLBACK = "I blanked on that. Try asking again."

messages = [{"role": "system", "content": SYSTEM_PROMPT}]
last_spoken = ""
_job_lock = threading.Lock()
_job_id = 0


def _next_job() -> int:
    """Invalidate any in-flight reply and return the new job id."""
    global _job_id
    with _job_lock:
        _job_id += 1
        return _job_id


def _job_alive(job: int) -> bool:
    with _job_lock:
        return _job_id == job


def skip_followup():
    """Keep the room mic on wake-word only (music / phone / cancel)."""
    client.publish("jarvis/stt/followup_arm", "skip")

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


def publish_status(state: str):
    client.publish("jarvis/status/brain", state)


def publish_sentence(sentence: str, *, first: bool, t0: float, job: int | None = None):
    global last_spoken
    if job is not None and not _job_alive(job):
        return False
    sentence = speakable(sentence).strip()
    if not sentence:
        return False
    elapsed = time.perf_counter() - t0
    if first:
        ts(f"first sentence in {elapsed:.2f}s → {sentence!r}")
    else:
        ts(f"sentence (+{elapsed:.2f}s) → {sentence!r}")
    last_spoken = sentence
    client.publish("jarvis/brain/response", sentence)
    return True


def _trim_memory():
    # drop old turns, always keep system at [0]
    # each "turn" = user + assistant → keep last MAX_TURNS*2 messages + system
    max_msgs = 1 + MAX_TURNS * 2
    if len(messages) > max_msgs:
        messages[:] = [messages[0]] + messages[-(max_msgs - 1) :]


def _stream_and_publish(
    chat_messages: list[dict],
    *,
    t0: float,
    max_tokens: int | None = None,
    job: int | None = None,
) -> str:
    global last_spoken
    buffer = ""
    full = []
    published_any = False
    try:
        for piece in stream_chat(chat_messages, max_tokens=max_tokens):
            if job is not None and not _job_alive(job):
                ts("stream cancelled")
                return ""
            buffer += piece
            full.append(piece)

            while True:
                ends = [buffer.find(p) for p in ".!?" if p in buffer]
                if not ends:
                    break
                idx = min(ends)
                sentence, buffer = buffer[: idx + 1], buffer[idx + 1 :]
                if publish_sentence(
                    sentence, first=not published_any, t0=t0, job=job
                ):
                    published_any = True

        if publish_sentence(buffer, first=not published_any, t0=t0, job=job):
            published_any = True
    except Exception as e:
        ts(f"stream error: {e}")
        return ""
    ts(f"stream done in {time.perf_counter() - t0:.2f}s")
    result = "".join(full).strip()
    if result:
        last_spoken = speakable(result)
    return result


def _speak_direct(text: str, *, t0: float, job: int | None = None) -> str:
    """Publish a non-streamed reply as one or more sentences."""
    global last_spoken
    if job is not None and not _job_alive(job):
        return ""
    published_any = False
    remaining = speakable(text).strip()
    while remaining:
        ends = [remaining.find(p) for p in ".!?" if p in remaining]
        if not ends:
            if publish_sentence(remaining, first=not published_any, t0=t0, job=job):
                published_any = True
            break
        idx = min(ends)
        sentence, remaining = remaining[: idx + 1], remaining[idx + 1 :].lstrip()
        if publish_sentence(sentence, first=not published_any, t0=t0, job=job):
            published_any = True
    result = speakable(text).strip()
    if result:
        last_spoken = result
    return result


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


def _handle_search(
    user_text: str, query: str, *, t0: float, job: int | None = None
) -> str:
    ts(f"command=search query={query!r}")
    try:
        data = tavily_client.search(query)
    except tavily_client.TavilyError as e:
        ts(f"search error: {e}")
        return _speak_direct("I couldn't reach the web just now.", t0=t0, job=job)

    facts = tavily_client.format_for_llm(data)
    ts(f"search ok in {time.perf_counter() - t0:.2f}s ({len(facts)} chars)")

    # Prefer Tavily's own sentence so gpt-oss is not billed for a rewrite.
    spoken = _tavily_spoken_fallback(data)
    if spoken:
        ts("search → Tavily answer (skipping LLM)")
        return _speak_direct(spoken, t0=t0, job=job)

    # Snippets only — one short Groq pass, no chat history.
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
    reply = _stream_and_publish(
        summarize, t0=t0, max_tokens=SEARCH_MAX_TOKENS, job=job
    )
    if reply:
        return reply

    # gpt-oss sometimes burns the token budget on reasoning and returns "".
    fallback = _tavily_spoken_fallback(data) or EMPTY_FALLBACK
    ts(f"search LLM empty — speaking Tavily fallback")
    return _speak_direct(fallback, t0=t0, job=job)


def _handle_spotify(cmd, *, t0: float, job: int | None = None) -> str:
    skip_followup()
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
            reply = "I don't have a Spotify device to play on right now."
        elif "Premium" in msg:
            reply = "Spotify Premium is required to play music."
        else:
            reply = "Spotify didn't cooperate just now."
    return _speak_direct(reply, t0=t0, job=job)


def _remember(user_text: str, reply: str):
    messages.append({"role": "user", "content": user_text})
    if reply:
        messages.append({"role": "assistant", "content": reply})
    _trim_memory()


def _handle_run(cmd, user_text: str, *, t0: float, job: int | None = None) -> str:
    ts(f"command={cmd.label} ← {user_text!r}")
    try:
        reply = cmd.run() if cmd.run else ""
    except Exception as e:
        ts(f"command={cmd.label} error: {e}")
        reply = "That didn't work just now."
    spoken = _speak_direct(reply or "Done.", t0=t0, job=job)
    _remember(user_text, spoken)
    ts(f"command={cmd.label} done in {time.perf_counter() - t0:.2f}s")
    return spoken


def _handle_timer(cmd, user_text: str, *, t0: float, job: int | None = None) -> str:
    seconds = int(cmd.payload.get("seconds") or 0)
    confirm = cmd.payload.get("confirm") or "Timer set."
    done = cmd.payload.get("done") or "Time's up."
    ts(f"command=timer seconds={seconds} ← {user_text!r}")

    def fire():
        try:
            _speak_direct(done, t0=time.perf_counter())
        except Exception as e:
            ts(f"timer fire error: {e}")

    threading.Timer(seconds, fire).start()
    spoken = _speak_direct(confirm, t0=t0, job=job)
    _remember(user_text, spoken)
    return spoken


def query_brain(user_text: str, *, source: str = "wake", job: int | None = None):
    t0 = time.perf_counter()
    cmd = match_command(user_text)

    if cmd is not None and cmd.skip_followup:
        skip_followup()

    # nevermind / shut up / stop talking — interrupt and stay silent
    if cmd is not None and cmd.silent:
        client.publish("jarvis/tts/interrupt", "stop")
        ts(f"command={cmd.label} silent cancel ← {user_text!r} [{source}]")
        return

    if job is not None and not _job_alive(job):
        return

    publish_status("thinking")
    try:
        if cmd is not None and cmd.kind == "repeat":
            ts(f"command=repeat ← {user_text!r}")
            spoken = last_spoken or "I haven't said anything yet."
            reply = _speak_direct(spoken, t0=t0, job=job)
            _remember(user_text, reply)
            return

        if cmd is not None and cmd.kind == "timer":
            _handle_timer(cmd, user_text, t0=t0, job=job)
            return

        if cmd is not None and cmd.run is not None:
            _handle_run(cmd, user_text, t0=t0, job=job)
            return

        if cmd is not None and cmd.direct_reply:
            ts(f"command={cmd.label} ← {user_text!r}")
            reply = _speak_direct(cmd.direct_reply, t0=t0, job=job)
            _remember(user_text, reply)
            ts(f"command={cmd.label} done in {time.perf_counter() - t0:.2f}s")
            return

        if cmd is not None and (cmd.spotify_query or cmd.spotify_action):
            reply = _handle_spotify(cmd, t0=t0, job=job)
            _remember(user_text, reply)
            return

        if cmd is not None and cmd.search_query:
            reply = _handle_search(user_text, cmd.search_query, t0=t0, job=job)
            _remember(user_text, reply)
            return

        messages.append({"role": "user", "content": user_text})
        _trim_memory()

        ts(f"request start [{provider_name()}/{model_name()}] ← {user_text!r}")
        assistant_text = _stream_and_publish(messages, t0=t0, job=job)

        # Empty stream (common with tiny max_tokens + reasoning models) → web fallback
        # for fact-like questions, otherwise speak a short apology so we never go quiet.
        if not assistant_text:
            if job is not None and not _job_alive(job):
                return
            ts("empty LLM reply")
            if looks_like_fact_question(user_text) and tavily_client.api_key():
                ts("retrying via web search")
                assistant_text = _handle_search(
                    user_text, user_text, t0=t0, job=job
                )
            else:
                assistant_text = _speak_direct(EMPTY_FALLBACK, t0=t0, job=job)

        if assistant_text:
            messages.append({"role": "assistant", "content": assistant_text})
    finally:
        if job is None or _job_alive(job):
            publish_status("idle")


def reset_memory():
    messages[:] = [{"role": "system", "content": SYSTEM_PROMPT}]
    ts("memory cleared")


def _run_turn(text: str, source: str, job: int):
    try:
        query_brain(text, source=source, job=job)
    except Exception as e:
        ts(f"error: {e}")
        if _job_alive(job):
            try:
                _speak_direct(
                    "Something went wrong on my end.",
                    t0=time.perf_counter(),
                    job=job,
                )
            except Exception:
                pass


def on_message(client, userdata, msg):
    text = msg.payload.decode()
    if msg.topic == "jarvis/tts/interrupt":
        # Don't block the MQTT thread on an in-flight Groq call.
        _next_job()
        return
    if msg.topic == "jarvis/brain/reset":
        reset_memory()
        return
    if msg.topic == "jarvis/camera/snap":
        ts("camera snap requested (MQTT)")
        job = _next_job()

        def _snap():
            try:
                describe = False
                if text and text.strip().startswith("{"):
                    try:
                        describe = bool(json.loads(text).get("describe"))
                    except json.JSONDecodeError:
                        describe = False
                reply = camera_client.snap_and_maybe_describe(describe)
                _speak_direct(reply, t0=time.perf_counter(), job=job)
            except Exception as e:
                ts(f"camera snap error: {e}")
                try:
                    _speak_direct(
                        "I couldn't take a photo.", t0=time.perf_counter(), job=job
                    )
                except Exception:
                    pass

        threading.Thread(target=_snap, daemon=True).start()
        return
    source = "followup" if msg.topic == "jarvis/stt/followup" else "wake"
    ts(f"received STT text [{source}] ← {text!r}")
    job = _next_job()
    threading.Thread(target=_run_turn, args=(text, source, job), daemon=True).start()


client.on_message = on_message
client.subscribe("jarvis/stt/text")
client.subscribe("jarvis/stt/followup")
client.subscribe("jarvis/brain/reset")
client.subscribe("jarvis/camera/snap")
client.subscribe("jarvis/tts/interrupt")
client.loop_start()

ts(
    f"listening on jarvis/stt/text + followup... "
    f"provider={provider_name()} model={model_name()}"
)
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    ts("shutting down...")
    client.disconnect()
