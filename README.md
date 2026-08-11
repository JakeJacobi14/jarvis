# Jarvis

Local voice assistant pipeline over MQTT: wake word → STT → LLM brain → TTS.

## Build progress

- ✅ Step 1: MQTT broker + smoke test
- ✅ Step 2: Push-to-talk / wake STT — **Cartesia Ink-2** (Whisper local fallback)
- ✅ Step 3: LLM brain (personality + MQTT) — **Groq `openai/gpt-oss-20b`** (Ollama fallback)
- ✅ Step 4: TTS — **Cartesia Sonic-3.5** (Kokoro local fallback)
- ✅ Step 5: openWakeWord ("Hey Jarvis")
- ✅ Step 6: Streaming LLM → sentence-chunked TTS queue
- ✅ Step 7: End-of-speech — Ink-2 turn detection (Whisper path keeps local energy VAD)
- ✅ Conversation memory + listen-again after TTS
- Step 8: Home Assistant + web/weather tools
- Step 9: Avatar UI
- Step 10: Real hardware

## Requirements

- **Docker** (Mosquitto broker)
- **Groq API key** (default brain) — [console.groq.com/keys](https://console.groq.com/keys)
  - Or **Ollama** with e.g. `ollama pull llama3.2:3b` if `JARVIS_LLM_PROVIDER=ollama`
- **Cartesia API key** (default TTS + STT) — [play.cartesia.ai/keys](https://play.cartesia.ai/keys)
  - Or local Kokoro if `JARVIS_TTS_PROVIDER=kokoro` / Whisper if `JARVIS_STT_PROVIDER=whisper`
- **Python 3.10+**, mic + speakers
## Config

```bash
cp .env.example .env
# edit .env → set GROQ_API_KEY=gsk_...
```

| Variable | Default | Notes |
|---|---|---|
| `JARVIS_LLM_PROVIDER` | `groq` | `groq` or `ollama` |
| `GROQ_API_KEY` | — | required for Groq |
| `GROQ_MODEL` | `openai/gpt-oss-20b` | |
| `GROQ_REASONING_EFFORT` | `low` | keep low for voice latency |
| `JARVIS_MAX_TOKENS` | `80` | short spoken replies |
| `JARVIS_TTS_PROVIDER` | `cartesia` | `cartesia` or `kokoro` |
| `CARTESIA_API_KEY` | — | required for Cartesia TTS |
| `CARTESIA_TTS_MODEL` | `sonic-3.5` | |
| `CARTESIA_VOICE_ID` | Archie (en-GB male) | [voice library](https://play.cartesia.ai/voices) |
| `JARVIS_STT_PROVIDER` | `cartesia` | `cartesia` (Ink-2) or `whisper` |
| `CARTESIA_STT_MODEL` | `ink-2` | streaming STT + turn detection |

## Run

```bash
./start.sh              # macOS: Terminal per service; Linux: background + logs/
./start.sh --bg         # background everywhere → logs/*.log
./start.sh --setup      # venvs + TTS models only
./stop.sh               # stop Python services
./stop.sh --broker      # also stop Mosquitto
```

On macOS you can also double-click **`Jarvis.command`**.

### Linux notes

Not quite one-click: install Docker yourself, then:

```bash
git clone https://github.com/JakeJacobi14/jarvis.git
cd jarvis
cp .env.example .env
# edit .env → set GROQ_API_KEY and CARTESIA_API_KEY (his own keys)
sudo apt install portaudio19-dev python3-venv   # Debian/Ubuntu; see below
./start.sh              # uses --bg automatically on Linux
tail -f logs/timing.log # watch the pipeline
```

You may also need PortAudio for mic/speakers:

```bash
# Debian/Ubuntu
sudo apt install portaudio19-dev python3-venv
# Fedora
sudo dnf install portaudio-devel
```

`Jarvis.command` and Terminal auto-open are macOS-only; on Linux use `./start.sh` (background) or run each service in your own terminals.

## Benchmark (LLM + TTS)

With the stack running, inject 5 fixed prompts (skips mic/STT) and print splits:

```bash
./bench.sh --label ollama                 # if you still have a local baseline
./bench.sh --label groq-gpt-oss-20b       # after switching to Groq
# … later, after Sonic TTS …
./bench.sh --label groq-sonic

./bench.sh --compare logs/benchmarks/<a>.json logs/benchmarks/<b>.json
```

Results land in `logs/benchmarks/`. Metrics per prompt: time-to-first sentence, LLM stream≈done, TTS tail, and end-to-end until speech finishes. Brain memory is reset between prompts so runs stay comparable.

## Timing logs

Each service stamps stages as `WAKE` / `STT` / `LLM` / `TTS`.  
`./start.sh` also writes them all to **`logs/timing.log`** and keeps that feed open in the main terminal so you can watch the full pipeline in one place.

```
[21:04:01.123] WAKE | detected 'hey_jarvis' ...
[21:04:01.130] STT  | wake received — listening...
[21:04:03.400] STT  | done in 0.72s → 'how are you'
[21:04:03.410] LLM  | first sentence in 0.85s → '...'
[21:04:04.200] TTS  | spoke in 0.40s
```

Ctrl-C in the main window only stops the timing view — services keep running (`./stop.sh` to stop them).

## MQTT topics in use

| Topic | Direction |
|---|---|
| `jarvis/wake/detected` | wake → STT |
| `jarvis/tts/interrupt` | wake → TTS (barge-in) |
| `jarvis/stt/text` | STT → brain |
| `jarvis/brain/reset` | bench → brain (clear conversation memory) |
| `jarvis/brain/response` | brain → TTS (one sentence per message when streaming) |
| `jarvis/tts/response` | TTS → `done` when queue drains |

## Not done yet

- Robust barge-in while speakers are loud (echo)
- Home Assistant / tools / avatar / Pi deploy
