"""Spotify playback via Web API.

Triggers:
  play … / put on … / spotify …
  pause / stop music / resume / skip / next / previous
"""
from __future__ import annotations

import re

from commands.base import Command, CommandResult

_PLAY = re.compile(
    r"""^\s*
        (?:hey\s+jarvis[,.]?\s+)?
        (?:please\s+)?
        (?:
            play(?:\s+me)? |
            put\s+on |
            spotify
        )
        \s+(?P<q>.+?)
        \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Avoid treating "play along" / game language as music when bare.
_PLAY_BLOCKLIST = re.compile(
    r"^\s*(?:a\s+game|along|nice|fair|ball|with\s+me)\b",
    re.IGNORECASE,
)

# STT often keeps trailing politeness in the query.
_TRAILING_FILLER = re.compile(
    r"""
        (?:
            \s+,?\s*
            (?:
                please |
                thanks |
                thank\s+you |
                for\s+me |
                real\s+quick |
                now
            )
        )+
        \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _clean_play_query(q: str) -> str:
    q = (q or "").strip(" .?!,")
    q = _TRAILING_FILLER.sub("", q).strip(" .?!,")
    return q


_CONTROL = [
    (re.compile(p, re.IGNORECASE), action)
    for p, action in (
        (r"^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?(?:pause(?:\s+music|\s+spotify)?|stop(?:\s+music|\s+spotify)?|halt)\s*[.!]?\s*$", "pause"),
        (r"^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?(?:resume(?:\s+music|\s+spotify)?|continue(?:\s+music)?|unpause)\s*[.!]?\s*$", "resume"),
        (r"^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?(?:skip(?:\s+(?:this\s+)?(?:song|track))?|next(?:\s+song|\s+track)?)\s*[.!]?\s*$", "next"),
        (r"^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?(?:previous(?:\s+song|\s+track)?|last\s+song|go\s+back)\s*[.!]?\s*$", "previous"),
    )
]


class SpotifyCommand(Command):
    name = "spotify"

    def match(self, text: str) -> CommandResult | None:
        raw = (text or "").strip()
        if not raw:
            return None

        for pat, action in _CONTROL:
            if pat.match(raw):
                return CommandResult(label=self.name, spotify_action=action)

        m = _PLAY.match(raw)
        if m:
            q = _clean_play_query(m.group("q"))
            if q and not _PLAY_BLOCKLIST.match(q):
                return CommandResult(label=self.name, spotify_query=q)

        return None
