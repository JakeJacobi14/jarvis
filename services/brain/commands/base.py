"""Extensible voice commands for Jarvis brain.

Add a new command by:
  1. Creating `commands/<name>.py` with a class that subclasses Command
  2. Instantiating it in `commands/__init__.py` REGISTERED list

Commands run before the normal LLM chat path. They can either speak a
direct reply, or hand search/context back for a short spoken LLM summary.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CommandResult:
    """Outcome of a matched command.

    - direct_reply: speak this text as-is (no LLM).
    - search_query: run web search, then summarize via LLM.
    - spotify_query: search Spotify and play that track.
    - spotify_action: pause | resume | next | previous.
    - label: short tag for timing logs (e.g. "search", "datetime").
    """

    label: str
    direct_reply: str | None = None
    search_query: str | None = None
    spotify_query: str | None = None
    spotify_action: str | None = None


class Command:
    """Base class for Jarvis commands."""

    name: str = "command"

    def match(self, text: str) -> CommandResult | None:
        """Return a CommandResult if this command should handle `text`."""
        raise NotImplementedError
