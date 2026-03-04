# Feature 1 — Spotify Queue Watcher

**Status**: DONE — committed `7c3372b` on `govee-lan-direct`

---

## Overview

The Spotify Queue Watcher is the foundational v3 component. It connects DreamSync to the Spotify Web API, monitors what's currently playing and what's coming next in the queue, and fires callbacks when tracks change. All other v3 features (song structure analyzer, show compiler, playback runtime) depend on this component to know *what* to analyze and *when* to switch shows.

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

- **session.py** — The queue watcher is started/stopped alongside existing watchers (ConfigWatcher, DeviceHealthMonitor, ProfileWatcher). `--spotify` flag enables it.
- **cli.py** — `spotify-auth` subcommand for OAuth setup. `--spotify`, `--spotify-client-id`, `--spotify-poll-interval` flags on `session`.
- **Callbacks** — `on_track_changed(new_track, old_track)` and `on_queue_updated(queue_snapshot)`. In Feature 1 these just log. Features 2–5 will wire them to the analyzer/compiler pipeline.

### Design Principles

- **Same daemon-thread pattern** as `ConfigWatcher` and `DeviceHealthMonitor`: init → start() → background loop → stop(). No asyncio.
- **Minimal scope**: Feature 1 only polls and dispatches events. No analysis, no compilation, no show playback.
- **Graceful degradation**: If Spotify auth is missing or API calls fail, the session continues in v2 mode.

---

## What Was Built

### D1: `spotify/models.py` — Data Models

Frozen dataclasses: `SpotifyTrack`, `PlaybackState`, `QueueSnapshot`. Plus `parse_track(data: dict)` helper for Spotify JSON → dataclass conversion.

### D2: `spotify/auth.py` — OAuth PKCE Authentication

- `start_auth_flow(client_id, redirect_port=8888)` — PKCE code verifier/challenge, browser open, local callback server, token exchange. No client secret needed.
- `TokenStore` — Reads/writes tokens to `~/.dreamsync/spotify_token.json` (access_token, refresh_token, expires_at, client_id).
- `refresh_if_needed(token_store)` — Conditional refresh via Spotify `/api/token` endpoint (5-minute buffer before expiry).

### D3: `spotify/client.py` — API Client

- `SpotifyClient.get_playback_state()` → `PlaybackState | None` (GET `/v1/me/player`)
- `SpotifyClient.get_queue()` → `QueueSnapshot` (GET `/v1/me/player/queue`)
- Auto token refresh before each request. Exponential backoff on HTTP 429.
- Raises `SpotifyAuthError` / `SpotifyAPIError` (handled by watcher).

### D4: `spotify/queue_watcher.py` — Queue Watcher

- Dual-interval polling: playback state every 2s, queue every 10s (or immediately after a track change).
- Track change detection via `track_id` comparison across consecutive polls.
- Error backoff: 5 consecutive failures → 10s interval, 10 failures → 30s. Resets on success.
- Daemon thread with `threading.Event` stop signal. `start()`/`stop()` are idempotent.
- Exposes `.current_track`, `.playback_state`, `.queue` properties (thread-safe).

### D5: CLI Integration

- `dreamsync spotify-auth --client-id <ID> [--port 8888]`
- `dreamsync session ... --spotify [--spotify-client-id ID] [--spotify-poll-interval N]`
- `DREAMSYNC_SPOTIFY_CLIENT_ID` env var support.

### D6: Session Integration

`SpotifyQueueWatcher` wired into `run_session()` with proper start/stop lifecycle and graceful fallback (prints message and continues in v2 mode if no valid token).

### Dependency

`httpx>=0.27` added to `pyproject.toml` under `[project.optional-dependencies]` as both `spotify` extra and included in `session` extra.

---

## Tests

30 unit tests across 4 files, all passing. 599 total suite tests pass with no regressions.

| File | Count | Coverage |
|------|-------|----------|
| `dev/tests/test_spotify_models.py` | Models, parse_track, immutability |
| `dev/tests/test_spotify_auth.py` | PKCE generation, TokenStore CRUD, expiry checks |
| `dev/tests/test_spotify_client.py` | Mocked HTTP responses, token refresh, rate limits, errors |
| `dev/tests/test_queue_watcher.py` | Track change detection, queue polling, error backoff, thread lifecycle |

Manual validation scripts: `dev/tests/manual_spotify_watcher.py`, `dev/tests/manual_spotify_oneshot.py`.

---

## Validation Results

| # | Criterion | Status |
|---|-----------|--------|
| 1 | Auth flow end-to-end (OAuth PKCE → token saved) | PASSED |
| 2 | Track change detection (within 4s) | PASSED |
| 3 | Queue lookahead (`on_queue_updated` fires within 15s) | PASSED |
| 4 | Playback position tracking (`progress_ms` + `timestamp`) | PASSED |
| 5 | Graceful degradation (no token → v2 fallback) | Deferred — code implemented, manual test skipped |
| 6 | Token refresh (>1hr sessions) | Deferred — will validate during extended use |
| 7 | All unit tests pass (30 spotify tests, 599 total) | PASSED |
| 8 | No regressions (existing suite unchanged) | PASSED |

Items 5 and 6 are implemented in code and covered by unit tests but were not manually validated end-to-end. They will be exercised during normal Feature 2+ development.

---

## Rate Limit Budget

| Endpoint | Frequency | Requests/min |
|----------|-----------|-------------|
| `/me/player` (playback state) | Every 2s | 30 |
| `/me/player/queue` | Every 10s | 6 |
| `/api/token` (refresh) | ~1/hour | <1 |
| **Total** | | **~36** |

Spotify's rolling limit is ~180 req/min. Well within budget.

---

## Callbacks for Feature 2+

The `on_track_changed` and `on_queue_updated` callbacks are the integration points for downstream features:

- **Feature 2** (Song Structure Analyzer): `on_queue_updated` triggers analysis of new queue entries.
- **Feature 3** (Show Compiler): After analysis completes, the compiler generates a timeline.
- **Feature 5** (Playback Runtime): Uses `playback_state.progress_ms` + `timestamp` to seek into the compiled timeline.
- **Feature 6** (v2 Fallback): If `spotify_watcher.auth_failed` or no compiled show exists, stay in v2 Director mode.
