"""Tavily Search API client (REST). Uses requests — already a brain dep."""
from __future__ import annotations

import os
from typing import Any

import requests

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class TavilyError(RuntimeError):
    pass


def api_key() -> str:
    return os.environ.get("TAVILY_API_KEY", "").strip()


def search(
    query: str,
    *,
    max_results: int | None = None,
    include_answer: bool = True,
    search_depth: str | None = None,
) -> dict[str, Any]:
    """Run a Tavily search. Raises TavilyError on missing key / HTTP failure."""
    key = api_key()
    if not key:
        raise TavilyError(
            "TAVILY_API_KEY is not set. Add it to .env (see .env.example)."
        )

    max_results = max_results or int(os.environ.get("TAVILY_MAX_RESULTS", "5"))
    depth = search_depth or os.environ.get("TAVILY_SEARCH_DEPTH", "basic")

    payload = {
        "query": query,
        "max_results": max_results,
        "include_answer": include_answer,
        "search_depth": depth,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(
            TAVILY_SEARCH_URL, json=payload, headers=headers, timeout=20
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise TavilyError(f"Tavily request failed: {e}") from e

    data = resp.json()
    if not isinstance(data, dict):
        raise TavilyError("Unexpected Tavily response")
    return data


def format_for_llm(data: dict[str, Any], *, max_chars: int = 1800) -> str:
    """Compact search payload for a spoken LLM summary."""
    parts: list[str] = []
    answer = (data.get("answer") or "").strip()
    if answer:
        parts.append(f"Answer: {answer}")

    for i, hit in enumerate(data.get("results") or [], start=1):
        title = (hit.get("title") or "").strip()
        content = (hit.get("content") or "").strip()
        if not title and not content:
            continue
        snippet = content[:280].replace("\n", " ")
        parts.append(f"{i}. {title}: {snippet}")

    text = "\n".join(parts).strip() or "(no results)"
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."
    return text
