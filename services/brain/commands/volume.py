"""Speaker volume — up/down/mute or a percent. Sonos is implied; no need to name it."""
from __future__ import annotations

import re

import volume_client
from commands.base import Command, CommandResult

_PREFIX = r"^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?"
# "the volume" / "the sonos speaker volume" / "speaker volumes"
_VOL = (
    r"(?:the\s+)?"
    r"(?:sonos\s+)?"
    r"(?:(?:speakers?|amp)s?\s+)?"
    r"(?:volume|volumes|sound)"
)
_WHERE = r"(?:\s+(?:on|of|for)?\s*(?:the\s+)?(?:sonos\s+)?(?:speakers?|amp|sonos))?"
_PCT = r"(?P<n>\d{1,3})\s*(?:percent|%)?"

_UP = re.compile(
    rf"""
    {_PREFIX}
    (?:
        turn\s+{_VOL}\s+up |
        turn\s+up\s+{_VOL} |
        {_VOL}\s+up |
        (?:increase|raise)\s+(?:the\s+)?volume |
        (?:turn|volume)\s+up |
        (?:make\s+it\s+)?(?:louder)(?:\s+please)?
    )
    {_WHERE}
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_DOWN = re.compile(
    rf"""
    {_PREFIX}
    (?:
        turn\s+{_VOL}\s+down |
        turn\s+down\s+{_VOL} |
        {_VOL}\s+down |
        (?:decrease|lower)\s+(?:the\s+)?volume |
        (?:turn|volume)\s+down |
        (?:make\s+it\s+)?(?:quieter|softer)
    )
    {_WHERE}
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_MUTE = re.compile(
    rf"{_PREFIX}mute(?:\s+(?:the\s+)?(?:volume|sound|speakers?))?{_WHERE}\s*[.?!]?\s*$",
    re.IGNORECASE,
)
_UNMUTE = re.compile(
    rf"{_PREFIX}unmute(?:\s+(?:the\s+)?(?:volume|sound|speakers?))?{_WHERE}\s*[.?!]?\s*$",
    re.IGNORECASE,
)

# "set volume to 40" / "turn the volume up to 50%" / "volume 40"
_SET = re.compile(
    rf"""
    {_PREFIX}
    (?:
        (?:set|change|put)\s+{_VOL}{_WHERE}\s+(?:to|at) |
        (?:turn|increase|decrease|lower|raise)\s+{_VOL}{_WHERE}\s+(?:up|down)?\s*(?:to|at) |
        {_VOL}{_WHERE}\s*(?:to|at)?
    )
    \s+{_PCT}
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# "turn the volume up 50%" / "turn up the volume by 10" / "volume up 20%"
_NUDGE_UP = re.compile(
    rf"""
    {_PREFIX}
    (?:
        turn\s+{_VOL}\s+up |
        turn\s+up\s+{_VOL} |
        {_VOL}\s+up |
        (?:increase|raise)\s+{_VOL} |
        (?:turn|increase|raise)\s+up
    )
    {_WHERE}
    \s+(?:by\s+)?{_PCT}
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_NUDGE_DOWN = re.compile(
    rf"""
    {_PREFIX}
    (?:
        turn\s+{_VOL}\s+down |
        turn\s+down\s+{_VOL} |
        {_VOL}\s+down |
        (?:decrease|lower)\s+{_VOL} |
        (?:turn|decrease|lower)\s+down
    )
    {_WHERE}
    \s+(?:by\s+)?{_PCT}
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_STATUS = re.compile(
    rf"""
    {_PREFIX}
    (?:
        what(?:'?s|\s+is)\s+{_VOL}{_WHERE} |
        how\s+loud(?:\s+is\s+it)?{_WHERE} |
        (?:check|get)\s+{_VOL}{_WHERE}
    )
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


class VolumeCommand(Command):
    name = "volume"

    def match(self, text: str) -> CommandResult | None:
        raw = (text or "").strip()
        if not raw:
            return None
        if _STATUS.match(raw):
            return CommandResult(label=self.name, run=volume_client.status_reply)
        if _MUTE.match(raw):
            return CommandResult(
                label=self.name, run=lambda: volume_client.set_muted(True)
            )
        if _UNMUTE.match(raw):
            return CommandResult(
                label=self.name, run=lambda: volume_client.set_muted(False)
            )
        m = _SET.match(raw)
        if m:
            n = max(0, min(100, int(m.group("n"))))
            return CommandResult(
                label=self.name, run=lambda pct=n: volume_client.set_percent(pct)
            )
        m = _NUDGE_UP.match(raw)
        if m:
            n = max(1, min(100, int(m.group("n"))))
            return CommandResult(
                label=self.name, run=lambda d=n: volume_client.nudge(+d)
            )
        m = _NUDGE_DOWN.match(raw)
        if m:
            n = max(1, min(100, int(m.group("n"))))
            return CommandResult(
                label=self.name, run=lambda d=n: volume_client.nudge(-d)
            )
        if _UP.match(raw):
            return CommandResult(
                label=self.name, run=lambda: volume_client.nudge(+10)
            )
        if _DOWN.match(raw):
            return CommandResult(
                label=self.name, run=lambda: volume_client.nudge(-10)
            )
        return None
