"""Short human small-talk — never let the LLM go robot-mode."""
from __future__ import annotations

import re

from commands.base import Command, CommandResult

_HOW_ARE_YOU = re.compile(
    r"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?
    (?:please\s+)?
    (?:
        how(?:'?s|\s+are)\s+you(?:\s+(?:doing|feeling|today|now))? |
        how(?:'?s|\s+is)\s+it\s+going |
        how(?:'?s|\s+have)\s+you\s+been |
        you\s+(?:good|alright|okay|ok)
    )
    (?:\s+(?:today|now))?
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_REPLY = "I'm good, sir."


class SmalltalkCommand(Command):
    name = "smalltalk"

    def match(self, text: str) -> CommandResult | None:
        raw = (text or "").strip()
        if not raw or not _HOW_ARE_YOU.match(raw):
            return None
        return CommandResult(label=self.name, direct_reply=_REPLY)
