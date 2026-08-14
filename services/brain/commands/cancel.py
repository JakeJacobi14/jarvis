"""Silent cancel / shut-up — never speak a confirmation."""
from __future__ import annotations

import re

from commands.base import Command, CommandResult

# "nevermind", "forget it", "shut up", "stop talking", etc.
_CANCEL = re.compile(
    r"""
    (?:^|.*?\b)
    (?:
        never\s*mind |
        forget\s+(?:it|that|what\s+i\s+said) |
        cancel\s+(?:that|it)? |
        scratch\s+that |
        shut\s+up |
        stop\s+talking |
        be\s+quiet |
        quiet\s+down |
        silence
    )
    (?:\s+jarvis)?
    (?:\s*,?\s*please)?
    \s*[.!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Mid-utterance abort: "how tall is the… nevermind"
_EMBEDDED_NEVERMIND = re.compile(
    r"\b(?:never\s*mind|forget\s+it|scratch\s+that)\b",
    re.IGNORECASE,
)


class CancelCommand(Command):
    name = "cancel"

    def match(self, text: str) -> CommandResult | None:
        raw = (text or "").strip()
        if not raw:
            return None
        if _CANCEL.search(raw) or _EMBEDDED_NEVERMIND.search(raw):
            return CommandResult(label=self.name, silent=True)
        return None
