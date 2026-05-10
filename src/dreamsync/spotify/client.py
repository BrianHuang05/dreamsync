"""Thin synchronous Spotify Web API client.

Uses ``httpx.Client`` from a background thread — no asyncio needed.
Automatically refreshes tokens and handles rate limits.
"""

from __future__ import annotations

import logging
import time

import httpx

from .auth import TokenStore, refresh_if_needed
from .models import PlaybackState, QueueSnapshot, SpotifyTrack, parse_track

_logger = logging.getLogger(__name__)

SPOTIFY_API_BASE = "https://api.spotify.com/v1"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SpotifyAuthError(Exception):
    """Token refresh failed or 401 after refresh."""


class SpotifyAPIError(Exception):
    """Non-auth API failure (5xx, unexpected status)."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class SpotifyClient:
    """Synchronous Spotify Web API client for playback and queue endpoints."""

    def __init__(self, token_store: TokenStore, *, timeout: float = 10.0) -> None:
        self._token_store = token_store
        self._http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._http.close()

    # -- Public API ----------------------------------------------------------

    def get_playback_state(self) -> PlaybackState | None:
        """GET /v1/me/player — current playback state.

        Returns None if nothing is playing (HTTP 204).
        """
        resp = self._request("GET", "/me/player")
        if resp.status_code == 204 or resp.status_code == 200 and not resp.content:
            return None

        data = resp.json()
        track_data = data.get("item")
        track = parse_track(track_data) if track_data else None
        device = data.get("device") or {}

        return PlaybackState(
            is_playing=data.get("is_playing", False),
            track=track,
            progress_ms=data.get("progress_ms") or 0,
            timestamp=time.monotonic(),
            device_name=device.get("name", ""),
            shuffle=data.get("shuffle_state", False),
            repeat=data.get("repeat_state", "off"),
        )

    def get_queue(self) -> QueueSnapshot:
        """GET /v1/me/player/queue — current play queue."""
        resp = self._request("GET", "/me/player/queue")
        data = resp.json()

        currently_data = data.get("currently_playing")
        currently = parse_track(currently_data) if currently_data else None
        queue_items = tuple(parse_track(t) for t in (data.get("queue") or []))

        return QueueSnapshot(
            currently_playing=currently,
            queue=queue_items,
            fetched_at=time.monotonic(),
        )

    def add_to_queue(self, uri: str, *, device_id: str | None = None) -> None:
        """POST /v1/me/player/queue — add an item to the playback queue."""
        params = {"uri": uri}
        if device_id:
            params["device_id"] = device_id
        self._request("POST", "/me/player/queue", params=params)

    def skip_to_next(self, *, device_id: str | None = None) -> None:
        """POST /v1/me/player/next — skip to the next queued item."""
        params = {"device_id": device_id} if device_id else None
        self._request("POST", "/me/player/next", params=params)

    def set_shuffle(self, enabled: bool, *, device_id: str | None = None) -> None:
        """PUT /v1/me/player/shuffle — toggle Spotify playback shuffle."""
        params: dict[str, str] = {"state": "true" if enabled else "false"}
        if device_id:
            params["device_id"] = device_id
        self._request("PUT", "/me/player/shuffle", params=params)

    # -- Internal ------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict | None = None,
    ) -> httpx.Response:
        """Make an authenticated request with auto-refresh and rate-limit retry."""
        # Refresh token if near expiry
        if not refresh_if_needed(self._token_store):
            raise SpotifyAuthError("Token refresh failed")

        url = f"{SPOTIFY_API_BASE}{path}"
        headers = {"Authorization": f"Bearer {self._token_store.access_token}"}

        resp = self._http.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_body,
        )

        # Handle 401 — try one refresh then retry
        if resp.status_code == 401:
            _logger.info("Got 401, attempting token refresh...")
            if not refresh_if_needed(self._token_store):
                raise SpotifyAuthError("Token refresh failed after 401")
            headers["Authorization"] = f"Bearer {self._token_store.access_token}"
            resp = self._http.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
            )
            if resp.status_code == 401:
                raise SpotifyAuthError("Still 401 after token refresh")

        # Handle 429 — rate limited
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "1"))
            _logger.warning("Rate limited, waiting %ds", retry_after)
            time.sleep(retry_after)
            resp = self._http.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
            )
            if resp.status_code == 429:
                raise SpotifyAPIError("Still rate limited after retry", 429)

        # Handle server errors
        if resp.status_code >= 500:
            raise SpotifyAPIError(
                f"Spotify server error: {resp.status_code}", resp.status_code
            )

        # 200 and 204 are success
        if resp.status_code not in (200, 204):
            raise SpotifyAPIError(
                f"Unexpected status {resp.status_code}: {resp.text[:200]}",
                resp.status_code,
            )

        return resp
