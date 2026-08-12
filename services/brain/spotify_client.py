"""Spotify Web API client — search + playback control.

Requires Spotify Premium and a one-time OAuth (see spotify_auth.py):
  SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN
"""
from __future__ import annotations

import os
import re
import time
from typing import Any

import requests

TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"

_access_token: str | None = None
_access_expires_at: float = 0.0


class SpotifyError(RuntimeError):
    pass


def _creds() -> tuple[str, str, str]:
    cid = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    refresh = os.environ.get("SPOTIFY_REFRESH_TOKEN", "").strip()
    if not cid or not secret or not refresh:
        raise SpotifyError(
            "Spotify is not configured. Set SPOTIFY_CLIENT_ID, "
            "SPOTIFY_CLIENT_SECRET, and SPOTIFY_REFRESH_TOKEN in .env "
            "(run: python spotify_auth.py)."
        )
    return cid, secret, refresh


def _get_access_token() -> str:
    global _access_token, _access_expires_at
    if _access_token and time.time() < _access_expires_at - 30:
        return _access_token

    cid, secret, refresh = _creds()
    try:
        resp = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
            },
            auth=(cid, secret),
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise SpotifyError(f"Spotify auth failed: {e}") from e

    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise SpotifyError("Spotify auth returned no access token")
    _access_token = token
    _access_expires_at = time.time() + float(data.get("expires_in", 3600))
    # Spotify may rotate refresh tokens
    new_refresh = data.get("refresh_token")
    if new_refresh:
        os.environ["SPOTIFY_REFRESH_TOKEN"] = new_refresh
    return token


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_get_access_token()}"}


def _request(method: str, path: str, **kwargs) -> requests.Response:
    url = path if path.startswith("http") else f"{API}{path}"
    try:
        resp = requests.request(method, url, headers=_headers(), timeout=15, **kwargs)
    except requests.RequestException as e:
        raise SpotifyError(f"Spotify request failed: {e}") from e
    return resp


def list_devices() -> list[dict[str, Any]]:
    resp = _request("GET", "/me/player/devices")
    if resp.status_code == 401:
        raise SpotifyError("Spotify auth expired — re-run spotify_auth.py")
    if resp.status_code >= 400:
        raise SpotifyError(f"Could not list devices ({resp.status_code})")
    return list((resp.json() or {}).get("devices") or [])


def _ensure_device() -> str | None:
    """Return an active device id, transferring to one if needed."""
    devices = list_devices()
    if not devices:
        raise SpotifyError(
            "No Spotify devices found. Open Spotify on your phone or computer first."
        )
    active = next((d for d in devices if d.get("is_active")), None)
    if active:
        return active.get("id")
    # Wake the first available device
    target = devices[0]
    did = target.get("id")
    if not did:
        raise SpotifyError("No usable Spotify device")
    resp = _request(
        "PUT",
        "/me/player",
        json={"device_ids": [did], "play": False},
    )
    # 204 success; 404 no device — already handled
    if resp.status_code not in (204, 200, 202):
        # still try playing without transfer
        pass
    return did


_BY_ARTIST = re.compile(
    r"^(?P<track>.+?)\s+by\s+(?P<artist>.+)$",
    re.IGNORECASE,
)
_ARTIST_ONLY_TRACK = {
    "something",
    "anything",
    "music",
    "songs",
    "a song",
    "some music",
    "some songs",
    "tracks",
}
# Song titles like "Stand by Me" — not "track by artist".
_NOT_ARTIST = {
    "me",
    "you",
    "my",
    "your",
    "him",
    "her",
    "us",
    "them",
    "myself",
    "yourself",
    "the",
    "a",
    "an",
    "way",
    "side",
    "night",
}


def _search_tracks(q: str, *, limit: int = 5) -> list[dict[str, Any]]:
    resp = _request(
        "GET",
        "/search",
        params={"q": q, "type": "track", "limit": limit},
    )
    if resp.status_code >= 400:
        raise SpotifyError(f"Spotify search failed ({resp.status_code})")
    return list(((resp.json() or {}).get("tracks") or {}).get("items") or [])


