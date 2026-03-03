# DreamSync — Next Steps

## Quick Reference

- **Project**: Local audio-reactive Govee LED controller (LAN UDP / BLE). v2 complete, building v3.
- **v3 vision**: Pre-sequenced show engine. See `dev/plans/v3-show-sequencer.md`.
- **Platform**: Windows 11, bash/Unix shell syntax, Python 3.11+
- **Install**: `pip install -e ".[session]"`
- **Tests**: `pytest dev/tests/`
- **Lint**: `ruff check src/`
- **Branch**: `govee-lan-direct` (primary)

---

## v3 Feature Checklist

| # | Feature | Status | Plan |
|---|---------|--------|------|
| 1 | Spotify Queue Watcher | Code complete, needs manual validation | `dev/plans/feature-1-spotify-queue-watcher.md` |
| 2 | Song Structure Analyzer | Not started | — |
| 3 | Show Compiler | Not started | — |
| 4 | Show Cache | Not started | — |
| 5 | Playback Runtime | Not started | — |
| 6 | v2 Fallback Switch | Not started | — |

---

## Current: Validate Feature 1 (Spotify Queue Watcher)

Code is written (`src/dreamsync/spotify/`), 30 unit tests pass, 599 total tests pass with no regressions. Manual validation steps remain.

### 1. Set up Spotify Developer App

- [x] Go to https://developer.spotify.com/dashboard and create an app (or use an existing one)
- [x] Set the redirect URI to `http://127.0.0.1:8888/callback`
- [x] Copy the **Client ID** from the app dashboard

### 2. Authorize DreamSync

- [x] Run: `dreamsync spotify-auth --client-id <YOUR_CLIENT_ID>`
- [x] Browser opens, log in and approve the scopes
- [x] Confirm the token was saved: `cat ~/.dreamsync/spotify_token.json`
- [x] Verify the file contains `access_token`, `refresh_token`, `expires_at`, and `client_id`

### 3. Test track change detection (headless — no lights needed)

Start playing music in Spotify on any device, then run the watcher:

```bash
python dev/tests/manual_spotify_watcher.py
```

- [x] Verify `TRACK CHANGED: "<song>" by <artist>` prints within 4 seconds of a song starting
- [x] Skip to a different song — verify the new track prints within 4 seconds
- [x] Verify `QUEUE UPDATED: N upcoming tracks` prints within 15 seconds

### 4. Test one-shot playback & queue fetch (headless)

Quick sanity check that the client can talk to the API at all:

```bash
python dev/tests/manual_spotify_oneshot.py
```

- [x] Verify it prints current track info (or "Nothing playing" if paused)
- [x] Verify queue lists upcoming tracks

### 5. Test graceful degradation (headless)

```bash
# Temporarily move the token file aside, then verify graceful failure:
mv ~/.dreamsync/spotify_token.json ~/.dreamsync/spotify_token.json.bak

python -c "
from dreamsync.spotify.auth import TokenStore, refresh_if_needed
store = TokenStore()
print(f'has_valid_token: {store.has_valid_token()}')
print(f'refresh result: {refresh_if_needed(store)}')
if not store.has_valid_token():
    print('PASS: no valid token found (expected)')
else:
    print('FAIL: token should not be valid')
"

# Restore the token file:
mv ~/.dreamsync/spotify_token.json.bak ~/.dreamsync/spotify_token.json
```

- [ ] Verify output shows `has_valid_token: False` and `PASS: no valid token found`
- [ ] Verify no crash / traceback

### 6. Verify no regressions

- [x] Run: `pytest dev/tests/` — all 599 tests should pass
- [ ] Run: `ruff check src/` — no lint errors

### 7. Commit when validated

- [ ] Commit the Feature 1 implementation once manual validation passes

---

## Next Up: Feature 2 — Song Structure Analyzer

No plan file exists yet. Create `dev/plans/feature-2-song-structure-analyzer.md` before starting implementation. Key decisions to make:

- [ ] Decide data source: Spotify Audio Analysis API (`/v1/audio-analysis/{id}`) vs. local librosa analysis vs. both
- [ ] Define the `SongStructure` data model (sections, bars, beats, time signatures)
- [ ] Define how `on_track_changed` / `on_queue_updated` callbacks trigger analysis
- [ ] Write the plan, then implement

Features 3-6 follow in order. See `dev/plans/v3-show-sequencer.md` for the full vision.
