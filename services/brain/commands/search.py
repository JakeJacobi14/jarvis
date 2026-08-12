"""Web search via Tavily.

Triggers:
  Explicit — "google …", "search …", "look up …"
  Auto     — live facts + encyclopedic lookups the model should not invent
"""
from __future__ import annotations

import re

from commands.base import Command, CommandResult

# "google X" / "search for X" / "look up X" / "look this up: X"
_EXPLICIT = re.compile(
    r"""^\s*
        (?:hey\s+jarvis[,.]?\s+)?
        (?:please\s+)?
        (?:
            google |
            search\s+(?:for\s+|the\s+web\s+for\s+|online\s+for\s+)? |
            look(?:\s+|-)up |
            look\s+(?:this|that|it)\s+up(?:\s+for\s+me)?(?:\s*[:\-]|,\s*)?
        )
        \s*(?P<q>.+?)
        \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Questions that need live / precise web data (not training-cutoff guesses).
_AUTO_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        # live / current
        r"\b(latest|breaking|today'?s?|tonight'?s?|this\s+week'?s?)\b.+\b(news|headline|score|scores|results?|price|prices|weather)\b",
        r"\b(what|how)\b.+\b(weather|forecast)\b",
        r"\b(who\s+won|who'?s\s+winning|final\s+score|game\s+score)\b",
        r"\b(stock\s+price|share\s+price|crypto|bitcoin|ethereum)\b",
        r"\b(current|live|right\s+now)\b.+\b(price|score|news|standings|rate|temperature)\b",
        r"\bwhat\s+(?:is|are)\s+the\s+(?:latest|current|today'?s)\b",
        # rankings / obscure facts (e.g. "100th tallest mountain")
        r"\b\d+(?:st|nd|rd|th)\s+(?:tallest|longest|largest|biggest|highest|smallest|shortest|fastest|oldest|youngest|richest|poorest)\b",
        r"\b(?:tallest|longest|largest|biggest|highest|smallest|shortest)\s+(?:\w+\s+){0,4}(?:in\s+the\s+world|on\s+earth|ever)\b",
        r"\bwhat\s+is\s+the\s+\d+(?:st|nd|rd|th)?\b",
        r"\bwho\s+is\s+the\s+(?:tallest|oldest|richest|youngest|fastest)\b",
        r"\b(?:population|capital|president|prime\s+minister|ceo|founder)\s+of\b",
        r"\bhow\s+(?:tall|high|long|far|old|heavy|big)\s+is\b",
        r"\bhow\s+many\s+(?:people|inhabitants|miles|kilometers|metres|meters)\b",
        r"\bwhen\s+(?:was|did|is|are|will)\b.+\b(?:born|founded|released|invented|discovered|die|died|happen|happen(?:ed)?)\b",
        # mid-sentence: "can you google …" / "please search for …"
        r"\b(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?(?:google|search\s+for|look\s+up)\b",
    )
]

# Soft strip of polite filler so the search query stays clean.
_FILLER = re.compile(
    r"^\s*(?:hey\s+jarvis[,.]?\s+)?(?:please\s+)?(?:can\s+you\s+|could\s+you\s+|would\s+you\s+)?"
    r"(?:tell\s+me\s+|find\s+(?:out\s+)?|check\s+)?",
    re.IGNORECASE,
)

# Used by brain when the LLM returns silence — looks like a fact question.
_FACTISH = re.compile(
    r"""^\s*
        (?:hey\s+jarvis[,.]?\s+)?
        (?:please\s+)?
        (?:
            what(?:'?s|\s+is|\s+are|\s+was|\s+were)\b |
            who(?:'?s|\s+is|\s+are|\s+was|\s+were)\b |
            when(?:'?s|\s+is|\s+was|\s+did|\s+will)\b |
            where(?:'?s|\s+is|\s+are|\s+was)\b |
            which\b |
            how\s+(?:many|much|tall|high|long|far|old|heavy|big|fast)\b |
            name\s+the\b
        )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def looks_like_fact_question(text: str) -> bool:
    """True for fact-seeking questions worth a web fallback if the LLM blanks."""
    raw = (text or "").strip()
    return bool(raw and _FACTISH.match(raw))


class SearchCommand(Command):
    name = "search"

    def match(self, text: str) -> CommandResult | None:
        raw = (text or "").strip()
        if not raw:
            return None

        m = _EXPLICIT.match(raw)
        if m:
            q = m.group("q").strip(" .?!,")
            if q:
                return CommandResult(label=self.name, search_query=q)

        if any(p.search(raw) for p in _AUTO_PATTERNS):
            q = _FILLER.sub("", raw).strip(" .?!,") or raw
            mid = re.search(
                r"\b(?:google|search(?:\s+for)?|look\s+up)\s+(?P<q>.+)$",
                q,
                re.IGNORECASE,
            )
            if mid:
                q = mid.group("q").strip(" .?!,")
            return CommandResult(label=self.name, search_query=q)

        return None
