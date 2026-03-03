"""Tests for dreamsync.spotify.auth — PKCE generation and TokenStore."""

import base64
import hashlib
import json
import time

import pytest

from dreamsync.spotify.auth import TokenStore, generate_pkce_pair


# ---------------------------------------------------------------------------
# PKCE tests
# ---------------------------------------------------------------------------


class TestPKCE:
    def test_pkce_verifier_length(self):
        """Generated verifier is 128 characters of URL-safe base64."""
        verifier, _challenge = generate_pkce_pair()
        assert len(verifier) == 128
        # Verify it's URL-safe base64 (only alphanumerics, -, _)
        assert all(c.isalnum() or c in "-_" for c in verifier)

    def test_pkce_challenge_is_sha256(self):
        """Challenge matches base64url(sha256(verifier))."""
        verifier, challenge = generate_pkce_pair()
        expected_digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(expected_digest).rstrip(b"=").decode("ascii")
        assert challenge == expected

    def test_pkce_pair_unique(self):
        """Each call generates a unique verifier."""
        v1, _ = generate_pkce_pair()
        v2, _ = generate_pkce_pair()
        assert v1 != v2


# ---------------------------------------------------------------------------
# TokenStore tests
# ---------------------------------------------------------------------------


class TestTokenStore:
    def test_token_store_save_load(self, tmp_path):
        """Write token to temp file, load it back, roundtrip equality."""
        path = tmp_path / "token.json"
        store = TokenStore(path=path)
        store.update(
            access_token="access_abc",
            refresh_token="refresh_xyz",
            expires_in=3600,
            client_id="my_client",
        )

        # Load in a fresh instance
        store2 = TokenStore(path=path)
        assert store2.access_token == "access_abc"
        assert store2.refresh_token == "refresh_xyz"
        assert store2.client_id == "my_client"
        assert store2.has_valid_token()

    def test_token_store_expired(self, tmp_path):
        """Token with expires_at in the past → has_valid_token() returns False."""
        path = tmp_path / "token.json"
        data = {
            "access_token": "expired_token",
            "refresh_token": "refresh",
            "expires_at": time.time() - 100,
            "client_id": "cid",
        }
        path.write_text(json.dumps(data))
        store = TokenStore(path=path)
        assert not store.has_valid_token()

    def test_token_store_near_expiry(self, tmp_path):
        """Token expiring in 3 minutes → needs_refresh() returns True."""
        path = tmp_path / "token.json"
        data = {
            "access_token": "near_expiry",
            "refresh_token": "refresh",
            "expires_at": time.time() + 180,  # 3 minutes
            "client_id": "cid",
        }
        path.write_text(json.dumps(data))
        store = TokenStore(path=path)
        # Within the 5-minute buffer → needs refresh
        assert store.needs_refresh()

    def test_token_store_no_refresh_when_fresh(self, tmp_path):
        """Token expiring in 30 minutes → needs_refresh() returns False."""
        path = tmp_path / "token.json"
        data = {
            "access_token": "fresh_token",
            "refresh_token": "refresh",
            "expires_at": time.time() + 1800,  # 30 minutes
            "client_id": "cid",
        }
        path.write_text(json.dumps(data))
        store = TokenStore(path=path)
        assert not store.needs_refresh()

    def test_token_store_empty_file(self, tmp_path):
        """Non-existent token file → has_valid_token() is False."""
        path = tmp_path / "nonexistent.json"
        store = TokenStore(path=path)
        assert not store.has_valid_token()
        assert store.access_token == ""

    def test_token_store_preserves_refresh_token_on_update(self, tmp_path):
        """Updating with empty refresh_token keeps the old one."""
        path = tmp_path / "token.json"
        store = TokenStore(path=path)
        store.update(
            access_token="a1",
            refresh_token="original_refresh",
            expires_in=3600,
            client_id="cid",
        )
        store.update(
            access_token="a2",
            refresh_token="",  # empty → should keep original
            expires_in=3600,
        )
        store2 = TokenStore(path=path)
        assert store2.access_token == "a2"
        assert store2.refresh_token == "original_refresh"
