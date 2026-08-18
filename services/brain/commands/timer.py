"""Duration timers — "set a timer for 5 minutes", "remind me in 30 seconds"."""
from __future__ import annotations

import re

from commands.base import Command, CommandResult

_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "forty-five": 45,
    "forty five": 45,
    "sixty": 60,
}

_TIMER = re.compile(
    r"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?
    (?:please\s+)?
    (?:
        (?:set\s+(?:a\s+|an\s+)?)?(?:timer|alarm)
            (?:\s+for|\s+in)? |
        remind\s+me(?:\s+in)?
    )
    \s+(?P<num>\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|
        fifteen|twenty|thirty|forty(?:[-\s]five)?|sixty)
    \s+(?P<unit>seconds?|secs?|minutes?|mins?|hours?|hrs?)
    (?:\s+(?:to|for)\s+(?P<label>.+?))?
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_MAX_SECONDS = 12 * 3600


def _to_int(raw: str) -> int | None:
    raw = raw.strip().lower()
    if raw.isdigit():
        return int(raw)
    return _WORDS.get(raw.replace("-", " "))


def _unit_seconds(unit: str) -> int:
    u = unit.lower()
    if u.startswith("sec"):
        return 1
    if u.startswith("min"):
        return 60
    return 3600


def _spoken_duration(seconds: int) -> str:
    if seconds < 60:
        n = seconds
        return f"{n} second{'s' if n != 1 else ''}"
    if seconds % 3600 == 0:
        n = seconds // 3600
        return f"{n} hour{'s' if n != 1 else ''}"
    if seconds % 60 == 0:
        n = seconds // 60
        return f"{n} minute{'s' if n != 1 else ''}"
    mins, secs = divmod(seconds, 60)
    return f"{mins} minutes {secs} seconds"


class TimerCommand(Command):
    name = "timer"

    def match(self, text: str) -> CommandResult | None:
        raw = (text or "").strip().strip(".?!,")
        if not raw:
            return None
        m = _TIMER.match(raw)
        if not m:
            return None
        n = _to_int(m.group("num"))
        if not n or n <= 0:
            return None
        seconds = n * _unit_seconds(m.group("unit"))
        if seconds > _MAX_SECONDS:
            return CommandResult(
                label=self.name,
                direct_reply="I can only set timers up to twelve hours.",
            )
        label = (m.group("label") or "").strip(" .?!,")
        confirm = f"Timer set for {_spoken_duration(seconds)}."
        done = f"Time's up. {label}." if label else "Time's up."
        return CommandResult(
            label=self.name,
            kind="timer",
            payload={"seconds": seconds, "confirm": confirm, "done": done},
        )
