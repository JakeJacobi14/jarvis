"""Spoken capability list — so he can answer "what can you do"."""
from __future__ import annotations

import re

from commands.base import Command, CommandResult

_HELP = re.compile(
    r"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?
    (?:please\s+)?
    (?:
        help |
        what(?:'?s|\s+are|\s+is)\s+your\s+(?:commands|skills|abilities) |
        what\s+can\s+you\s+do |
        what\s+do\s+you\s+do |
        what\s+are\s+you\s+(?:able\s+to\s+do|capable\s+of) |
        list\s+(?:your\s+)?commands
    )
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_REPLY = (
    "I can play Spotify, search the web, check the weather, control lights, "
    "take a photo, clip the last thirty seconds, set a timer, and change volume."
)


class HelpCommand(Command):
    name = "help"

    def match(self, text: str) -> CommandResult | None:
        raw = (text or "").strip()
        if not raw or not _HELP.match(raw):
            return None
        return CommandResult(label=self.name, direct_reply=_REPLY)
