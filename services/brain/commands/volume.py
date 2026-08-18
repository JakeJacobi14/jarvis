"""Speaker volume — up/down/mute or a percent."""
from __future__ import annotations

import re

import volume_client
from commands.base import Command, CommandResult

_UP = re.compile(
    r"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?
    (?:
        (?:turn|volume)\s+up |
        (?:make\s+it\s+)?(?:louder|louder\s+please) |
        increase\s+(?:the\s+)?volume
    )
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_DOWN = re.compile(
    r"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?
    (?:
        (?:turn|volume)\s+down |
        (?:make\s+it\s+)?(?:quieter|softer) |
        decrease\s+(?:the\s+)?volume |
        lower\s+(?:the\s+)?volume
    )
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_MUTE = re.compile(
    r"^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?mute(?:\s+(?:the\s+)?(?:volume|sound|speakers?))?\s*[.?!]?\s*$",
    re.IGNORECASE,
)
_UNMUTE = re.compile(
    r"^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?unmute(?:\s+(?:the\s+)?(?:volume|sound|speakers?))?\s*[.?!]?\s*$",
    re.IGNORECASE,
)

_SET = re.compile(
    r"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?
    (?:set\s+)?(?:the\s+)?volume(?:\s+to)?\s+
    (?P<n>\d{1,3})\s*(?:percent|%)?
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_STATUS = re.compile(
    r"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?
    (?:what(?:'?s|\s+is)\s+(?:the\s+)?volume |
       how\s+loud(?:\s+is\s+it)? )
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
        if _UP.match(raw):
            return CommandResult(
                label=self.name, run=lambda: volume_client.nudge(+10)
            )
        if _DOWN.match(raw):
            return CommandResult(
                label=self.name, run=lambda: volume_client.nudge(-10)
            )
        m = _SET.match(raw)
        if m:
            n = int(m.group("n"))
            n = max(0, min(100, n))
            return CommandResult(
                label=self.name, run=lambda pct=n: volume_client.set_percent(pct)
            )
        return None
