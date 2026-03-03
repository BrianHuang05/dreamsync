# Feature 1 — Spotify Queue Watcher: Implementation Plan

## Overview

The Spotify Queue Watcher is the foundational v3 component. It connects DreamSync to the Spotify Web API, monitors what's currently playing and what's coming next in the queue, and fires callbacks when tracks change. All other v3 features (song structure analyzer, show compiler, playback runtime) depend on this component to know *what* to analyze and *when* to switch shows.

---

## Prerequisites

### 1. Spotify Developer App

- A Spotify Developer application must be registered at <https://developer.spotify.com/dashboard>.
- Required scopes: `user-read-playback-state`, `user-read-currently-playing`.
- The app provides a **Client ID** and **Client Secret**.

### 2. OAuth 2.0 Authorization Code Flow (PKCE)

- Spotify's Web API requires an OAuth access token for user-scoped endpoints.
- We'll use the **Authorization Code with PKCE** flow (no client secret needed at runtime, suitable for CLI apps).
- The token must be refreshed periodically (Spotify tokens expire after 1 hour).

### 3. New Dependency

- `httpx` — async-capable HTTP client (used for API calls). Preferred over `requests` for its async support and modern API. Add to `pyproject.toml` under a new `[project.optional-dependencies] spotify` extra.

### 4. No Existing Spotify Code

- The current codebase has zero Spotify integration. This is a greenfield module.

---

## Architecture

```
src/dreamsync/
├── spotify/
│   ├── __init__.py
│   ├── auth.py           # OAuth PKCE flow, token storage, refresh
│   ├── client.py         # Thin Spotify Web API wrapper
│   ├── queue_watcher.py  # Background polling thread, event dispatch
│   └── models.py         # Dataclasses for track, playback state, queue
```

### Integration Points

- **session.py** — The queue watcher is started/stopped alongside existing watchers (ConfigWatcher, DeviceHealthMonitor, ProfileWatcher). New `--spotify` flag enables it.
- **cli.py** — New `spotify-auth` subcommand for initial OAuth setup. New `--spotify` flag on `session` command.
- **Callbacks** — The queue watcher exposes `on_track_changed(track)` and `on_queue_updated(queue)` callbacks. In Feature 1, these just log. Features 2–5 will wire them to the analyzer/compiler pipeline.

### Design Principles

- **Same daemon-thread pattern** as `ConfigWatcher` and `DeviceHealthMonitor`: init → start() → background loop → stop(). No asyncio in the main loop.
- **Minimal scope**: Feature 1 only polls and dispatches events. No analysis, no compilation, no show playback.
- **Graceful degradation**: If Spotify auth is missing or API calls fail, the session continues in v2 mode. The watcher logs warnings but never crashes the session.

---

## Deliverables

### D1: `spotify/models.py` — Data Models

Frozen dataclasses representing Spotify API responses (only the fields we need):

```python
@dataclass(frozen=True)
class SpotifyTrack:
    track_id: str           # Spotify track ID
    name: str
    artist: str             # Primary artist name
    album: str
    duration_ms: int
    uri: str                # spotify:track:XXX

@dataclass(frozen=True)
class PlaybackState:
    is_playing: bool
    track: SpotifyTrack | None
    progress_ms: int        # Current playback position
    timestamp: float        # Local monotonic time when this was fetched
    device_name: str        # Spotify playback device name
    shuffle: bool
    repeat: str             # "off", "track", "context"

@dataclass(frozen=True)
class QueueSnapshot:
    currently_playing: SpotifyTrack | None
    queue: tuple[SpotifyTrack, ...]   # Upcoming tracks
    fetched_at: float                  # Local monotonic time
```

### D2: `spotify/auth.py` — OAuth PKCE Authentication

Handles the full OAuth lifecycle:

1. **`start_auth_flow(client_id, redirect_port=8888)`** — Generates PKCE code verifier/challenge, opens the Spotify auth URL in the user's browser, starts a temporary local HTTP server on `redirect_port` to capture the callback, exchanges the auth code for tokens.
2. **`TokenStore`** — Reads/writes tokens to `~/.dreamsync/spotify_token.json`. Fields: `access_token`, `refresh_token`, `expires_at` (epoch seconds), `client_id`.
3. **`refresh_if_needed(token_store)`** — Checks expiry, refreshes via Spotify's `/api/token` endpoint if within 5 minutes of expiry. Updates the stored token file.
4. **`load_or_prompt(client_id)`** — Loads stored token if valid, otherwise triggers `start_auth_flow`.

**Token file location**: `~/.dreamsync/spotify_token.json` (created on first auth, gitignored).

