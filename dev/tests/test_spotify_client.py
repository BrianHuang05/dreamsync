"""Tests for dreamsync.spotify.client — mocked HTTP responses."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from dreamsync.spotify.auth import TokenStore
from dreamsync.spotify.client import SpotifyAPIError, SpotifyAuthError, SpotifyClient
from dreamsync.spotify.models import PlaybackState, QueueSnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token_store(tmp_path):
    """Create a TokenStore with a valid token."""
    path = tmp_path / "token.json"
    store = TokenStore(path=path)
    store.update(
        access_token="valid_token",
        refresh_token="refresh_tok",
        expires_in=3600,
        client_id="test_client",
    )
    return store


def _mock_response(status_code=200, json_data=None, content=b"", headers=None):
    """Build a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = json.dumps(json_data).encode() if json_data else content
    resp.json.return_value = json_data or {}
    resp.text = json.dumps(json_data) if json_data else ""
    resp.headers = headers or {}
    return resp


PLAYBACK_JSON = {
    "is_playing": True,
    "item": {
        "id": "track1",
        "name": "Test Song",
        "artists": [{"name": "Test Artist"}],
        "album": {"name": "Test Album"},
        "duration_ms": 240000,
        "uri": "spotify:track:track1",
    },
    "progress_ms": 60000,
    "device": {"name": "My Speaker"},
    "shuffle_state": False,
    "repeat_state": "off",
}

QUEUE_JSON = {
    "currently_playing": {
        "id": "track1",
        "name": "Current",
        "artists": [{"name": "Artist A"}],
        "album": {"name": "Album A"},
        "duration_ms": 200000,
        "uri": "spotify:track:track1",
    },
    "queue": [
        {
            "id": "track2",
            "name": "Next",
            "artists": [{"name": "Artist B"}],
            "album": {"name": "Album B"},
            "duration_ms": 180000,
            "uri": "spotify:track:track2",
        },
    ],
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetPlaybackState:
    @patch("dreamsync.spotify.client.refresh_if_needed", return_value=True)
    def test_get_playback_state_playing(self, _mock_refresh, tmp_path):
        """200 response with playing track → returns PlaybackState."""
        store = _make_token_store(tmp_path)
        client = SpotifyClient(store)
        with patch.object(client._http, "request", return_value=_mock_response(200, PLAYBACK_JSON)):
            state = client.get_playback_state()
        assert isinstance(state, PlaybackState)
        assert state.is_playing is True
        assert state.track.name == "Test Song"
        assert state.track.artist == "Test Artist"
        assert state.progress_ms == 60000
        assert state.device_name == "My Speaker"

    @patch("dreamsync.spotify.client.refresh_if_needed", return_value=True)
    def test_get_playback_state_nothing_playing(self, _mock_refresh, tmp_path):
        """204 response → returns None."""
        store = _make_token_store(tmp_path)
        client = SpotifyClient(store)
        with patch.object(client._http, "request", return_value=_mock_response(204)):
            state = client.get_playback_state()
        assert state is None

    @patch("dreamsync.spotify.client.refresh_if_needed", return_value=True)
    def test_get_playback_state_token_refresh(self, mock_refresh, tmp_path):
        """refresh_if_needed is called before each request."""
        store = _make_token_store(tmp_path)
        client = SpotifyClient(store)
        with patch.object(client._http, "request", return_value=_mock_response(200, PLAYBACK_JSON)):
            client.get_playback_state()
        mock_refresh.assert_called()


class TestGetQueue:
    @patch("dreamsync.spotify.client.refresh_if_needed", return_value=True)
    def test_get_queue_returns_snapshot(self, _mock_refresh, tmp_path):
        """200 with queue data → returns QueueSnapshot with correct tracks."""
        store = _make_token_store(tmp_path)
        client = SpotifyClient(store)
        with patch.object(client._http, "request", return_value=_mock_response(200, QUEUE_JSON)):
            q = client.get_queue()
        assert isinstance(q, QueueSnapshot)
        assert q.currently_playing.name == "Current"
        assert len(q.queue) == 1
        assert q.queue[0].name == "Next"


class TestPlaybackControlEndpoints:
    @patch("dreamsync.spotify.client.refresh_if_needed", return_value=True)
    def test_add_to_queue_calls_expected_endpoint(self, _mock_refresh, tmp_path):
        store = _make_token_store(tmp_path)
        client = SpotifyClient(store)
        with patch.object(client._http, "request", return_value=_mock_response(204)) as mock_request:
            client.add_to_queue("spotify:track:track2", device_id="device123")

        mock_request.assert_called_once()
        _, url = mock_request.call_args.args[:2]
        assert url.endswith("/me/player/queue")
        assert mock_request.call_args.kwargs["params"] == {
            "uri": "spotify:track:track2",
            "device_id": "device123",
        }

    @patch("dreamsync.spotify.client.refresh_if_needed", return_value=True)
    def test_skip_to_next_calls_expected_endpoint(self, _mock_refresh, tmp_path):
        store = _make_token_store(tmp_path)
        client = SpotifyClient(store)
        with patch.object(client._http, "request", return_value=_mock_response(204)) as mock_request:
            client.skip_to_next()

        mock_request.assert_called_once()
        _, url = mock_request.call_args.args[:2]
        assert url.endswith("/me/player/next")

    @patch("dreamsync.spotify.client.refresh_if_needed", return_value=True)
    def test_set_shuffle_calls_expected_endpoint(self, _mock_refresh, tmp_path):
        store = _make_token_store(tmp_path)
        client = SpotifyClient(store)
        with patch.object(client._http, "request", return_value=_mock_response(204)) as mock_request:
            client.set_shuffle(True, device_id="speaker1")

        mock_request.assert_called_once()
        _, url = mock_request.call_args.args[:2]
        assert url.endswith("/me/player/shuffle")
        assert mock_request.call_args.kwargs["params"] == {
            "state": "true",
            "device_id": "speaker1",
        }


class TestErrorHandling:
    @patch("dreamsync.spotify.client.refresh_if_needed", return_value=True)
    def test_rate_limit_retry(self, _mock_refresh, tmp_path):
        """429 with Retry-After → client waits and retries."""
        store = _make_token_store(tmp_path)
        client = SpotifyClient(store)
        rate_resp = _mock_response(429, headers={"Retry-After": "0"})
        ok_resp = _mock_response(200, PLAYBACK_JSON)
        with patch.object(client._http, "request", side_effect=[rate_resp, ok_resp]):
            state = client.get_playback_state()
        assert state is not None
        assert state.track.name == "Test Song"

    @patch("dreamsync.spotify.client.refresh_if_needed", return_value=False)
    def test_auth_error_raises(self, _mock_refresh, tmp_path):
        """Token refresh failure → raises SpotifyAuthError."""
        store = _make_token_store(tmp_path)
        client = SpotifyClient(store)
        with pytest.raises(SpotifyAuthError):
            client.get_playback_state()

    @patch("dreamsync.spotify.client.refresh_if_needed", return_value=True)
    def test_server_error_raises(self, _mock_refresh, tmp_path):
        """500 response → raises SpotifyAPIError."""
        store = _make_token_store(tmp_path)
        client = SpotifyClient(store)
        with patch.object(client._http, "request", return_value=_mock_response(500)):
            with pytest.raises(SpotifyAPIError) as exc_info:
                client.get_playback_state()
            assert exc_info.value.status_code == 500
