"""Local date/time — no web needed, and never invent these.

Examples: "what day is it", "what's today's date", "what time is it"
"""
from __future__ import annotations

import re
from datetime import datetime

from commands.base import Command, CommandResult

_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bwhat(?:'?s|\s+is)\s+(?:the\s+)?(?:current\s+)?(?:day|date|time)\b",
        r"\bwhat\s+(?:day|time|date)\s+is\s+it\b",
        r"\bwhat\s+day\s+of\s+the\s+week\b",
        r"\bwhat(?:'?s|\s+is)\s+today(?:'?s)?\s+date\b",
        r"\btell\s+me\s+(?:the\s+)?(?:time|date|day)\b",
        r"\b(?:current\s+)?(?:time|date)\s+please\b",
    )
]


class DateTimeCommand(Command):
    name = "datetime"

    def match(self, text: str) -> CommandResult | None:
        raw = (text or "").strip()
        if not raw:
            return None
        if not any(p.search(raw) for p in _PATTERNS):
            return None

        now = datetime.now()
        lower = raw.lower()
        wants_time = "time" in lower and "date" not in lower and "day" not in lower
        wants_day = "day" in lower and "date" not in lower and "time" not in lower

        if wants_time:
            reply = f"It's {now.strftime('%-I:%M %p')}."
        elif wants_day:
            reply = f"It's {now.strftime('%A')}."
        else:
            # date, or mixed day/date/time questions
            if "time" in lower:
                reply = (
                    f"It's {now.strftime('%A')}, "
                    f"{now.strftime('%B %-d')}, "
                    f"{now.strftime('%-I:%M %p')}."
                )
            else:
                reply = f"Today is {now.strftime('%A')}, {now.strftime('%B %-d, %Y')}."

        return CommandResult(label=self.name, direct_reply=reply)