### D3: `spotify/client.py` — API Client

Thin synchronous wrapper around Spotify Web API endpoints:

```python
class SpotifyClient:
    def __init__(self, token_store: TokenStore):
        ...

    def get_playback_state(self) -> PlaybackState | None:
        """GET /v1/me/player — current playback state."""

    def get_queue(self) -> QueueSnapshot:
        """GET /v1/me/player/queue — current queue."""
```

- Uses `httpx.Client` (sync) for simplicity — we're calling from a background thread, not an async loop.
- Automatically calls `refresh_if_needed()` before each request.
- Returns `None` from `get_playback_state()` if nothing is playing (HTTP 204).
- Raises `SpotifyAuthError` if token refresh fails (handled by watcher).
- Raises `SpotifyAPIError` for non-auth API failures (rate limits, server errors).
- Implements exponential backoff for HTTP 429 (rate-limited) responses.

### D4: `spotify/queue_watcher.py` — Queue Watcher

The core polling thread:

```python
class SpotifyQueueWatcher:
    def __init__(
        self,
        client: SpotifyClient,
        *,
        poll_interval: float = 2.0,
        queue_poll_interval: float = 10.0,
        on_track_changed: Callable[[SpotifyTrack, SpotifyTrack | None], None] | None = None,
        on_playback_state_changed: Callable[[PlaybackState], None] | None = None,
        on_queue_updated: Callable[[QueueSnapshot], None] | None = None,
    ):
        ...

    def start(self) -> None: ...
    def stop(self) -> None: ...

    @property
    def current_track(self) -> SpotifyTrack | None: ...

    @property
    def playback_state(self) -> PlaybackState | None: ...

    @property
    def queue(self) -> QueueSnapshot | None: ...
```

**Polling strategy**:
- **Playback state** (`/me/player`): polled every `poll_interval` seconds (default 2s). This gives us current track, progress, and play/pause state.
- **Queue** (`/me/player/queue`): polled every `queue_poll_interval` seconds (default 10s) or immediately after a track change. Queue polling is less frequent because queue changes are less time-critical and we don't want to hit rate limits.
- **Track change detection**: Compare `track_id` from consecutive playback polls. On change, fire `on_track_changed(new_track, previous_track)` and immediately poll the queue.
- **Progress tracking**: Store `(progress_ms, local_timestamp)` from each poll. Between polls, the playback runtime (Feature 5) can interpolate the current position as `progress_ms + (now - local_timestamp) * 1000` (if playing). This is not computed here — just stored.

**Error handling**:
- Auth errors → log warning, attempt token refresh, retry once. If still failing, enter a backoff state (30s between retries) and set `self._auth_failed = True`. The session checks this flag to know whether Spotify is available.
- Rate limits (429) → honor `Retry-After` header, back off.
- Network errors → increment failure counter, log, retry on next interval. After 5 consecutive failures, increase poll interval to 10s. After 10, increase to 30s. Reset on success.

**Thread lifecycle**:
- Daemon thread named `"spotify-queue-watcher"`.
- Uses `threading.Event` for stop signaling (same pattern as `ConfigWatcher`).
- `start()` / `stop()` are idempotent.

### D5: CLI Integration

**New subcommand: `dreamsync spotify-auth`**

```
dreamsync spotify-auth --client-id <ID>
```

Runs the interactive OAuth PKCE flow. Opens the browser, waits for callback, stores token to `~/.dreamsync/spotify_token.json`, prints confirmation.

**New flags on `session` command:**

```
--spotify                 Enable Spotify queue watcher (requires prior auth)
--spotify-client-id ID    Spotify app client ID (can also be set via DREAMSYNC_SPOTIFY_CLIENT_ID env var)
--spotify-poll-interval N Playback poll interval in seconds (default: 2.0)
```

**Environment variable support:**
- `DREAMSYNC_SPOTIFY_CLIENT_ID` — alternative to `--spotify-client-id`

### D6: Session Integration

In `session.py:run_session()`, after the existing watcher setup:

```python
# 4d. Spotify queue watcher
spotify_watcher = None
if spotify:
    from dreamsync.spotify.auth import TokenStore, load_or_prompt
    from dreamsync.spotify.client import SpotifyClient
    from dreamsync.spotify.queue_watcher import SpotifyQueueWatcher

    token_store = TokenStore()
    if not token_store.has_valid_token():
        print("Spotify: no valid token found. Run 'dreamsync spotify-auth' first.")
    else:
        client = SpotifyClient(token_store)
        spotify_watcher = SpotifyQueueWatcher(
            client,
            on_track_changed=lambda new, old: print(
                f"Spotify: now playing '{new.name}' by {new.artist}"
            ),
        )
        spotify_watcher.start()
```

