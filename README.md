# Jarvis

Local voice assistant pipeline over MQTT: wake word → STT → Ollama brain → TTS.

## Build progress

- ✅ Step 1: MQTT broker + smoke test
- ✅ Step 2: Push-to-talk pipeline (mic → faster-whisper → text)
- ✅ Step 3: Ollama brain (personality + MQTT)
- ✅ Step 4: Kokoro TTS
- ✅ Step 5: openWakeWord ("Hey Jarvis")
- ✅ Step 6: Streaming LLM → sentence-chunked TTS queue
- ✅ Step 7: End-of-speech detection (energy/RMS VAD; library VAD later)
- ✅ Conversation memory + listen-again after TTS
- Step 8: Home Assistant + web/weather tools
- Step 9: Avatar UI
- Step 10: Real hardware

## Requirements

- **Docker** (Mosquitto broker)
- **Ollama** with a chat model, e.g. `ollama pull llama3.2:3b`
- **Python 3.10+**, mic + speakers
- Kokoro TTS model files (auto-downloaded by `./start.sh` if missing)

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

Not quite one-click: install Docker + Ollama yourself, then:

```bash
git clone https://github.com/JakeJacobi14/jarvis.git
cd jarvis
ollama pull llama3.2:3b
./start.sh              # uses --bg automatically on Linux
tail -f logs/*.log      # watch timing lines
```

You may also need PortAudio for mic/speakers:

```bash
# Debian/Ubuntu
sudo apt install portaudio19-dev python3-venv
# Fedora
sudo dnf install portaudio-devel
```

`Jarvis.command` and Terminal auto-open are macOS-only; on Linux use `./start.sh` (background) or run each service in your own terminals.

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
| `jarvis/brain/response` | brain → TTS (one sentence per message when streaming) |
| `jarvis/tts/response` | TTS → `done` when queue drains |

## Not done yet

- Robust barge-in while speakers are loud (echo)
- Home Assistant / tools / avatar / Pi deploy
