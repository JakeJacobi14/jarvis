"""Decide whether a follow-up utterance was meant for Jarvis.

Wake-word / PTT turns always count as addressed. Follow-up window speech
is filtered so side conversation with friends doesn't start a reply loop.
"""
from __future__ import annotations

import re

_JARVIS_ALIASES = {"jarvis", "jarv", "jervis", "jarves"}

# Words that can follow hey/hi/so without being a person's name.
_NOT_NAMES = {
    "there",
    "guys",
    "everyone",
    "everybody",
    "man",
    "dude",
    "bro",
    "buddy",
    "all",
    "basically",
    "then",
    "anyway",
    "like",
    "um",
    "uh",
    "yeah",
    "yep",
    "yes",
    "what",
    "how",
    "when",
    "where",
    "why",
    "who",
    "if",
    "maybe",
    "just",
    "now",
    "again",
    "please",
    "sir",
    "mate",
    "folks",
    "team",
    "boss",
    "you",
    "but",
    "and",
    "or",
    "the",
    "a",
    "an",
    "my",
    "your",
    "its",
    "it's",
}

# "hey Nate" / "so Mike" / "hi Sarah" — directed at someone else.
_OTHER_PERSON = re.compile(
    r"\b(?:hey|hi|hello|yo|so)\s+([a-zA-Z']+)\b",
    re.IGNORECASE,
)

# "Nate, how's it going" vocative
_VOCATIVE = re.compile(
    r"\b([A-Za-z']{2,})\s*,\s*(?:how|what|where's|how's|whats|who)\b",
    re.IGNORECASE,
)

# Clear signals the user is still talking to Jarvis.
_ADDRESSED = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bjarvis\b",
        r"\bthank(?:s| you)\b",
        r"\bappreciate\s+(?:it|that|you)\b",
        r"[?]\s*$",
        r"^\s*(?:hey\s+)?(?:what|what's|whats|who|who's|whos|when|where|why|how|which|whose)\b",
        r"^\s*(?:is|are|was|were|do|does|did|can|could|would|will|should|am)\b",
        r"^\s*(?:tell|show|give|explain)\s+(?:me|us)\b",
        r"^\s*(?:remind|set|turn|open|close|play|pause|stop|skip|resume|search|google|look\s+up|put\s+on|take|snap|dim|brighten|mute|unmute)\b",
        # Continuations must look like a follow-up question, not a story ("And then…")
        r"^\s*(?:and|also)\s+(?:what|how's|hows|how|who|when|where|why|the|that|it|about)\b",
        r"^\s*(?:what about|how about|another|one more)\b",
        r"^\s*(?:yes|yep|yeah|sure|ok|okay|no|nope|go ahead|do it|please do)\b.*\b(?:that|it|please|jarvis)\b",
        r"^\s*(?:yes|yep|sure|ok|okay|please|go ahead|do it)(?:\s+(?:sure|please|thanks))?\s*[.!]?\s*$",
    )
]


def _name_is_other_person(name: str) -> bool:
    n = (name or "").strip("'\"").lower()
    if not n or n in _JARVIS_ALIASES or n in _NOT_NAMES:
        return False
    return len(n) >= 2


def _addressed_to_someone_else(text: str) -> bool:
    """True if the utterance greets / names another person (not Jarvis)."""
    for m in _OTHER_PERSON.finditer(text):
        if _name_is_other_person(m.group(1)):
            return True
    for m in _VOCATIVE.finditer(text):
        if _name_is_other_person(m.group(1)):
            return True
    return False


def is_addressed_to_jarvis(text: str) -> bool:
    """True if follow-up speech looks directed at Jarvis."""
    raw = (text or "").strip()
    if not raw:
        return False

    mentions_jarvis = bool(re.search(r"\b(?:jarvis|jarv)\b", raw, re.IGNORECASE))

    # Talking to Nate/Mike/etc. — not Jarvis (unless Jarvis is also named).
    if _addressed_to_someone_else(raw) and not mentions_jarvis:
        return False

    if mentions_jarvis:
        return True

    return any(p.search(raw) for p in _ADDRESSED)