def _artist_matches(track: dict[str, Any], wanted: str) -> bool:
    wanted_l = wanted.lower().strip()
    if not wanted_l:
        return False
    for a in track.get("artists") or []:
        name = (a.get("name") or "").lower().strip()
        if not name:
            continue
        if wanted_l == name:
            return True
        if name.startswith("the ") and wanted_l == name[4:]:
            return True
        if wanted_l.startswith("the ") and name == wanted_l[4:]:
            return True
        # Whole-word containment (avoids "me" matching "Meg")
        if re.search(rf"\b{re.escape(wanted_l)}\b", name):
            return True
        if len(wanted_l) >= 4 and wanted_l in name:
            return True
    return False


def search_track(query: str) -> dict[str, Any] | None:
    """Find a track. Parses 'Song by Artist' into Spotify field filters."""
    raw = (query or "").strip()
    if not raw:
        return None

    m = _BY_ARTIST.match(raw)
    if m:
        track_q = m.group("track").strip(" \"'")
        artist_q = m.group("artist").strip(" \"'")
        looks_like_artist = (
            bool(track_q and artist_q)
            and artist_q.lower() not in _NOT_ARTIST
            and len(artist_q) >= 2
        )
        if looks_like_artist:
            # "play something by Queen" → top track for that artist
            if track_q.lower() in _ARTIST_ONLY_TRACK:
                items = _search_tracks(f'artist:"{artist_q}"', limit=1)
                return items[0] if items else None

            # Prefer structured filters so we get the right artist.
            for candidate_q in (
                f'track:"{track_q}" artist:"{artist_q}"',
                f"{track_q} artist:{artist_q}",
            ):
                for item in _search_tracks(candidate_q, limit=5):
                    if _artist_matches(item, artist_q):
                        return item

            # Structured miss → raw fallback below (also covers odd STT).

    items = _search_tracks(raw, limit=1)
    return items[0] if items else None


def play_query(query: str) -> str:
    """Search for a track and start playback. Returns a spoken confirmation."""
    track = search_track(query)
    if not track:
        return f"I couldn't find anything for {query}."

    uri = track.get("uri")
    name = track.get("name") or query
    artists = ", ".join(
        a.get("name", "") for a in (track.get("artists") or []) if a.get("name")
    )
    label = f"{name} by {artists}" if artists else name

    device_id = _ensure_device()
    params = {"device_id": device_id} if device_id else None
    resp = _request(
        "PUT",
        "/me/player/play",
        params=params,
        json={"uris": [uri]},
    )
    if resp.status_code == 404:
        return "Open Spotify on a device first, then ask me again."
    if resp.status_code == 403:
        return "Spotify Premium is required to play music."
    if resp.status_code not in (204, 200, 202):
        raise SpotifyError(f"Play failed ({resp.status_code}): {resp.text[:160]}")
    return f"Playing {label}."


def control(action: str) -> str:
    """pause | resume | next | previous → spoken confirmation."""
    device_id = None
    try:
        device_id = _ensure_device()
    except SpotifyError:
        # pause/next still may work if something is active
        pass
    params = {"device_id": device_id} if device_id else None

    if action == "pause":
        resp = _request("PUT", "/me/player/pause", params=params)
        if resp.status_code in (204, 200, 202):
            return "Paused."
        if resp.status_code == 404:
            return "Nothing is playing."
        raise SpotifyError(f"Pause failed ({resp.status_code})")

    if action == "resume":
        resp = _request("PUT", "/me/player/play", params=params)
        if resp.status_code in (204, 200, 202):
            return "Resuming."
        if resp.status_code == 404:
            return "Open Spotify on a device first."
        raise SpotifyError(f"Resume failed ({resp.status_code})")

    if action == "next":
        resp = _request("POST", "/me/player/next", params=params)
        if resp.status_code in (204, 200, 202):
            return "Skipping."
        raise SpotifyError(f"Skip failed ({resp.status_code})")

    if action == "previous":
        resp = _request("POST", "/me/player/previous", params=params)
        if resp.status_code in (204, 200, 202):
            return "Going back."
        raise SpotifyError(f"Previous failed ({resp.status_code})")

    raise SpotifyError(f"Unknown Spotify action: {action}")
