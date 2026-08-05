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
| `mic-wakeword` | Always-listening wake word detection ("Hey Jarvis") | **Picovoice Porcupine** (or openWakeWord as free/OSS fallback) |
| `stt` | Streams speech → text after wake word fires, using VAD to detect end of speech | **faster-whisper** (or `whisper.cpp` if running directly on Pi); **webrtcvad** / **silero-vad** for end-of-speech detection |
| `brain` | Orchestrator: local LLM + tool calling (search, weather, smart home) + conversation memory | **Ollama** (local LLM, e.g. Llama 3.1 8B to start) |
| `tts` | Converts response text to speech, streamed sentence-by-sentence for low latency | **Kokoro TTS** (local/free) — ElevenLabs or OpenAI TTS as optional higher-quality paid alternative |
| `home-control` | Executes smart home actions | **Home Assistant** REST/websocket API (supports basically every smart plug/bulb brand — future-proofs us against whatever hardware gets bought) |
| `avatar` | Animates a face/avatar on the screen, synced to speaking state | Web-based (HTML/Canvas), Rive/Lottie or Live2D for lip-sync |
| `web-tools` | Real-time web search + weather, exposed to `brain` as callable tools | **Brave Search API** or **SerpAPI** (search), **Open-Meteo** (weather, free, no key) |
| *(optional)* `speaker-id` | Identifies who is talking | **Resemblyzer** or **pyannote-audio** |

---

## 3. Latency Strategy (top priority)

To avoid "chatbot pause" and keep it feeling like a real conversation:

1. **Stream everything.** LLM tokens stream from Ollama → fed to TTS sentence-by-sentence, so Jarvis starts speaking before the full response is generated. STT similarly processes partial audio rather than waiting for total silence.
2. **VAD instead of fixed silence timeouts** — detect the instant speech stops rather than waiting out a timer.
3. **Test entirely local first.** All services run on the M4 MacBook Pro during development — no network hop, no dependency on final hardware, and the M4 is strong enough to run Ollama + faster-whisper + Kokoro simultaneously.
4. **Start with a smaller/faster LLM** while tuning the pipeline; scale model size up only once latency feels solid.

---

## 4. Repo Structure

```
jarvis/
  broker/                 # mosquitto config
  services/
    mic-wakeword/          # Porcupine + audio capture
    stt/                   # faster-whisper + VAD
    brain/                 # Ollama + tool-calling orchestrator + memory
    tts/                   # Kokoro
    home-control/          # Home Assistant API wrapper
    web-tools/             # search + weather
    avatar/                # web-based avatar UI
  shared/
    schemas/               # shared MQTT message formats (JSON schema)
  docker-compose.yml        # each service toggled per-machine
```

Each service gets its own Docker Compose entry, so which machine runs which service is just a config choice — not a code change.

---

## 5. MQTT Topic Schema (draft — refine together)

| Topic | Publisher | Payload |
|---|---|---|
| `jarvis/wake/detected` | mic-wakeword | `{ "timestamp": ... }` |
| `jarvis/stt/partial` | stt | `{ "text": "...", "final": false }` |
| `jarvis/stt/final` | stt | `{ "text": "...", "final": true }` |
| `jarvis/brain/response` | brain | `{ "text": "...", "sentence_index": n }` (streamed) |
| `jarvis/brain/action` | brain | `{ "tool": "home-control", "action": "lights_off" }` |
| `jarvis/tts/status` | tts | `{ "state": "speaking" \| "done" }` |
| `jarvis/avatar/state` | avatar | `{ "state": "idle" \| "listening" \| "thinking" \| "speaking" }` |

---

## 6. Build Order (MVP-first)

1. **MQTT broker up + smoke test** (`mosquitto_pub`/`sub`) on the Mac.
2. **Bare pipeline, push-to-talk**: mic → faster-whisper → printed text (no wake word yet).
3. **Add Ollama**: text → LLM → printed response, with a defined personality/system prompt.
4. **Add Kokoro TTS**: pipeline talks back out loud.
5. **Add Porcupine wake word**: hands-free "Hey Jarvis" trigger.
6. **Add streaming**: sentence-chunked LLM → TTS to kill response latency.
7. **Add VAD**: replace timeout-based end-of-speech detection.
8. **Add Home Assistant integration + web search/weather as tools.**
9. **Add avatar UI last** — cosmetic, easiest to bolt on once the core loop is solid.
10. **Move services onto real hardware** once the Pi/camera/screen arrive — this is just a docker-compose reshuffle, not new code.

---

## 7. Open Questions / To Decide Together

- Final choice of LLM size vs. hardware constraints once we know what compute the Pi (or server) can actually handle.
- Whether voice recognition (speaker ID) is in scope for v1 or a later add-on.
- Final mic/speaker hardware, once picked, to confirm compatibility with the STT/TTS libraries chosen here.
- Screen size/type for the avatar, to decide between a lightweight web view vs. a full animation engine.


8/05/2026 - changed the plan to use Kokoro TTS instead of Piper TTS