And in the `finally` cleanup block:

```python
if spotify_watcher is not None:
    spotify_watcher.stop()
```

---

## Step-by-Step Implementation Plan

### Step 1: Add `httpx` dependency

**File**: `pyproject.toml`

Add `spotify` optional dependency group:
```toml
[project.optional-dependencies]
spotify = ["httpx>=0.27"]
```

Update `session` group to include `spotify`:
```toml
session = ["bleak>=0.21", "pyyaml>=6.0", "httpx>=0.27"]
```

**Completion**: `pip install -e ".[spotify]"` succeeds.

### Step 2: Create `spotify/models.py`

**Files**: `src/dreamsync/spotify/__init__.py`, `src/dreamsync/spotify/models.py`

Implement the three frozen dataclasses: `SpotifyTrack`, `PlaybackState`, `QueueSnapshot`. Include a `parse_track(data: dict) -> SpotifyTrack` helper that extracts the fields we need from Spotify's raw JSON.

**Completion**: Unit tests pass (see T1 below).

### Step 3: Create `spotify/auth.py`

**File**: `src/dreamsync/spotify/auth.py`

Implement:
- `TokenStore` — file-based token persistence at `~/.dreamsync/spotify_token.json`
- `generate_pkce_pair()` — code verifier (128 chars, URL-safe base64) + SHA256 challenge
- `start_auth_flow(client_id, redirect_port)` — browser open + local HTTP callback server + token exchange
- `refresh_if_needed(token_store)` — conditional refresh via `/api/token`
- `load_or_prompt(client_id)` — convenience wrapper

**Completion**: `dreamsync spotify-auth --client-id <test_id>` opens browser, captures callback, writes token file. Unit tests for `TokenStore` CRUD and PKCE generation pass.

### Step 4: Create `spotify/client.py`

**File**: `src/dreamsync/spotify/client.py`

Implement `SpotifyClient` with:
- `get_playback_state()` → `PlaybackState | None`
- `get_queue()` → `QueueSnapshot`
- Auto token refresh before requests
- Error handling: `SpotifyAuthError`, `SpotifyAPIError`
- Rate limit handling (429 + `Retry-After`)

**Completion**: Manual test against real Spotify account confirms both endpoints return correct data. Unit tests with mocked HTTP responses pass (see T2).

### Step 5: Create `spotify/queue_watcher.py`

**File**: `src/dreamsync/spotify/queue_watcher.py`

Implement `SpotifyQueueWatcher` with:
- Dual-interval polling (playback fast, queue slow)
- Track change detection and callback dispatch
- Error backoff logic
- Thread lifecycle (`start`/`stop`)

**Completion**: Unit tests with a mock client pass (see T3). Manual integration test confirms track changes are detected within 2–4 seconds.

### Step 6: CLI integration

**Files**: `src/dreamsync/cli.py`

Add:
- `spotify-auth` subcommand
- `--spotify`, `--spotify-client-id`, `--spotify-poll-interval` flags to `session`

**Completion**: `dreamsync spotify-auth --help` and `dreamsync session --help` show new options.

### Step 7: Session integration

**File**: `src/dreamsync/session.py`

Wire `SpotifyQueueWatcher` into `run_session()` with proper start/stop lifecycle.

**Completion**: `dreamsync session --config dev/devices.yaml --spotify` starts the watcher, prints track changes to stdout, and stops cleanly on Ctrl+C.

---

## Tests

### T1: `test_spotify_models.py`

Location: `dev/tests/test_spotify_models.py`

| Test | Description |
|---|---|
| `test_parse_track_from_api_json` | Feed a real Spotify API track JSON blob into `parse_track()`, assert all fields map correctly. |
| `test_parse_track_missing_fields` | Feed incomplete JSON, assert graceful defaults (e.g., empty artist). |
| `test_playback_state_immutable` | Confirm `PlaybackState` is frozen (raises on attribute assignment). |
| `test_queue_snapshot_tuple` | Confirm `QueueSnapshot.queue` is a tuple, not a mutable list. |

### T2: `test_spotify_client.py`

Location: `dev/tests/test_spotify_client.py`

Uses `unittest.mock.patch` to mock `httpx.Client.get` responses.

| Test | Description |
|---|---|
| `test_get_playback_state_playing` | Mock 200 response with playing track → returns `PlaybackState` with correct fields. |
| `test_get_playback_state_nothing_playing` | Mock 204 response → returns `None`. |
| `test_get_playback_state_token_refresh` | Mock expired token → assert `refresh_if_needed` called before request. |
| `test_get_queue_returns_snapshot` | Mock 200 with queue data → returns `QueueSnapshot` with correct track list. |
| `test_rate_limit_retry` | Mock 429 with `Retry-After: 1` → assert client waits and retries. |
| `test_auth_error_raises` | Mock 401 after refresh attempt → raises `SpotifyAuthError`. |

