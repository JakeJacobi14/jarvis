"""Command registry — order matters (first match wins)."""
from __future__ import annotations

from commands.base import Command, CommandResult
from commands.cancel import CancelCommand
from commands.datetime_cmd import DateTimeCommand
from commands.search import SearchCommand
from commands.spotify import SpotifyCommand

# First match wins. Cancel/silence before anything that might speak.
REGISTERED: list[Command] = [
    CancelCommand(),
    DateTimeCommand(),
    SpotifyCommand(),
    SearchCommand(),
]


def match_command(text: str) -> CommandResult | None:
    """Return the first command result that matches `text`, or None."""
    for cmd in REGISTERED:
        result = cmd.match(text)
        if result is not None:
            return result
    return None
