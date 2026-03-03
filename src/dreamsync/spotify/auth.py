"""OAuth 2.0 Authorization Code (PKCE) flow for Spotify.

Handles the full token lifecycle: initial browser-based auth, file-based
persistence, and transparent refresh before expiry.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

_logger = logging.getLogger(__name__)

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPES = "user-read-playback-state user-read-currently-playing"

TOKEN_DIR = Path.home() / ".dreamsync"
TOKEN_PATH = TOKEN_DIR / "spotify_token.json"

# Refresh tokens when within this many seconds of expiry
REFRESH_BUFFER_SECONDS = 300  # 5 minutes


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code verifier and its SHA-256 challenge.

    Returns ``(verifier, challenge)`` where *verifier* is 128 chars of
    URL-safe base64 and *challenge* is ``base64url(sha256(verifier))``.
    """
    verifier = secrets.token_urlsafe(96)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ---------------------------------------------------------------------------
# Token store
# ---------------------------------------------------------------------------


class TokenStore:
    """Read/write Spotify tokens to ``~/.dreamsync/spotify_token.json``."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or TOKEN_PATH
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text("utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                _logger.warning("Could not load token file: %s", exc)
                self._data = {}

    def save(self) -> None:
        """Persist the current token data to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2), "utf-8")
        # Best-effort restrictive permissions on non-Windows
        if sys.platform != "win32":
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    # -- Properties ----------------------------------------------------------

    @property
    def access_token(self) -> str:
        return self._data.get("access_token", "")

    @property
    def refresh_token(self) -> str:
        return self._data.get("refresh_token", "")

    @property
    def expires_at(self) -> float:
        return float(self._data.get("expires_at", 0))

    @property
    def client_id(self) -> str:
        return self._data.get("client_id", "")

    # -- Helpers -------------------------------------------------------------

    def has_valid_token(self) -> bool:
        """Return True if we have an access token that isn't expired."""
        return bool(self.access_token) and time.time() < self.expires_at

    def needs_refresh(self) -> bool:
        """Return True if the token expires within the refresh buffer."""
        if not self.access_token:
            return False
        return time.time() >= (self.expires_at - REFRESH_BUFFER_SECONDS)

    def update(
        self,
        access_token: str,
        refresh_token: str,
        expires_in: int,
        client_id: str = "",
    ) -> None:
        """Store new token data and persist to disk."""
        self._data["access_token"] = access_token
        if refresh_token:
            self._data["refresh_token"] = refresh_token
        self._data["expires_at"] = time.time() + expires_in
        if client_id:
            self._data["client_id"] = client_id
        self.save()


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------


def refresh_if_needed(token_store: TokenStore) -> bool:
    """Refresh the Spotify token if near expiry.

    Returns True if the token is now valid, False if refresh failed.
    """
    if not token_store.needs_refresh():
        return True

    if not token_store.refresh_token:
        _logger.warning("Token needs refresh but no refresh_token available")
        return False

    _logger.info("Refreshing Spotify token...")
    try:
        resp = httpx.post(
            SPOTIFY_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": token_store.refresh_token,
                "client_id": token_store.client_id,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        token_store.update(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", token_store.refresh_token),
            expires_in=data.get("expires_in", 3600),
        )
        _logger.info("Spotify token refreshed successfully")
        return True
    except Exception as exc:
        _logger.warning("Spotify token refresh failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Interactive OAuth PKCE flow
# ---------------------------------------------------------------------------


def start_auth_flow(client_id: str, redirect_port: int = 8888) -> TokenStore:
    """Run the interactive OAuth PKCE flow.

    Opens the user's browser to Spotify's auth page, captures the callback
    on a local HTTP server, exchanges the code for tokens, and persists them.
    """
    redirect_uri = f"http://127.0.0.1:{redirect_port}/callback"
    verifier, challenge = generate_pkce_pair()

    # Build the authorization URL
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    auth_url = f"{SPOTIFY_AUTH_URL}?{urlencode(params)}"

    # State for the callback handler
    auth_code: list[str] = []
    auth_error: list[str] = []
    received = Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            qs = parse_qs(urlparse(self.path).query)
            if "code" in qs:
                auth_code.append(qs["code"][0])
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h2>Authorization successful!</h2>"
                    b"<p>You can close this tab.</p></body></html>"
                )
            else:
                error = qs.get("error", ["unknown"])[0]
                auth_error.append(error)
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    f"<html><body><h2>Authorization failed: {error}</h2></body></html>".encode()
                )
            received.set()

        def log_message(self, format: str, *args: Any) -> None:
            pass  # Suppress HTTP server logs

    # Start local server
    server = HTTPServer(("127.0.0.1", redirect_port), CallbackHandler)
    server.timeout = 120  # 2 minute timeout

    print(f"Opening browser for Spotify authorization...")
    print(f"If the browser doesn't open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    # Wait for callback
    while not received.is_set():
        server.handle_request()

    server.server_close()

    if auth_error:
        raise RuntimeError(f"Spotify authorization failed: {auth_error[0]}")

    if not auth_code:
        raise RuntimeError("No authorization code received")

    # Exchange code for tokens
    print("Exchanging authorization code for tokens...")
    resp = httpx.post(
        SPOTIFY_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": auth_code[0],
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()

    token_store = TokenStore()
    token_store.update(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", ""),
        expires_in=data.get("expires_in", 3600),
        client_id=client_id,
    )
    print(f"Spotify token saved to {token_store.path}")
    return token_store


def load_or_prompt(client_id: str) -> TokenStore | None:
    """Load stored token or run interactive auth.

    Returns a TokenStore with a valid token, or None if auth fails/is cancelled.
    """
    store = TokenStore()

    # If we have a valid (or refreshable) token, use it
    if store.has_valid_token():
        return store

    if store.refresh_token and store.client_id:
        if refresh_if_needed(store):
            return store

    # Need fresh auth
    try:
        return start_auth_flow(client_id)
    except Exception as exc:
        _logger.warning("Spotify auth flow failed: %s", exc)
        return None
