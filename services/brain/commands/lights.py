"""RGB strip / smart lights — WLED, Feit/Tuya, then Home Assistant."""
from __future__ import annotations

import re

import lights_client
from commands.base import Command, CommandResult

_COLOR_NAMES = "|".join(
    re.escape(n)
    for n in sorted(lights_client.COLORS.keys(), key=len, reverse=True)
)

_ON = re.compile(
    r"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?
    (?:
        (?:turn|switch)\s+(?:the\s+)?(?:lights?|strip|leds?)\s+on |
        (?:turn|switch)\s+on\s+(?:the\s+)?(?:lights?|strip|leds?) |
        lights?\s+on
    )
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_OFF = re.compile(
    r"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?
    (?:
        (?:turn|switch)\s+(?:the\s+)?(?:lights?|strip|leds?)\s+off |
        (?:turn|switch)\s+off\s+(?:the\s+)?(?:lights?|strip|leds?) |
        lights?\s+off |
        kill\s+(?:the\s+)?(?:lights?|strip)
    )
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_STATUS = re.compile(
    r"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?
    (?:
        (?:are|is)\s+(?:the\s+)?(?:lights?|strip)\s+on |
        what(?:'?s|\s+is)\s+(?:the\s+)?(?:light|lights|strip)\s+(?:doing|status|at)
    )
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_RAINBOW = re.compile(
    r"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?
    (?:
        (?:make\s+(?:the\s+)?)?(?:lights?|strip)\s+(?:rainbow|party) |
        rainbow\s+(?:mode|lights?|strip)? |
        party\s+mode
    )
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_WARMER = re.compile(
    r"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?
    (?:
        (?:make\s+(?:the\s+)?)?(?:lights?|bulbs?)\s+warmer |
        warmer\s+(?:the\s+)?(?:lights?|bulbs?)
    )
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_COOLER = re.compile(
    r"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?
    (?:
        (?:make\s+(?:the\s+)?)?(?:lights?|bulbs?)\s+(?:cooler|colder) |
        (?:cooler|colder)\s+(?:the\s+)?(?:lights?|bulbs?)
    )
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_WARMTH = re.compile(
    r"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?
    (?:
        (?:set|change)\s+(?:the\s+)?(?:lights?|bulbs?)?\s*
            (?:warmth|color\s*temp(?:erature)?|colour\s*temp(?:erature)?|cct)
            \s+(?:to\s+)?(?P<n>\d{1,3})\s*(?:percent|%)? |
        (?:set|change)\s+(?:the\s+)?(?:lights?|bulbs?)\s+to\s+
            (?P<k>\d{4})\s*(?:k|kelvin)?
    )
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_COLOR = re.compile(
    rf"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?
    (?:
        (?:set|make|turn|change)\s+(?:the\s+)?(?:lights?|strip|leds?|bulbs?)
            \s+(?:to\s+|)?(?P<c1>{_COLOR_NAMES}) |
        (?:lights?|strip|bulbs?)\s+(?:to\s+)?(?P<c2>{_COLOR_NAMES})
    )
    (?:\s+(?:at\s+)?(?P<b1>\d{{1,3}})\s*(?:percent|%)?)?
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_BRIGHT_DELTA = re.compile(
    r"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?
    (?:
        turn\s+(?:the\s+)?lights?\s+(?P<dir1>up|down|brighter|dimmer) |
        turn\s+(?P<dir2>up|down)\s+(?:the\s+)?lights? |
        (?:make\s+(?:the\s+)?)?lights?\s+(?P<dir3>brighter|dimmer|up|down) |
        (?P<dir4>brighten|dim)\s+(?:the\s+)?lights?
    )
    (?:\s+(?:by\s+)?(?P<n>\d{1,3})\s*(?:percent|%)?)?
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_BRIGHT = re.compile(
    r"""
    ^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?
    (?:
        (?:set\s+)?(?:the\s+)?(?:lights?|strip|brightness)
            \s+(?:to\s+)?(?P<n>\d{1,3})\s*(?:percent|%)? |
        dim\s+(?:the\s+)?(?:lights?|strip) |
        brighten\s+(?:the\s+)?(?:lights?|strip)
    )
    \s*[.?!]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _run(
    action: str,
    color: str | None = None,
    brightness: int | None = None,
    **kwargs,
):
    return lambda: lights_client.apply(
        action=action, color=color, brightness=brightness, **kwargs
    )


class LightsCommand(Command):
    name = "lights"

    def match(self, text: str) -> CommandResult | None:
        raw = (text or "").strip()
        if not raw:
            return None
        if _OFF.match(raw):
            return CommandResult(label=self.name, run=_run("off"))
        if _ON.match(raw):
            return CommandResult(label=self.name, run=_run("on"))
        if _STATUS.match(raw):
            return CommandResult(label=self.name, run=_run("status"))
        if _RAINBOW.match(raw):
            return CommandResult(label=self.name, run=_run("rainbow"))
        if _WARMER.match(raw):
            return CommandResult(
                label=self.name, run=_run("color", warmth_delta=-25)
            )
        if _COOLER.match(raw):
            return CommandResult(
                label=self.name, run=_run("color", warmth_delta=25)
            )
        m = _WARMTH.match(raw)
        if m:
            k = m.group("k")
            if k:
                kelvin = max(2000, min(7000, int(k)))
                return CommandResult(
                    label=self.name, run=_run("color", kelvin=kelvin)
                )
            n = max(0, min(100, int(m.group("n"))))
            return CommandResult(
                label=self.name, run=_run("color", warmth=n)
            )
        m = _COLOR.match(raw)
        if m:
            color = (m.group("c1") or m.group("c2") or "").lower()
            bri = m.group("b1")
            brightness = int(bri) if bri else None
            if brightness is not None:
                brightness = max(1, min(100, brightness))
            return CommandResult(
                label=self.name, run=_run("color", color, brightness)
            )
        m = _BRIGHT_DELTA.match(raw)
        if m:
            direction = (
                m.group("dir1") or m.group("dir2") or m.group("dir3") or m.group("dir4") or ""
            ).lower()
            step = m.group("n")
            delta = int(step) if step else 20
            if direction in {"down", "dimmer", "dim"}:
                delta = -delta
            return CommandResult(
                label=self.name, run=_run("color", brightness_delta=delta)
            )
        m = _BRIGHT.match(raw)
        if m:
            lower = raw.lower()
            if "dim" in lower:
                return CommandResult(label=self.name, run=_run("color", None, 20))
            if "brighten" in lower:
                return CommandResult(label=self.name, run=_run("color", None, 100))
            n = max(1, min(100, int(m.group("n"))))
            return CommandResult(label=self.name, run=_run("color", None, n))
        return None
