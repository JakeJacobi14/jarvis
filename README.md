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
- Step 8: Home Assistant + web/weather tools
- Step 9: Avatar UI
- Step 10: Real hardware

## Run (Mac)

Needs **Docker Desktop** and **Ollama**. Venvs are created/updated automatically.

```bash
./start.sh              # broker + Ollama + a Terminal window per service
./start.sh --bg         # same stack, but backgrounded → logs/*.log
./start.sh --setup      # only create/update services/*/.venv
./stop.sh               # stop Python services
./stop.sh --broker      # also stop Mosquitto
```

Or double-click **`Jarvis.command`** in Finder (same as `./start.sh`).

## MQTT topics in use

| Topic | Direction |
|---|---|
| `jarvis/wake/detected` | wake → STT |
| `jarvis/tts/interrupt` | wake → TTS (barge-in) |
| `jarvis/stt/text` | STT → brain |
| `jarvis/brain/response` | brain → TTS (one sentence per message when streaming) |
| `jarvis/tts/response` | TTS → `done` when queue drains |

## Not done yet

- Conversation memory across turns
- Listen-again window after TTS (wake once per conversation)
- Robust barge-in while speakers are loud (echo)
- Home Assistant / tools / avatar / Pi deploy
