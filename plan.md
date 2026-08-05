# Jarvis — Home AI Assistant: Project Plan

**Goal:** A local, low-latency conversational assistant for the apartment. Wake word → listens → understands → (optionally) acts on smart home / web → responds out loud, fast enough that it feels like talking to a person, not a chatbot.

**Team:**
- **Software** (this repo, pipeline, AI logic) — [ jake ]
- **Hardware** (compute, mic, camera, screen, speakers) — [ frank ]

---

## 1. Core Design Principle: Decoupled Services over MQTT

We are **not** building one monolithic script. Each stage of the pipeline is an independent service that communicates over an **MQTT broker** (pub/sub messaging). This means:

- Either of us can develop/test our piece without the other's hardware being ready.
- Hardware can change (different mic, different compute box, a second Pi in another room) without touching the software architecture — it just becomes another MQTT client.
- Services can run wherever makes sense: today, everything on one Mac for testing; later, wake-word listening on the Pi while the LLM/TTS run on something beefier.

**MQTT** = lightweight publish/subscribe protocol. Services publish short messages to named **topics** (e.g. `jarvis/stt/text`); anything subscribed to that topic receives it instantly. No service needs to know the others exist or where they're running — everyone just talks through the broker (we're using **Mosquitto**, open source, runs on a Pi or laptop with negligible overhead).

> Note: raw audio is *not* sent over MQTT (too heavy for the protocol's design). MQTT carries **events and text** (wake-word-fired, transcribed text, response text, TTS-done). Audio itself streams in-process or over a local socket between adjacent services (mic→STT, LLM→TTS).

---

## 2. Pipeline & Service Breakdown

| Service | Responsibility | Key Library |
|---|---|---|
| `mic-wakeword` | Always-listening wake word detection ("Hey Jarvis") | **openWakeWord** (OSS). Porcupine remains an optional later upgrade if we want a commercial keyword model. |
| `stt` | Speech → text after wake word; end-of-speech via energy/RMS (upgrade path: webrtcvad / silero-vad) | **faster-whisper** |
| `brain` | Local LLM + personality; streams sentences over MQTT. Still TODO: tool calling, conversation memory | **Ollama** (currently `neural-chat`) |
| `tts` | Text → speech; sentence queue so streamed brain chunks play in order; interrupt via MQTT | **Kokoro** (`kokoro-onnx`) |
| `home-control` | Executes smart home actions | **Home Assistant** REST/websocket API |
| `avatar` | Animates a face/avatar on the screen, synced to speaking state | Web-based (HTML/Canvas), Rive/Lottie or Live2D for lip-sync |
| `web-tools` | Real-time web search + weather, exposed to `brain` as callable tools | **Brave Search API** or **SerpAPI** (search), **Open-Meteo** (weather, free, no key) |
| *(optional)* `speaker-id` | Identifies who is talking | **Resemblyzer** or **pyannote-audio** |

---

## 3. Latency Strategy (top priority)

To avoid "chatbot pause" and keep it feeling like a real conversation:

1. **Stream everything.** LLM tokens stream from Ollama → published sentence-by-sentence → TTS queue plays them in order, so Jarvis can start speaking before the full response is generated.
2. **VAD instead of fixed silence timeouts** — energy/RMS end-of-speech is in place; library VAD (webrtcvad/silero) is the upgrade if accuracy needs it.
3. **Test entirely local first.** All services run on the M4 MacBook Pro during development — no network hop, no dependency on final hardware, and the M4 is strong enough to run Ollama + faster-whisper + Kokoro simultaneously.
4. **Start with a smaller/faster LLM** while tuning the pipeline; scale model size up only once latency feels solid.

---

## 4. Repo Structure

```
jarvis/
  broker/                 # mosquitto config
  services/
    mic-wakeword/          # openWakeWord + audio capture
    stt/                   # faster-whisper + energy VAD
    brain/                 # Ollama orchestrator (memory/tools TODO)
    tts/                   # Kokoro + sentence queue + interrupt
    home-control/          # Home Assistant API wrapper (TODO)
    web-tools/             # search + weather (TODO)
    avatar/                # web-based avatar UI (TODO)
  shared/
    schemas/               # shared MQTT message formats (JSON schema) (TODO)
  docker-compose.yml        # Mosquitto today; more services later
  start.sh / stop.sh        # local multi-service launcher
```

---

## 5. MQTT Topic Schema

### In use today

| Topic | Publisher | Payload |
|---|---|---|
| `jarvis/wake/detected` | mic-wakeword | `detected` |
| `jarvis/tts/interrupt` | mic-wakeword | `stop` (TTS calls `sd.stop()`, clears queue) |
| `jarvis/stt/text` | stt | plain transcribed text (non-speech / `.` filtered out) |
| `jarvis/brain/response` | brain | one sentence per message while streaming |
| `jarvis/tts/response` | tts | `done` when the speak queue drains |

### Planned / not wired yet

| Topic | Publisher | Payload |
|---|---|---|
| `jarvis/stt/partial` / `jarvis/stt/final` | stt | structured partial/final transcripts |
| `jarvis/brain/action` | brain | `{ "tool": "...", "action": "..." }` |
| `jarvis/tts/status` | tts | `{ "state": "speaking" \| "done" }` |
| `jarvis/session/ended` | stt | clear brain memory / leave conversation mode |
| `jarvis/avatar/state` | avatar | `{ "state": "idle" \| "listening" \| "thinking" \| "speaking" }` |

---

## 6. Build Order (MVP-first)

1. ✅ **MQTT broker up + smoke test** on the Mac.
2. ✅ **Bare pipeline, push-to-talk**: mic → faster-whisper → text.
3. ✅ **Ollama brain**: text → LLM → MQTT response + personality prompt.
4. ✅ **Kokoro TTS**: pipeline talks back out loud.
5. ✅ **openWakeWord**: hands-free "Hey Jarvis" (+ interrupt publish).
6. ✅ **Streaming**: Ollama `stream=True` → sentence publish → TTS queue.
7. ✅ **VAD (v1)**: energy/RMS end-of-speech (replace fixed `sleep(5)`). Library VAD optional later.
8. **Home Assistant + web search/weather as tools.**
9. **Avatar UI** — cosmetic, bolt on once the core loop is solid.
10. **Move services onto real hardware** — docker-compose reshuffle, not new architecture.

### Near-term enhancements (not numbered steps, but next UX wins)

- **Conversation memory** in brain (`/api/chat` + rolling messages).
- **Conversation mode**: after TTS `done`, listen ~3–5s without requiring the wake word again.
- **Stronger barge-in** while speakers are playing (echo / own-voice problem).

---

## 7. Open Questions / To Decide Together

- Final choice of LLM size vs. hardware constraints once we know what compute the Pi (or server) can actually handle.
- Whether to stick with openWakeWord long-term or switch to Porcupine for keyword quality.
- Whether voice recognition (speaker ID) is in scope for v1 or a later add-on.
- Final mic/speaker hardware, once picked, to confirm compatibility with the STT/TTS libraries chosen here.
- Screen size/type for the avatar, to decide between a lightweight web view vs. a full animation engine.

8/05/2026 - changed the plan to use Kokoro TTS instead of Piper TTS
