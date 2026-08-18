"""Current weather via Open-Meteo (no API key)."""
from __future__ import annotations

import os
from typing import Any

import requests

GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"

_WMO = {
    0: "clear",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "foggy",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    80: "light showers",
    81: "showers",
    82: "heavy showers",
    95: "thunderstorms",
    96: "thunderstorms",
    99: "thunderstorms",
}


class WeatherError(RuntimeError):
    pass


def _geocode(name: str) -> tuple[float, float, str]:
    try:
        r = requests.get(
            GEOCODE,
            params={"name": name, "count": 1, "language": "en", "format": "json"},
            timeout=8,
        )
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        raise WeatherError(f"geocode failed: {e}") from e
    results = data.get("results") or []
    if not results:
        raise WeatherError(f"couldn't find {name!r}")
    hit = results[0]
    label = hit.get("name") or name
    admin = hit.get("admin1") or ""
    if admin and admin.lower() != label.lower():
        label = f"{label}, {admin}"
    return float(hit["latitude"]), float(hit["longitude"]), label


def resolve_location(spoken_place: str | None = None) -> tuple[float, float, str]:
    if spoken_place:
        return _geocode(spoken_place)
    lat = os.environ.get("JARVIS_LAT", "").strip()
    lon = os.environ.get("JARVIS_LON", "").strip()
    if lat and lon:
        label = os.environ.get("JARVIS_PLACE_NAME", "").strip() or "home"
        return float(lat), float(lon), label
    city = os.environ.get("JARVIS_CITY", "").strip()
    if city:
        return _geocode(city)
    raise WeatherError(
        "No location set. Add JARVIS_CITY or JARVIS_LAT/JARVIS_LON to .env."
    )


def forecast(spoken_place: str | None = None) -> str:
    try:
        lat, lon, label = resolve_location(spoken_place)
    except WeatherError as e:
        msg = str(e)
        if "No location" in msg:
            return (
                "I don't know where home is yet. Set JARVIS_CITY in the env file."
            )
        if "couldn't find" in msg:
            return f"I couldn't find {spoken_place}."
        return "Weather lookup failed just now."

    tz = os.environ.get("JARVIS_TIMEZONE", "auto").strip() or "auto"
    try:
        r = requests.get(
            FORECAST,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,weather_code,wind_speed_10m",
                "timezone": tz,
                "wind_speed_unit": "mph",
                "temperature_unit": os.environ.get("JARVIS_TEMP_UNIT", "celsius"),
            },
            timeout=8,
        )
        r.raise_for_status()
        data: dict[str, Any] = r.json()
    except requests.RequestException:
        return "I couldn't reach the weather service."

    current = data.get("current") or {}
    temp = current.get("temperature_2m")
    code = current.get("weather_code")
    wind = current.get("wind_speed_10m")
    if temp is None:
        return "Weather came back empty."
    sky = _WMO.get(int(code) if code is not None else -1, "unknown conditions")
    unit_env = os.environ.get("JARVIS_TEMP_UNIT", "celsius").strip().lower()
    unit_short = "fahrenheit" if unit_env.startswith("f") else "celsius"
    try:
        t = round(float(temp))
    except (TypeError, ValueError):
        t = temp
    wind_bit = ""
    try:
        if wind is not None and float(wind) >= 15:
            wind_bit = f", wind {round(float(wind))} miles per hour"
    except (TypeError, ValueError):
        pass
    place = "here" if label == "home" else f"in {label}"
    return f"It's {t} {unit_short} and {sky} {place}{wind_bit}."
