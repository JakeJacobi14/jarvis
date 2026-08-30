"""Dashcam clips — "clip that" saves the last stretch of camera + mic."""
from __future__ import annotations

import re

import clip_client
from commands.base import Command, CommandResult

_WORD_NUMBERS = {
    "five": 5,
    "ten": 10,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "forty-five": 45,
    "forty five": 45,
    "fifty": 50,
    "sixty": 60,
    "ninety": 90,
    "one": 1,
    "two": 2,
    "three": 3,
    "a": 1,
    "an": 1,
}

# "clip that" / "clip it" / "save that clip" / "make a clip"
_CLIP = re.compile(
    r"""
    ^\s*(?:hey\s+)?(?:jarvis[,.]?\s+)?(?:please\s+)?
    (?:can\s+you\s+|could\s+you\s+|go\s+ahead\s+and\s+)?
    (?:
        clip\s+(?:that|this|it|the\s+last\s+bit)? |
        (?:save|grab|record|make|take)\s+(?:me\s+)?(?:a\s+|that\s+|the\s+)?clip |
        save\s+(?:that|this)\s+(?:to\s+a\s+clip|as\s+a\s+clip)
    )
    (?:\s*[,.]?\s*jarvis)?\s*[.?!]*\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# "clip the last 45 seconds" / "save the last two minutes"
_CLIP_LENGTH = re.compile(
    r"""
    ^\s*(?:hey\s+)?(?:jarvis[,.]?\s+)?(?:please\s+)?
    (?:can\s+you\s+|could\s+you\s+|go\s+ahead\s+and\s+)?
    (?:clip|save|grab|record|keep)\s+
    (?:me\s+)?(?:the\s+|that\s+)?(?:last|past|previous)\s+
    (?:(?P<amount>\d+|[a-z]+(?:\s+[a-z]+)?)\s+)?
    (?P<unit>seconds?|secs?|minutes?|mins?)
    (?:\s*[,.]?\s*jarvis)?\s*[.?!]*\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _amount(raw: str | None) -> int | None:
    """None means the phrase had no number, as in "the last minute"."""
    if raw is None:
        return 1
    raw = raw.strip().lower()
    if raw.isdigit():
        return int(raw)
    return _WORD_NUMBERS.get(raw)


class ClipCommand(Command):
    name = "clip"

    def match(self, text: str) -> CommandResult | None:
        raw = (text or "").strip()
        if not raw:
            return None

        m = _CLIP_LENGTH.match(raw)
        if m:
            amount = _amount(m.group("amount"))
            if amount is None:
                return None
            unit = m.group("unit").lower()
            seconds = amount * 60 if unit.startswith(("minute", "min")) else amount
            return CommandResult(
                label=self.name,
                run=lambda s=seconds: clip_client.save_clip(s),
            )

        if _CLIP.match(raw):
            return CommandResult(label=self.name, run=clip_client.save_clip)
        return None
