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
- ✅ Brain command system + Tavily web search (`google …` / `search …` / live-fact detect)
- ✅ Weather (Open-Meteo), lights (Feit/Tuya, WLED, Home Assistant), camera snap, timers, volume
- ✅ Phone control panel + talking avatar (PWA on the LAN)
- ✅ Dashcam clips — *"Jarvis, clip that"* saves the last 30s of camera + mic
- ✅ Live transcript on the phone panel + always-listen from a phone or laptop mic
- ✅ Built-in Spotify Connect speaker (spotifyd), so no Spotify app has to be open

## Requirements

- **Docker** (Mosquitto broker)
- **Groq API key** (default brain) — [console.groq.com/keys](https://console.groq.com/keys)
  - Or **Ollama** with e.g. `ollama pull llama3.2:3b` if `JARVIS_LLM_PROVIDER=ollama`
- **Cartesia API key** (default TTS + STT) — [play.cartesia.ai/keys](https://play.cartesia.ai/keys)
  - Or local Kokoro if `JARVIS_TTS_PROVIDER=kokoro` / Whisper if `JARVIS_STT_PROVIDER=whisper`
- **Tavily API key** (web search commands) — [app.tavily.com](https://app.tavily.com/home)
- **Python 3.10+**, mic + speakers
- **ffmpeg** — required for `clip that`, also used for camera stills
- Optional: **Feit / Tuya WiFi bulbs**, a **WLED** strip, or Home Assistant for lights
- Spotify speaker: **spotifyd** is downloaded automatically by `./start.sh` / `Jarvis.command`
## Config

```bash
cp .env.example .env
# edit .env → set GROQ_API_KEY=gsk_... and TAVILY_API_KEY=tvly-...
```

| Variable | Default | Notes |
|---|---|---|
| `JARVIS_LLM_PROVIDER` | `groq` | `groq` or `ollama` |
| `GROQ_API_KEY` | — | required for Groq |
| `GROQ_MODEL` | `openai/gpt-oss-20b` | |
| `GROQ_REASONING_EFFORT` | `low` | keep low for voice latency |
| `JARVIS_MAX_TOKENS` | `50` | short spoken replies |
| `TAVILY_API_KEY` | — | required for `google` / `search` commands |
| `TAVILY_MAX_RESULTS` | `5` | hits fed into the spoken summary |
| `SPOTIFY_CLIENT_ID` | — | Spotify developer app |
| `SPOTIFY_CLIENT_SECRET` | — | Spotify developer app |
| `SPOTIFY_REFRESH_TOKEN` | — | from `spotify_auth.py` |
| `JARVIS_SPOTIFY_LOCAL_DEVICE` | `auto` | run spotifyd as a local Connect speaker |
| `JARVIS_SPOTIFY_DEVICE` | `Jarvis` | Connect device name to prefer |
| `JARVIS_TTS_PROVIDER` | `cartesia` | `cartesia` or `kokoro` |
| `CARTESIA_API_KEY` | — | required for Cartesia TTS |
| `CARTESIA_TTS_MODEL` | `sonic-3.5` | |
| `CARTESIA_VOICE_ID` | Archie (en-GB male) | [voice library](https://play.cartesia.ai/voices) |
| `JARVIS_STT_PROVIDER` | `cartesia` | `cartesia` (Ink-2) or `whisper` |
| `CARTESIA_STT_MODEL` | `ink-2` | streaming STT + turn detection |
| `JARVIS_CITY` | — | home city for weather (or `JARVIS_LAT`/`JARVIS_LON`) |
| `WLED_HOST` | — | RGB strip IP, e.g. `192.168.1.50` |
| `HA_URL` / `HA_TOKEN` / `HA_LIGHT_ENTITY` | — | Home Assistant lights if not using WLED |
| `JARVIS_CONTROL_PORT` | `8740` | phone panel + avatar |
| `JARVIS_CONTROL_PIN` | — | optional PIN for the panel |
| `JARVIS_CONTROL_TLS` | `1` | https on by default so the hold-to-speak mic works |
| `JARVIS_CLIP_ENABLED` | `1` | rolling dashcam buffer for `clip that` |
| `JARVIS_CLIP_SECONDS` | `30` | default clip length |
| `JARVIS_CLIP_MAX_SECONDS` | `90` | buffer depth / longest clip you can ask for |
| `JARVIS_CLIP_AUDIO` | `1` | record the mic alongside video |

### Voice commands (brain)

Extensible registry in `services/brain/commands/`. First match wins.

| Say… | What happens |
|---|---|
| `google …` / `search …` / `look up …` | Tavily search → short spoken summary |
| live / ranking / encyclopedic facts | auto-search via Tavily (with silent-reply fallback) |
| `what's the weather` / `weather in …` | Open-Meteo forecast for home (`JARVIS_CITY`) or a named place |
| `what day/time/date is it` | local clock (no web) |
| `play …` / `put on …` | Spotify search + play (Premium + auth) |
| `pause` / `resume` / `skip` / `next` / `previous` | Spotify playback control |
| `turn on/off the lights` / `lights red` / `rainbow` | WLED strip or Home Assistant light |
| `take a picture` / `what do you see` | one still from the camera; Groq vision describes it when asked |
| `clip that` / `clip the last 45 seconds` | saves the last stretch of camera + mic to `clips/` |
| `set a timer for 5 minutes` / `remind me in 30 seconds` | speaks when it fires (brain must stay running) |
| `volume up/down` / `set volume to 40` / `mute` | macOS or Linux (pactl/wpctl) speaker volume |
| `what can you do` / `help` | short capability list |
| `what did you say` / `repeat that` | last reply again |
| `nevermind` / `shut up` / `stop talking` / `quiet down` | cancel silently (interrupt, no “okay”) |

After Jarvis answers, follow-up speech is answered without requiring the wake word again.

### Spotify (no app needed)

Playback goes through the **Spotify Web API**, which can only steer an existing
Spotify Connect device. `./start.sh` and **Jarvis.command** download
[spotifyd](https://github.com/Spotifyd/spotifyd) into `services/spotify/bin/`
on first run and start it as a speaker named `JARVIS_SPOTIFY_DEVICE`. That is
what lets music start with no Spotify app open anywhere.

Two one-time steps, both requiring Premium:

```bash
# 1. Web API access (search, play, pause, skip)
#    Create an app at https://developer.spotify.com/dashboard
#    Add redirect URI http://127.0.0.1:8732/callback
#    Put SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET in .env, then:
cd services/brain && .venv/bin/python spotify_auth.py

# 2. The local speaker itself (browser login, once)
./start.sh --spotify-login
```

Double-clicking **Jarvis.command** (or a normal `./start.sh`) will also prompt
for that speaker login the first time, as long as you're at a terminal.

After that every start brings the device up automatically and *play Bohemian
Rhapsody* plays through this machine's speakers. Credentials cache in
`services/spotify/cache/`, so the login is genuinely once.

Skip step 2 and everything still works the old way — you just have to leave
Spotify open on some device. Set `JARVIS_SPOTIFY_LOCAL_DEVICE=off` to never
start it.

### Sonos Amp

If a Sonos speaker is on the LAN, Jarvis sends **speech, Spotify, and volume**
through it instead of the Mac speakers. Discovery is automatic; if you have
more than one, pin it:

```bash
# .env
JARVIS_SONOS_NAME=Amp          # room / device name in the Sonos app
# JARVIS_SONOS_IP=192.168.1.50
# JARVIS_AUDIO_OUTPUT=local    # force Mac speakers
```

Spotify is queued through the Amp the same way the Sonos app does it, so it
takes over from HDMI/TV automatically (no one has to change the source in
the Sonos app). Speech is a short WAV the Amp fetches over HTTP from this
machine (`JARVIS_SONOS_HTTP_PORT`, default 8742). macOS may ask to allow
incoming connections the first time.

### Dashcam clips

Say *clip that* and the last 30 seconds of camera + microphone are written to
`clips/` named by date and time (`clip-Aug-30-2026-9-43pm.mp4`). Also *clip the last 10 seconds*, *clip the last minute*, or the
**Clip 30s** button on the phone panel.

To make that possible a `clipper` service records continuously into a rolling
buffer (`services/clipper/.clipbuf/`, ~90s deep, oldest segments deleted). Only
the requested tail is ever copied out, so saving takes well under a second and
nothing else is kept. Roughly 25–40 MB of disk is in use at any moment.

The camera light stays on while Jarvis runs. Turn the whole thing off with
`JARVIS_CLIP_ENABLED=0`, or record silent video with `JARVIS_CLIP_AUDIO=0`.
Because the recorder holds the camera, *take a picture* pulls its frame from the
buffer instead of fighting for the device.

### Lights / RGB strips

**Feit WiFi A19** (and other Tuya bulbs): pair them on **2.4 GHz** in the Smart Life app, create a project at [iot.tuya.com](https://iot.tuya.com), put `TUYA_API_KEY` / `TUYA_API_SECRET` in `.env`, then:

```bash
services/brain/.venv/bin/python services/brain/tuya_setup.py wizard
```

That writes `tuya/devices.json`. Restart the brain. Then: *turn on the lights*, *make the lights warmer*, *daylight*, *dim the lights*.

A **[WLED](https://kno.wled.ge/)** strip uses `WLED_HOST` and wins if both are set. Philips Hue / Ikea can still go through **Home Assistant** (`HA_URL`, `HA_TOKEN`, `HA_LIGHT_ENTITY`).

### Camera

Say *take a picture* or *what do you see*. Needs `ffmpeg` (or `imagesnap` on macOS / `fswebcam` on Linux). Stills land in `captures/`. *What do you see* also sends the frame to Groq vision (`GROQ_VISION_MODEL`).

### Phone panel + avatar (headless PC)

The box does not need a monitor. The control panel binds to the LAN:

```bash
./start.sh --control     # panel only, so you can start him from a phone
./start.sh --detach      # voice + panel, then exit (systemd / SSH)
```

On your phone, open `https://<pc-ip>:8740` (accept the self-signed cert warning once). `http://` on the same port redirects there. Start / Stop the voice stack, take a photo, save a clip, read the live transcript, watch the timing log. `/avatar` is a full-screen talking visor — drop `idle.png` + `talk.png` into `services/control/web/character/` to use your own 2D model (mouth-open vs idle). Add the URL to the home screen for a standalone “app”.

Optional PIN: `JARVIS_CONTROL_PIN` in `.env`. Keep it on the apartment LAN; do not port-forward it.

#### Live transcript

Everything Jarvis hears and says streams to the panel over SSE, so you can read the conversation from another room. Sentences from one answer are folded into a single line, and phone/typed commands are tagged with where they came from.

#### Talking to him from your phone

Tap **Tap to listen here** once (allow the mic). The page keeps listening in the background and sends each utterance to the Jarvis box (Groq Whisper, falling back to Cartesia). That is a second microphone — the room wake-word mic stays on the whole time, so you can still say *Hey Jarvis* in the room. He answers out loud on the Sonos.

Browsers only grant microphone access on a secure origin. HTTPS is on by default; `start.sh` generates a self-signed certificate. Accept the warning once per device. If you'd rather skip the warning, put the panel behind a real certificate (Tailscale Serve or Cloudflare Tunnel). `http://127.0.0.1:8740` also counts as secure if you are on the Jarvis machine itself.

The **type a command** box next to the mic always works.

Linux box at boot:

```bash
./deploy/install-systemd.sh
# optional: systemctl --user enable --now jarvis.service   # voice on boot
sudo loginctl enable-linger $USER   # required if nobody logs into a GUI
```

`./stop.sh` stops the voice stack but **leaves the phone panel up**. `./stop.sh --all` stops the panel and broker too.

### Add a custom command

```python
# services/brain/commands/ping.py
from commands.base import Command, CommandResult

class PingCommand(Command):
    name = "ping"
    def match(self, text: str) -> CommandResult | None:
        if (text or "").strip().lower() in {"ping", "you there"}:
            return CommandResult(label=self.name, direct_reply="Here.")
        return None
```

Import it and append `PingCommand()` to `REGISTERED` in `commands/__init__.py` (order matters — first match wins). Use `direct_reply` to speak as-is, or `run=lambda: some_client.do()` to call an API and speak the returned string.

## Run

```bash
./start.sh              # macOS: Terminal per service; Linux: background + logs/
./start.sh --bg         # background everywhere → logs/*.log (stays on timing feed)
./start.sh --detach     # background + phone panel, then exit
./start.sh --control    # phone panel only
./start.sh --setup      # venvs + TTS models only
./start.sh --spotify-login   # one-time Spotify device sign-in
./stop.sh               # stop Python voice services (panel stays up)
./stop.sh --broker      # also stop Mosquitto
./stop.sh --all         # voice + panel + broker
```

On macOS you can also double-click **`Jarvis.command`**.

### Linux notes

Not quite one-click: install Docker yourself, then:

```bash
git clone https://github.com/JakeJacobi14/jarvis.git
cd jarvis
cp .env.example .env
# edit .env → set GROQ_API_KEY and CARTESIA_API_KEY (his own keys)
sudo apt install portaudio19-dev python3-venv ffmpeg   # Debian/Ubuntu; see below
./start.sh --detach
# phone: http://<this-box-ip>:8740
```

You may also need PortAudio for mic/speakers:

```bash
# Debian/Ubuntu
sudo apt install portaudio19-dev python3-venv ffmpeg
# Fedora
sudo dnf install portaudio-devel ffmpeg
```

`Jarvis.command` and Terminal auto-open are macOS-only; on Linux use `./start.sh --detach` (or `./start.sh` for a live timing feed).

Wake word and always-on STT use the machine’s default input (or `JARVIS_MIC_DEVICE` in `.env`) and stay open even when a phone or laptop is also listening. The panel’s **Tap to listen here** path uploads from that device’s mic instead of touching the room stream. List devices with `services/mic-wakeword/.venv/bin/python -m sounddevice`, then either `wpctl set-default <id>` / `pactl set-default-source <name>` and restart, or set `JARVIS_MIC_DEVICE` to that index.

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
| `jarvis/stt/text` | STT → brain (wake word / PTT) |
| `jarvis/stt/followup` | STT → brain (post-answer listen) |
| `jarvis/brain/reset` | bench → brain (clear conversation memory) |
| `jarvis/brain/response` | brain → TTS (one sentence per message when streaming) |
| `jarvis/tts/response` | TTS → `done` when queue drains |
| `jarvis/status/tts` | TTS → `speaking` / `idle` (avatar) |
| `jarvis/status/stt` | STT/wake → `listening` / `idle` (avatar) |
| `jarvis/status/brain` | brain → `thinking` / `idle` (avatar) |
| `jarvis/status/clipper` | clipper → `recording` / `saving` / `idle` |
| `jarvis/camera/snap` | phone/other → brain (take a still; optional JSON `{"describe":true}`) |
| `jarvis/clip/save` | brain/panel → clipper (JSON `{"id":…,"seconds":30}`) |
| `jarvis/clip/saved` | clipper → requester (JSON with `name` + real `seconds`) |
| `jarvis/clip/error` | clipper → requester (JSON `{"id":…,"error":…}`) |

The panel also subscribes to `jarvis/stt/text`, `jarvis/stt/followup`, and
`jarvis/brain/response` to build the live transcript, and publishes to
`jarvis/stt/text` when you speak or type from a phone.

## Not done yet

- Robust barge-in while speakers are loud (echo)
- Live2D / Rive lip-sync (PNG pair on `/avatar` is the current 2D path)
- Per-room devices, Hue-direct (use HA or WLED for now)
- Clips are camera-only; no screen recording, and no ring buffer on disk reboot-safe
