"""Ask the clipper service to save the last N seconds of footage.

Fire-and-wait over MQTT: the request carries an id, and we block briefly for
the matching `jarvis/clip/saved` / `jarvis/clip/error` so Jarvis can confirm
out loud rather than guess.
"""
from __future__ import annotations

import json
import os
import threading
import uuid

import paho.mqtt.client as mqtt

DEFAULT_SECONDS = 30
# The brain calls this from its MQTT callback thread, so the wait has to stay
# short. A save measures ~1-2s: mostly the clipper letting the in-progress
# segment close. On timeout we say so rather than blocking any longer.
WAIT_SECONDS = 8.0


def _broker() -> tuple[str, int]:
    host = os.environ.get("JARVIS_MQTT_HOST", "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(os.environ.get("JARVIS_MQTT_PORT", "1883"))
    except ValueError:
        port = 1883
    return host, port


def _spoken_length(seconds: int) -> str:
    if seconds % 60 == 0 and seconds >= 60:
        minutes = seconds // 60
        return "the last minute" if minutes == 1 else f"the last {minutes} minutes"
    return f"the last {seconds} seconds"


def save_clip(seconds: int | None = None) -> str:
    """Request a clip and return a spoken confirmation."""
    try:
        length = int(seconds or int(os.environ.get("JARVIS_CLIP_SECONDS", DEFAULT_SECONDS)))
    except (TypeError, ValueError):
        length = DEFAULT_SECONDS
    length = max(1, length)

    request_id = uuid.uuid4().hex[:12]
    done = threading.Event()
    outcome: dict[str, str] = {}

    def on_connect(client, userdata, flags, reason_code, properties=None):
        client.subscribe("jarvis/clip/saved")
        client.subscribe("jarvis/clip/error")
        client.publish(
            "jarvis/clip/save",
            json.dumps({"id": request_id, "seconds": length}),
        )

    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return
        if str(data.get("id") or "") != request_id:
            return
        if msg.topic == "jarvis/clip/saved":
            outcome["ok"] = str(data.get("name") or "")
            outcome["seconds"] = str(data.get("seconds") or "")
        else:
            outcome["error"] = str(data.get("error") or "")
        done.set()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    host, port = _broker()
    try:
        client.connect(host, port, keepalive=30)
    except OSError:
        return "I couldn't reach the clip recorder."
    client.loop_start()
    try:
        done.wait(WAIT_SECONDS)
    finally:
        client.loop_stop()
        try:
            client.disconnect()
        except Exception:
            pass

    if "ok" in outcome:
        # The buffer may have held less than asked for; report what he got.
        try:
            saved = int(outcome.get("seconds") or length)
        except ValueError:
            saved = length
        if abs(saved - length) <= 1:
            saved = length
        return f"Clipped {_spoken_length(saved)}, sir."
    if "error" in outcome:
        reason = outcome["error"]
        if "warming up" in reason.lower():
            return "The buffer is still filling. Give me a few seconds."
        if "recorder isn't running" in reason.lower():
            return "The camera recorder isn't running, so there's nothing to clip."
        return "I couldn't save that clip."
    return "The clip recorder didn't answer. It may not be running."
