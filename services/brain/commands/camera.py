"""Take one still from the connected camera; optionally describe it."""
from __future__ import annotations

import re

import camera_client
from commands.base import Command, CommandResult

_SNAP = re.compile(
    r"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?
    (?:
        (?:can\s+you\s+|could\s+you\s+)?
        (?:take|snap|capture)\s+(?:a\s+|one\s+)?(?:picture|photo|pic|shot|image) |
        take\s+a\s+look |
        screenshot\s+(?:the\s+)?(?:camera|room)
    )
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_SEE = re.compile(
    r"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?
    (?:
        what\s+do\s+you\s+see |
        what(?:'?s|\s+is)\s+(?:on\s+(?:the\s+)?camera|in\s+(?:front\s+of\s+you|the\s+room|view)) |
        look\s+around |
        describe\s+(?:the\s+)?(?:room|scene|view|photo)
    )
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


class CameraCommand(Command):
    name = "camera"

    def match(self, text: str) -> CommandResult | None:
        raw = (text or "").strip()
        if not raw:
            return None
        if _SEE.match(raw):
            return CommandResult(
                label=self.name,
                run=lambda: camera_client.snap_and_maybe_describe(True),
            )
        if _SNAP.match(raw):
            return CommandResult(
                label=self.name,
                run=lambda: camera_client.snap_and_maybe_describe(False),
            )
        return None
