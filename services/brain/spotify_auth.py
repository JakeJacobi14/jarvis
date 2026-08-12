#!/usr/bin/env python3
"""One-time Spotify OAuth — prints a refresh token for .env.

Usage (from services/brain, with venv active):
  python spotify_auth.py

Prereqs:
  1. Create an app at https://developer.spotify.com/dashboard
  2. Add Redirect URI: http://127.0.0.1:8732/callback
  3. Put SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in repo-root .env
  4. Spotify Premium on the account you authorize
"""
from __future__ import annotations

import http.server
import os
import secrets
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
REDIRECT_URI = os.environ.get(
    "SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8732/callback"
).strip()
SCOPES = " ".join(
    [
        "user-modify-playback-state",
        "user-read-playback-state",
        "user-read-currently-playing",
    ]
)


def _load_dotenv():
    path = ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


def _upsert_env(key: str, value: str):
    path = ROOT / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    out, seen = [], False
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            seen = True
        else:
            out.append(line)
    if not seen:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={value}")
    text = "\n".join(out)
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


def main():
    _load_dotenv()
    cid = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        raise SystemExit(
            "Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env first."
        )

    state = secrets.token_urlsafe(16)
    result: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != urllib.parse.urlparse(REDIRECT_URI).path:
                self.send_response(404)
                self.end_headers()
                return
            qs = urllib.parse.parse_qs(parsed.query)
            if qs.get("state", [None])[0] != state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"State mismatch")
                return
            if "error" in qs:
                result["error"] = qs["error"][0]
                body = b"Authorization denied. You can close this tab."
            else:
                result["code"] = qs.get("code", [""])[0]
                body = b"Jarvis authorized. You can close this tab."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):  # silence
            return

    host = urllib.parse.urlparse(REDIRECT_URI).hostname or "127.0.0.1"
    port = urllib.parse.urlparse(REDIRECT_URI).port or 8732
    server = http.server.HTTPServer((host, port), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    params = {
        "client_id": cid,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "show_dialog": "true",
    }
    url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(params)
    print("Opening Spotify authorize page…")
    print(url)
    webbrowser.open(url)
    thread.join(timeout=180)
    server.server_close()

    if result.get("error"):
        raise SystemExit(f"Authorization failed: {result['error']}")
    code = result.get("code")
    if not code:
        raise SystemExit("No auth code received (timed out?). Try again.")

    resp = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        auth=(cid, secret),
        timeout=20,
    )
    if resp.status_code >= 400:
        raise SystemExit(f"Token exchange failed: {resp.status_code} {resp.text}")
    data = resp.json()
    refresh = data.get("refresh_token")
    if not refresh:
        raise SystemExit(
            "No refresh_token returned. Revoke app access and re-run with show_dialog."
        )

    _upsert_env("SPOTIFY_REFRESH_TOKEN", refresh)
    _upsert_env("SPOTIFY_REDIRECT_URI", REDIRECT_URI)
    print("\nSuccess. Wrote SPOTIFY_REFRESH_TOKEN to .env")
    print("Restart the brain service, open Spotify on a device, then say: play …")


if __name__ == "__main__":
    main()
