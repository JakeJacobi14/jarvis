"""Command registry — order matters (first match wins)."""
from __future__ import annotations

from commands.base import Command, CommandResult
from commands.camera import CameraCommand
from commands.cancel import CancelCommand
from commands.datetime_cmd import DateTimeCommand
from commands.help_cmd import HelpCommand
from commands.lights import LightsCommand
from commands.repeat import RepeatCommand
from commands.search import SearchCommand
from commands.smalltalk import SmalltalkCommand
from commands.spotify import SpotifyCommand
from commands.timer import TimerCommand
from commands.volume import VolumeCommand
from commands.weather import WeatherCommand

# First match wins. Cancel/silence before anything that might speak.
REGISTERED: list[Command] = [
    CancelCommand(),
    RepeatCommand(),
    SmalltalkCommand(),
    HelpCommand(),
    DateTimeCommand(),
    TimerCommand(),
    VolumeCommand(),
    LightsCommand(),
    CameraCommand(),
    WeatherCommand(),
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
