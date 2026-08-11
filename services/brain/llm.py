"""LLM backends for Jarvis brain. Yields streamed text chunks.

Providers:
  groq   — GroqCloud OpenAI-compatible API (default: openai/gpt-oss-20b)
  ollama — local Ollama /api/chat
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator

import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OLLAMA_URL = "http://localhost:11434/api/chat"


def provider_name() -> str:
    return os.environ.get("JARVIS_LLM_PROVIDER", "groq").strip().lower()


def model_name() -> str:
    p = provider_name()
    if p == "groq":
        return os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
    if p == "ollama":
        return os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
    return "?"


def stream_chat(messages: list[dict]) -> Iterator[str]:
    p = provider_name()
    if p == "groq":
        yield from _stream_groq(messages)
    elif p == "ollama":
        yield from _stream_ollama(messages)
    else:
        raise ValueError(
            f"Unknown JARVIS_LLM_PROVIDER={p!r} (expected 'groq' or 'ollama')"
        )


def _stream_groq(messages: list[dict]) -> Iterator[str]:
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Copy .env.example → .env and add your key."
        )

    model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
    url = os.environ.get("GROQ_BASE_URL", GROQ_URL)
    # Voice: keep replies short; low reasoning avoids long silent "think" time.
    max_tokens = int(os.environ.get("JARVIS_MAX_TOKENS", "50"))
    effort = os.environ.get("GROQ_REASONING_EFFORT", "low")

    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": float(os.environ.get("JARVIS_TEMPERATURE", "0.7")),
        "top_p": float(os.environ.get("JARVIS_TOP_P", "0.9")),
        "max_completion_tokens": max_tokens,
        "reasoning_effort": effort,
        "include_reasoning": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    with requests.post(
        url, json=payload, headers=headers, stream=True, timeout=60
    ) as response:
        response.raise_for_status()
        # requests defaults HTTP bodies to ISO-8859-1; Groq is UTF-8.
        # Without this, curly quotes/em-dashes become "â\x80\x99" and TTS
        # literally says "circumflex".
        response.encoding = "utf-8"
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith(":"):  # SSE comment / keepalive
                continue
            if not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            piece = delta.get("content") or ""
            if piece:
                yield piece


def _stream_ollama(messages: list[dict]) -> Iterator[str]:
    url = os.environ.get("OLLAMA_URL", OLLAMA_URL)
    model = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
    max_tokens = int(os.environ.get("JARVIS_MAX_TOKENS", "60"))

    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {
            "temperature": float(os.environ.get("JARVIS_TEMPERATURE", "0.7")),
            "top_p": float(os.environ.get("JARVIS_TOP_P", "0.9")),
            "num_predict": max_tokens,
        },
    }

    with requests.post(url, json=payload, stream=True, timeout=120) as response:
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            data = json.loads(line)
            piece = data.get("message", {}).get("content", "")
            if piece:
                yield piece
            if data.get("done"):
                break