### T3: `test_queue_watcher.py`

Location: `dev/tests/test_queue_watcher.py`

Uses a mock `SpotifyClient` that returns scripted responses.

| Test | Description |
|---|---|
| `test_detects_track_change` | Mock client returns track A, then track B. Assert `on_track_changed` fires with `(B, A)`. |
| `test_no_callback_when_same_track` | Mock client returns same track twice. Assert `on_track_changed` is NOT called. |
| `test_queue_polled_on_track_change` | Assert that `get_queue()` is called immediately after a track change, not on the slow interval. |
| `test_handles_nothing_playing` | Mock client returns `None` playback state. Assert no crash, no callback. |
| `test_error_backoff` | Mock client raises network error 5 times. Assert poll interval increases. |
| `test_start_stop_idempotent` | Call `start()` twice, `stop()` twice — no errors, thread count stays at 1. |
| `test_properties_thread_safe` | Read `.current_track` and `.playback_state` from main thread while watcher runs — no race conditions. |

### T4: `test_spotify_auth.py`

Location: `dev/tests/test_spotify_auth.py`

| Test | Description |
|---|---|
| `test_pkce_verifier_length` | Generated verifier is 128 characters of URL-safe base64. |
| `test_pkce_challenge_is_sha256` | Challenge matches `base64url(sha256(verifier))`. |
| `test_token_store_save_load` | Write token to temp file, load it back, assert roundtrip equality. |
| `test_token_store_expired` | Store a token with `expires_at` in the past → `has_valid_token()` returns `False`. |
| `test_token_store_near_expiry` | Store a token expiring in 3 minutes → `needs_refresh()` returns `True` (5-minute buffer). |
| `test_token_file_permissions` | On non-Windows: token file created with 0o600 permissions. |

---

## Completion Criteria

Feature 1 is **done** when all of the following are true:

1. **Auth flow works end-to-end**: `dreamsync spotify-auth --client-id <ID>` opens the browser, user authorizes, token is saved to `~/.dreamsync/spotify_token.json`, and subsequent API calls succeed without re-auth.

2. **Queue watcher detects track changes**: When running `dreamsync session --config <config> --spotify`, changing the song in Spotify causes `on_track_changed` to fire and a log message to print within 4 seconds.

3. **Queue lookahead works**: The watcher's `.queue` property returns the upcoming tracks from Spotify's queue. Adding a song to the queue in Spotify causes `on_queue_updated` to fire within 15 seconds.

4. **Playback position tracking**: The watcher's `.playback_state` provides `progress_ms` and `timestamp` accurate enough for the future playback runtime to interpolate position within ~100ms accuracy.

5. **Graceful degradation**: If `--spotify` is passed but no token exists, the session prints a helpful message and continues in v2 mode. If Spotify becomes unreachable mid-session, the watcher enters backoff and logs warnings but does not crash the session.

6. **Token refresh works**: Sessions lasting longer than 1 hour transparently refresh the Spotify token without user intervention.

7. **All tests pass**: `pytest dev/tests/test_spotify_*.py` runs clean (20+ tests across T1–T4).

8. **No regressions**: Existing `pytest` suite still passes. The `session` command without `--spotify` behaves identically to before.

---

## Rate Limit Awareness

Spotify's Web API has a rolling rate limit (typically ~180 requests per minute per app, though not officially documented). Our polling budget:

- Playback state: 30 req/min (every 2s)
- Queue: 6 req/min (every 10s)
- Token refresh: ~1 req/hour
- **Total: ~36 req/min** — well within limits.

If rate-limited, the watcher backs off per the `Retry-After` header and temporarily increases poll intervals.

---

## Future Hooks (Not Implemented in Feature 1)

The `on_track_changed` and `on_queue_updated` callbacks are the integration points for Features 2–5:

- **Feature 2** (Song Structure Analyzer): `on_queue_updated` triggers analysis of new queue entries.
- **Feature 3** (Show Compiler): After analysis completes, the compiler generates a timeline.
- **Feature 5** (Playback Runtime): Uses `playback_state.progress_ms` + `timestamp` to seek into the compiled timeline each frame.
- **Feature 6** (v2 Fallback): If `spotify_watcher.auth_failed` or no compiled show exists, the session stays in v2 Director mode.

These are called out here for context but are explicitly out of scope for Feature 1.
