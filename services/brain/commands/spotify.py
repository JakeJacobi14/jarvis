"""Spotify playback via Web API.

Triggers:
  play … / put on … / spotify … (including mid-sentence after STT junk)
  can you play …
  pause / stop music / resume / skip / next / previous
"""
from __future__ import annotations

import re

from commands.base import Command, CommandResult

# Leading junk STT sometimes prepends ("service. Play …", "plague play …")
_PLAY = re.compile(
    r"""
        (?:^|.*?\b)
        (?:please\s+)?
        (?:
            (?:can|could|would)\s+you\s+(?:please\s+)?play(?:\s+me)? |
            play(?:\s+me)? |
            put\s+on |
            spotify
        )
        \s+(?P<q>.+?)
        \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Bare "play music" / "play a song" with no title
_PLAY_GENERIC = re.compile(
    r"""
        (?:^|.*?\b)
        (?:please\s+)?
        (?:can|could|would)\s+you\s+(?:please\s+)?play
        (?:\s+(?:some\s+)?(?:music|songs?|something))?
        \s*$
    |
        (?:^|.*?\b)
        (?:please\s+)?play
        \s+(?:some\s+)?(?:music|songs?|something|a\s+song)
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

_GENERIC_QUERIES = {
    "music",
    "some music",
    "a song",
    "songs",
    "some songs",
    "something",
    "something good",
    "my music",
}


def _clean_play_query(q: str) -> str:
    q = (q or "").strip(" .?!,")
    q = _TRAILING_FILLER.sub("", q).strip(" .?!,")
    return q


_CONTROL = [
    (re.compile(p, re.IGNORECASE | re.VERBOSE), action)
    for p, action in (
        (
            r"""
            (?:^|.*?\b)
            (?:please\s+)?
            (?:
                pause(?:\s+(?:the\s+)?(?:music|song|track|spotify))? |
                stop(?:\s+(?:the\s+)?(?:music|song|track|spotify|playing))? |
                halt
            )
            \s*[.!]?\s*$
            """,
            "pause",
        ),
        (
            r"""
            (?:^|.*?\b)
            (?:please\s+)?
            (?:
                resume(?:\s+(?:the\s+)?(?:music|song|spotify))? |
                continue(?:\s+(?:the\s+)?(?:music|song))? |
                unpause
            )
            \s*[.!]?\s*$
            """,
            "resume",
        ),
        (
            r"""
            (?:^|.*?\b)
            (?:please\s+)?
            (?:
                skip(?:\s+(?:this\s+)?(?:song|track))? |
                next(?:\s+(?:song|track))?
            )
            \s*[.!]?\s*$
            """,
            "next",
        ),
        (
            r"""
            (?:^|.*?\b)
            (?:please\s+)?
            (?:
                previous(?:\s+(?:song|track))? |
                last\s+song |
                go\s+back
            )
            \s*[.!]?\s*$
            """,
            "previous",
        ),
    )
]


class SpotifyCommand(Command):
    name = "spotify"

    def match(self, text: str) -> CommandResult | None:
        raw = (text or "").strip()
        if not raw:
            return None

        # Common Ink-2 mishear: "play" → "plague"
        raw = re.sub(r"\bplague\b", "play", raw, flags=re.IGNORECASE)

        for pat, action in _CONTROL:
            if pat.match(raw):
                return CommandResult(
                    label=self.name, spotify_action=action, skip_followup=True
                )

        # "play music" with no title → resume / ask
        if _PLAY_GENERIC.search(raw):
            return CommandResult(
                label=self.name, spotify_action="resume_or_ask", skip_followup=True
            )

        m = _PLAY.search(raw)
        if m:
            q = _clean_play_query(m.group("q"))
            if q.lower() in _GENERIC_QUERIES:
                return CommandResult(
                    label=self.name, spotify_action="resume_or_ask", skip_followup=True
                )
            if q and not _PLAY_BLOCKLIST.match(q):
                return CommandResult(
                    label=self.name, spotify_query=q, skip_followup=True
                )

        return None
