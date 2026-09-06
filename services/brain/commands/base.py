"""Extensible voice commands for Jarvis brain.

Add a new command by:
  1. Creating `commands/<name>.py` with a class that subclasses Command
  2. Instantiating it in `commands/__init__.py` REGISTERED list

Commands run before the normal LLM chat path. They can speak a direct
reply, run a callable, hand search/context back for a spoken summary,
or set a kind the brain handles (timer / repeat).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommandResult:
    """Outcome of a matched command.

    - direct_reply: speak this text as-is (no LLM).
    - run: call this, then speak the returned text.
    - search_query: run web search, then summarize via LLM.
    - spotify_query: search Spotify and play that track.
    - spotify_action: pause | resume | next | previous.
    - silent: handle with no spoken reply (cancel / shut up).
    - skip_followup: do not open the room mic again after this turn
      (music / barge-in — follow-up would hear the speakers).
    - kind: brain-handled extra (timer, repeat).
    - payload: extra data for kind handlers.
    - label: short tag for timing logs (e.g. "search", "datetime").
    """

    label: str
    direct_reply: str | None = None
    run: Callable[[], str] | None = None
    search_query: str | None = None
    spotify_query: str | None = None
    spotify_action: str | None = None
    silent: bool = False
    skip_followup: bool = False
    kind: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


class Command:
    """Base class for Jarvis commands."""

    name: str = "command"

    def match(self, text: str) -> CommandResult | None:
        """Return a CommandResult if this command should handle `text`."""
        raise NotImplementedError
