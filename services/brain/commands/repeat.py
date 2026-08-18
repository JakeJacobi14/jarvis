"""Repeat the last thing Jarvis said."""
from __future__ import annotations

import re

from commands.base import Command, CommandResult

_REPEAT = re.compile(
    r"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?
    (?:please\s+)?
    (?:
        (?:can\s+you\s+)?repeat(?:\s+that)? |
        (?:say|what\s+did\s+you\s+say)(?:\s+that)?\s+again |
        what\s+did\s+you\s+(?:just\s+)?say |
        come\s+again |
        pardon(?:\s+me)?
    )
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


class RepeatCommand(Command):
    name = "repeat"

    def match(self, text: str) -> CommandResult | None:
        raw = (text or "").strip()
        if not raw or not _REPEAT.match(raw):
            return None
        return CommandResult(label=self.name, kind="repeat")
