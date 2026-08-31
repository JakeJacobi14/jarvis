"""Voice-command matching tests (no MQTT, no network I/O)."""
from __future__ import annotations

import sys
from pathlib import Path

from commands import match_command

_IR_LOCAL = Path(__file__).resolve().parents[2] / "ir-local"
if _IR_LOCAL.is_dir():
    sys.path.insert(0, str(_IR_LOCAL))


def _label(text: str) -> str | None:
    r = match_command(text)
    return None if r is None else r.label


def test_cancel():
    assert _label("nevermind") == "cancel"
    assert _label("shut up") == "cancel"
    assert _label("stop talking") == "cancel"


def test_smalltalk():
    r = match_command("how are you today")
    assert r is not None and r.label == "smalltalk"
    assert r.direct_reply == "I'm good, sir."
    assert _label("how's it going") == "smalltalk"
    assert _label("how are you") == "smalltalk"


def test_help():
    r = match_command("what can you do")
    assert r is not None and r.label == "help" and r.direct_reply
    assert _label("help") == "help"


def test_repeat():
    r = match_command("what did you say")
    assert r is not None and r.kind == "repeat"


def test_timer():
    r = match_command("set a timer for 5 minutes")
    assert r is not None and r.kind == "timer"
    assert r.payload["seconds"] == 300
    r = match_command("remind me in 30 seconds to check the oven")
    assert r is not None and r.payload["seconds"] == 30
    assert "oven" in r.payload["done"]


def test_volume():
    r = match_command("volume up")
    assert r is not None and r.label == "volume" and r.run
    r = match_command("set volume to 40 percent")
    assert r is not None and r.label == "volume"
    r = match_command("volume 40")
    assert r is not None and r.label == "volume"
    r = match_command("decrease volume to 10%")
    assert r is not None and r.label == "volume" and r.run
    r = match_command("decrease volume on sonos speakers to 10%")
    assert r is not None and r.label == "volume" and r.run
    r = match_command("what's the volume on the sonos speakers")
    assert r is not None and r.label == "volume" and r.run
    r = match_command("turn up sonos speakers 10%")
    assert r is not None and r.label == "volume" and r.run
    r = match_command("turn up sonos by 10%")
    assert r is not None and r.label == "volume" and r.run
    r = match_command("turn up sonos speakers")
    assert r is not None and r.label == "volume" and r.run


def test_ir_tv_ac():
    if not (_IR_LOCAL / "ir_devices.py").is_file():
        return
    assert _label("turn on the tv") == "ir"
    assert _label("turn off the television") == "ir"
    assert _label("turn on the ac") == "ir"
    assert _label("turn off the air conditioner") == "ir"


def test_gree_checksum():
    if not (_IR_LOCAL / "ir_client.py").is_file():
        return
    import ir_client

    state = ir_client.gree_state(power=True, temp_c=24)
    assert len(state) == 8
    assert (state[7] >> 4) == ir_client.gree_checksum(state)
    raw = ir_client.gree_timings(state)
    assert len(raw) > 80
    assert raw[0] == ir_client.GREE_HDR_MARK


def test_lights():
    assert _label("turn on the lights") == "lights"
    assert _label("lights off") == "lights"
    assert _label("set the lights to red") == "lights"
    assert _label("set the lights to warm white") == "lights"
    assert _label("set the lights to daylight") == "lights"
    assert _label("make the lights rainbow") == "lights"
    assert _label("make the lights warmer") == "lights"
    assert _label("make the lights cooler") == "lights"
    assert _label("set warmth to 30 percent") == "lights"
    assert _label("set the lights to 2700k") == "lights"


def test_camera():
    assert _label("take a picture") == "camera"
    assert _label("what do you see") == "camera"


def test_weather():
    assert _label("what's the weather") == "weather"
    assert _label("what's the weather like") == "weather"
    assert _label("weather in London") == "weather"


def test_datetime():
    assert _label("what time is it") == "datetime"
    assert _label("what day is it") == "datetime"


def test_spotify_still_wins_play():
    r = match_command("play bohemian rhapsody")
    assert r is not None and r.spotify_query


def test_search_still_wins_google():
    r = match_command("google the tallest mountain")
    assert r is not None and r.search_query


def test_unmatched_falls_through():
    assert match_command("tell me a fun fact about octopuses") is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all command tests passed")
