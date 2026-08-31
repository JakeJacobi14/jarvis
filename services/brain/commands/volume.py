"""Speaker volume — up/down/mute or a percent."""
from __future__ import annotations

import re

import volume_client
from commands.base import Command, CommandResult

_PREFIX = r"^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?"
_WHERE = r"(?:\s+(?:on|of|for)?\s*(?:the\s+)?(?:sonos\s+)?(?:speakers?|amp|sonos))?"

_UP = re.compile(
    rf"""
    {_PREFIX}
    (?:
        (?:turn|volume)\s+up |
        (?:make\s+it\s+)?(?:louder|louder\s+please) |
        increase\s+(?:the\s+)?volume
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
        (?:turn|volume)\s+down |
        (?:make\s+it\s+)?(?:quieter|softer) |
        decrease\s+(?:the\s+)?volume |
        lower\s+(?:the\s+)?volume
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

# "decrease volume to 10%" / "set volume on sonos speakers to 10 percent"
_SET = re.compile(
    rf"""
    {_PREFIX}
    (?:
        (?:set|turn|change|increase|decrease|lower|raise|put)\s+
        (?:the\s+)?
        (?:sonos\s+)?
        (?:volume|sound)
        {_WHERE}
        \s+(?:down|up)?\s*(?:to|at)
        |
        (?:the\s+)?(?:sonos\s+)?volume{_WHERE}\s*(?:to|at)?
    )
    \s+(?P<n>\d{{1,3}})\s*(?:percent|%)?
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_NUDGE_UP = re.compile(
    rf"""
    {_PREFIX}
    (?:
        (?:turn|volume|increase|raise)\s+up |
        increase\s+(?:the\s+)?volume
    )
    {_WHERE}
    \s+(?:by\s+)?(?P<n>\d{{1,3}})\s*(?:percent|%)?
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_NUDGE_DOWN = re.compile(
    rf"""
    {_PREFIX}
    (?:
        (?:turn|volume|decrease|lower)\s+down |
        decrease\s+(?:the\s+)?volume |
        lower\s+(?:the\s+)?volume
    )
    {_WHERE}
    \s+(?:by\s+)?(?P<n>\d{{1,3}})\s*(?:percent|%)?
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_STATUS = re.compile(
    rf"""
    {_PREFIX}
    (?:
        what(?:'?s|\s+is)\s+(?:the\s+)?(?:sonos\s+)?volume{_WHERE} |
        how\s+loud(?:\s+is\s+it)?{_WHERE} |
        (?:check|get)\s+(?:the\s+)?volume{_WHERE}
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
            n = max(1, min(50, int(m.group("n"))))
            return CommandResult(
                label=self.name, run=lambda d=n: volume_client.nudge(+d)
            )
        m = _NUDGE_DOWN.match(raw)
        if m:
            n = max(1, min(50, int(m.group("n"))))
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
