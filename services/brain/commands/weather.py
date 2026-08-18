"""Local weather via Open-Meteo — matches before the Tavily search catch-all."""
from __future__ import annotations

import re

import weather_client
from commands.base import Command, CommandResult

_WEATHER = re.compile(
    r"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?
    (?:please\s+)?
    (?:
        (?:what(?:'?s|\s+is)|\s*how(?:'?s|\s+is))\s+(?:the\s+)?weather
        (?:\s+like)?
        (?:\s+(?:like\s+)?(?:in|for|at)\s+(?P<place1>.+))?
        |
        weather(?:\s+(?:in|for|at)\s+(?P<place2>.+))?
        |
        (?:is\s+it|will\s+it)\s+(?:rain(?:ing)?|snow(?:ing)?|sunny|cold|hot|warm)
            (?:\s+(?:in|for|at)\s+(?P<place3>.+))?
        |
        temperature(?:\s+(?:in|for|at)\s+(?P<place4>.+))?
        |
        forecast(?:\s+(?:in|for|at)\s+(?P<place5>.+))?
    )
    (?:\s+(?:like|today|tonight|outside|right\s+now))?
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _clean_place(raw: str | None) -> str | None:
    if not raw:
        return None
    place = raw.strip(" .?!,")
    place = re.sub(
        r"^\s*(?:like\s+)?(?:today|tonight|right\s+now|outside)\s*",
        "",
        place,
        flags=re.IGNORECASE,
    )
    place = place.strip(" .?!,")
    if not place or place.lower() in {"today", "tonight", "outside", "here", "home"}:
        return None
    return place


class WeatherCommand(Command):
    name = "weather"

    def match(self, text: str) -> CommandResult | None:
        raw = (text or "").strip().strip(".?!")
        if not raw:
            return None
        m = _WEATHER.match(raw)
        if not m:
            return None
        place = None
        for key in ("place1", "place2", "place3", "place4", "place5"):
            place = _clean_place(m.groupdict().get(key))
            if place:
                break
        return CommandResult(
            label=self.name,
            run=lambda p=place: weather_client.forecast(p),
        )
