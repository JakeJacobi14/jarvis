"""WLED RGB strips and optional Home Assistant lights."""
from __future__ import annotations

import os
from typing import Any

import requests

COLORS: dict[str, tuple[int, int, int]] = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 80, 255),
    "white": (255, 255, 255),
    "warm": (255, 170, 80),
    "warm white": (255, 170, 80),
    "cool": (180, 210, 255),
    "cool white": (180, 210, 255),
    "orange": (255, 90, 0),
    "yellow": (255, 200, 0),
    "purple": (160, 0, 255),
    "pink": (255, 40, 140),
    "cyan": (0, 220, 255),
    "magenta": (255, 0, 180),
    "amber": (255, 140, 0),
}


class LightsError(RuntimeError):
    pass


def configured() -> bool:
    return bool(
        os.environ.get("WLED_HOST", "").strip()
        or (
            os.environ.get("HA_URL", "").strip()
            and os.environ.get("HA_TOKEN", "").strip()
            and os.environ.get("HA_LIGHT_ENTITY", "").strip()
        )
    )


def _wled_base() -> str | None:
    host = os.environ.get("WLED_HOST", "").strip().rstrip("/")
    if not host:
        return None
    if host.startswith("http://") or host.startswith("https://"):
        return host
    return f"http://{host}"


def _ha() -> tuple[str, str, str] | None:
    url = os.environ.get("HA_URL", "").strip().rstrip("/")
    token = os.environ.get("HA_TOKEN", "").strip()
    entity = os.environ.get("HA_LIGHT_ENTITY", "").strip()
    if url and token and entity:
        return url, token, entity
    return None


def _wled_state(body: dict[str, Any]) -> dict[str, Any]:
    base = _wled_base()
    if not base:
        raise LightsError("WLED_HOST is not set")
    try:
        r = requests.post(f"{base}/json/state", json=body, timeout=5)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise LightsError(f"WLED failed: {e}") from e


def _wled_get() -> dict[str, Any]:
    base = _wled_base()
    if not base:
        raise LightsError("WLED_HOST is not set")
    try:
        r = requests.get(f"{base}/json/state", timeout=5)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise LightsError(f"WLED failed: {e}") from e


def _ha_call(service: str, body: dict[str, Any]) -> None:
    ha = _ha()
    if not ha:
        raise LightsError("Home Assistant is not configured")
    url, token, entity = ha
    domain, _, action = service.partition("/")
    payload = {"entity_id": entity, **body}
    try:
        r = requests.post(
            f"{url}/api/services/{domain}/{action}",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=8,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        raise LightsError(f"Home Assistant failed: {e}") from e


def _pct_to_bri(pct: int) -> int:
    return max(1, min(255, round(int(pct) * 255 / 100)))


def apply(
    *,
    action: str,
    color: str | None = None,
    brightness: int | None = None,
) -> str:
    if not configured():
        return (
            "Lights aren't set up yet. Add WLED_HOST or Home Assistant settings "
            "to the env file."
        )
    try:
        if _wled_base():
            return _apply_wled(action=action, color=color, brightness=brightness)
        return _apply_ha(action=action, color=color, brightness=brightness)
    except LightsError:
        return "The lights didn't respond."


def _apply_wled(*, action: str, color: str | None, brightness: int | None) -> str:
    if action == "status":
        state = _wled_get()
        on = bool(state.get("on"))
        bri = int(state.get("bri") or 0)
        pct = round(bri * 100 / 255) if bri else 0
        if not on:
            return "The lights are off."
        return f"The lights are on at {pct} percent."

    body: dict[str, Any] = {}
    if action == "off":
        body["on"] = False
        _wled_state(body)
        return "Lights off."
    if action == "on":
        body["on"] = True
        _wled_state(body)
        return "Lights on."
    if action == "rainbow":
        body = {"on": True, "seg": [{"fx": 9, "sx": 128, "ix": 128}]}
        _wled_state(body)
        return "Rainbow on the strip."

    body["on"] = True
    if brightness is not None:
        body["bri"] = _pct_to_bri(brightness)
    if color:
        rgb = COLORS.get(color.lower())
        if rgb:
            body.setdefault("seg", [{}])
            body["seg"][0]["col"] = [list(rgb)]
            body["seg"][0]["fx"] = 0
    _wled_state(body)
    if color and brightness is not None:
        return f"Lights {color} at {brightness} percent."
    if color:
        return f"Lights are {color}."
    if brightness is not None:
        return f"Brightness {brightness} percent."
    return "Done."


def _apply_ha(*, action: str, color: str | None, brightness: int | None) -> str:
    if action == "status":
        return "I can switch them, but I don't read Home Assistant state yet."
    if action == "off":
        _ha_call("light/turn_off", {})
        return "Lights off."
    if action == "rainbow":
        return "Rainbow needs a WLED strip. Home Assistant lights stay solid."
    body: dict[str, Any] = {}
    if brightness is not None:
        body["brightness_pct"] = max(1, min(100, int(brightness)))
    if color:
        rgb = COLORS.get(color.lower())
        if rgb:
            body["rgb_color"] = list(rgb)
    _ha_call("light/turn_on", body)
    if action == "on" and not color and brightness is None:
        return "Lights on."
    if color and brightness is not None:
        return f"Lights {color} at {brightness} percent."
    if color:
        return f"Lights are {color}."
    if brightness is not None:
        return f"Brightness {brightness} percent."
    return "Lights on."
