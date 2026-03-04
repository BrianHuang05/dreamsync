# DreamSync v3 — Service-Agnostic Show Engine

## Vision

v3 evolves from a Spotify-coupled show engine into a **service-agnostic lighting system**. Any MP3/WAV/FLAC/OGG file goes in; pre-compiled lighting commands come out over Govee LAN and BLE — no Spotify, no cloud APIs, no accounts required.

The analysis and compilation pipeline is _already_ service-agnostic: `decode_mp3()` uses ffmpeg, `analyze_song()` is pure local DSP, `compile_show()` is pure data transformation, and `ShowCache` works with any track ID. The remaining work is decoupling the **runtime layer** — position tracking, track sequencing, and session orchestration — from Spotify so the system runs standalone with local audio files.

---

## Goals

1. **Fully offline** — Analyze, compile, and play light shows from local MP3 files with zero network dependencies. No Spotify account, no API keys, no cloud services.
2. **Service-agnostic input** — Accept any audio format ffmpeg can decode (MP3, WAV, FLAC, OGG, AAC). The same pipeline that works with Spotify-sourced analysis works with local files.
3. **Local position tracking** — Drive the show timeline from `AudioPlayer`'s sounddevice playback position instead of Spotify API polling. Same `tick(t)` interface, different position source.
4. **Local playlist sequencing** — Detect track changes and advance through a playlist of local files without Spotify's queue API. Support directory-based, file-list, and CLI-driven track loading.
5. **Preserve Spotify mode** — Keep `SpotifyShowSession` as an optional mode. The system supports both Spotify-connected and standalone local sessions.
6. **Same output stack** — Reuse the v2/v3 renderer, Govee LAN adapter, Govee BLE adapter, profiles, and mood system unchanged.

---

## How Service-Agnostic Differs from Spotify-Only v3

| | v3 Spotify Mode | v3 Service-Agnostic Mode |
|---|---|---|
| Audio source | Spotify playback on user's device | Local MP3/WAV/FLAC/OGG file |
| Position tracking | Spotify API polling → `PositionInterpolator` | `AudioPlayer.position_seconds` (sounddevice) |
| Track change detection | `SpotifyQueueWatcher` polls `/me/player` | Playlist manager: end-of-track callback or CLI `next` |
| Queue lookahead | Spotify queue API (`/me/player/queue`) | Local playlist file or directory scan |
| Analysis source | Offline DSP (same) | Offline DSP (same) |
| Compilation | `compile_show()` (same) | `compile_show()` (same) |
| Cache key | Spotify track ID + profile fingerprint | File hash or filename + profile fingerprint |
| Runtime | `SpotifyShowSession` | `LocalShowSession` (new) |
| Device output | Govee LAN + BLE (same) | Govee LAN + BLE (same) |
| Network required | Yes (Spotify API) | No |

---

## Component Dependency Matrix

| Component | Requires Spotify | Requires Audio File | Offline Capable | Status |
|---|---|---|---|---|
| `decode_mp3()` | No | Yes (any ffmpeg format) | Yes | **DONE** |
| `OfflineFeaturePipeline` | No | No (takes raw PCM) | Yes | **DONE** |
| `GlobalBpmEstimator` | No | No (takes feature rows) | Yes | **DONE** |
| `SectionSegmenter` | No | No (takes feature rows) | Yes | **DONE** |
| `analyze_song()` | No | Yes (any format via ffmpeg) | Yes | **DONE** |
| `NarrativeArcPlanner` | No | No (pure data) | Yes | **DONE** |
| `TreatmentSelector` | No | No (pure data) | Yes | **DONE** |
| `TransitionPlanner` | No | No (pure data) | Yes | **DONE** |
| `compile_show()` | No | No (pure data) | Yes | **DONE** |
| `ShowPlaybackRuntime` | No | No (needs `t` float) | Yes | **DONE** |
| `ShowCache` | No | No (pure storage) | Yes | **DONE** |
| `AudioPlayer` | No | Yes (MP3 decode + playback) | Yes | **DONE** |
| `SegmentRenderer` | No | No (pure rendering) | Yes | **DONE** |
| `GoveeBleAdapter` | No | No (GATT packets) | Yes | **DONE** |
| `GoveeLanAdapter` | No | No (UDP packets) | Yes | **DONE** |
| `PositionInterpolator` | **Yes** | No | No | **DONE** (Spotify only) |
| `SpotifyShowSession` | **Yes** | No | No | **DONE** (Spotify only) |
| `SpotifyQueueWatcher` | **Yes** | No | No | **DONE** (Spotify only) |
| `LocalShowSession` | No | Yes | Yes | **TODO** (Feature 7) |
| `PlaylistManager` | No | Yes | Yes | **TODO** (Feature 8) |

---

## Feature Overview

### 1. Spotify Queue Watcher — **DONE** (`7c3372b`)

- Polls the Spotify Web API (`/me/player` and `/me/player/queue`) on configurable intervals.
- Fires `on_track_changed` and `on_queue_updated` callbacks for downstream features to hook into.
- Tracks queue additions in real time — if a user adds a song mid-session, the watcher picks it up within ~15s.
- Monitors current playback position (`/me/player`) to synchronize show playback with the actual track timestamp.
- OAuth PKCE auth flow via `dreamsync spotify-auth`, token auto-refresh, graceful v2 fallback if Spotify is unavailable.

### 2. Song Structure Analyzer — **DONE** (213 tests across C3+C4+C5)

The core analytical capability. Given any audio file, produce a structural map:

- **Bar/beat grid** — Absolute timestamps for every beat and downbeat via offline beat tracking.
- **Section segmentation** — Identify repeating structural units: intro, verse, pre-chorus, chorus, bridge, drop, outro. Uses pitch/timbre self-similarity clustering.
- **Macro pattern detection** — Recognize common song forms (verse-chorus, EDM drop structures, etc.).
- **Per-section feature summary** — Energy, BPM, mood per section. Feeds into palette/effect selection.

### 3. Show Compiler — **DONE** (58 tests across C1–C5)

Takes the structural map and produces a **show timeline** — a list of timed cues:

- Each section maps to a **lighting treatment**: effect mode, color palette, intensity curve, speed, and transition style.
- Uses the existing profile system (YAML mood → effect pools) to select treatments.
- Transitions between sections are compiled with explicit crossfade or hard-cut timing, aligned to bar boundaries.
- **Narrative arc** rules: build energy across verse → pre-chorus → chorus, reset at bridge, peak at final chorus.
- Output format: a JSON timeline of timestamped cues that the runtime can seek into at any point.

### 4. Show Cache — **DONE** (36 tests across D4.1–D4.4)

- Compiled shows are cached by track ID + profile fingerprint.
- If a song has been compiled before, skip re-analysis.
- Cache invalidation: profile change or manual flush via `cache-clear`.
- File-based KV store: `~/.dreamsync/cache/{track_id}/{fingerprint}.show.json`.
- CLI: `cache-list`, `cache-clear`, `cache-info` subcommands.

### 5. Playback Runtime — **DONE** (22 tests across D5.1–D5.3)

- `ShowPlaybackRuntime.tick(t)` takes a float position in seconds, seeks into the compiled timeline, and emits the corresponding `LightingIntent` to the SegmentRenderer.
- **Already position-source agnostic** — `tick(t)` does not care where `t` comes from. This is the key decoupling point.
- Handles pauses, seeks, and track skips by re-syncing to whatever position is provided.
- The renderer, device adapters (Govee LAN / BLE), health monitor, and profile system are **unchanged** from v2.

### 6. Spotify Show Session — **DONE**

- `SpotifyShowSession` orchestrates the Spotify-connected mode: track change detection via queue watcher, position via `PositionInterpolator`, background precompilation.
- `PositionInterpolator` derives smooth position from periodic Spotify API polls with slew-limited drift correction and seek detection.
- CLI: `--v3` flag on the `session` subcommand. `run_v3_session()` entry point.
- Falls back to v2 Director if Spotify is unavailable or analysis fails.

### 7. Local Show Session — **TODO**

The core new feature. A standalone session that drives shows from local audio files without Spotify:

- **`LocalShowSession`** — New session class that replaces `SpotifyShowSession` for local playback. Same lifecycle (analyze → compile → cache → play), but uses `AudioPlayer` for both audio output and position tracking.
- **Position source** — `AudioPlayer.position_seconds` feeds directly into `ShowPlaybackRuntime.tick(t)`. No `PositionInterpolator` needed — `AudioPlayer` tracks position natively via sounddevice frame counting.
- **Track lifecycle** — On session start: decode → analyze → compile → cache → play. On track end: advance to next track from playlist (Feature 8) or stop.
- **Analyze-while-playing** — While the current track plays, precompile the _next_ track in the playlist on a background thread (mirrors Spotify mode's queue precompilation).
- **CLI entry point** — `dreamsync play <file_or_directory>` for single-file or directory mode. `dreamsync session --local <playlist>` for playlist mode. Reuses `--cache-dir` and profile flags.
- **Same output path** — `tick(t) → LightingIntent → SegmentRenderer → Govee LAN/BLE`. Zero changes to the device layer.

### 8. Local Playlist Manager — **TODO**

Track sequencing and queue management without Spotify:

- **Playlist sources:**
  - Single file: `dreamsync play song.mp3`
  - Directory: `dreamsync play ./music/` — scans for audio files, plays in order (alphabetical or shuffled)
  - Playlist file: `dreamsync play playlist.m3u` — reads `.m3u` / `.m3u8` / plain text file list
- **Track advancement** — End-of-track detection via `AudioPlayer` (sounddevice stream completes). Fires `on_track_ended` callback to trigger next-track loading.
- **CLI controls** — `next`, `prev`, `stop` commands (or keyboard shortcuts) during a running session.
- **Shuffle and repeat** — `--shuffle` flag randomizes playlist order. `--repeat` loops the playlist.
- **Cache key scheme** — Use content hash (SHA256 of first 64KB + file size) as track ID for cache lookups. Stable across renames, unique across different files.

---

## Architecture: Service-Agnostic Mode

```
┌──────────────────────────────────┐
│  MP3/WAV/FLAC/OGG File (local)   │
└──────────────┬───────────────────┘
               │
               ▼
     ┌───────────────────┐
     │  ffmpeg Decoder    │  ← any format, fully offline
     └────────┬──────────┘
              │
              ▼ PCM Signal
    ┌──────────────────────┐
    │  Feature Pipeline    │  ← pure signal processing
    │  (spectral, beat,    │
    │   BPM, mood)         │
    └────────┬─────────────┘
             │
             ▼ FeatureRow[]
    ┌──────────────────────┐
    │  Section Segmenter   │  ← self-similarity clustering
    └────────┬─────────────┘
             │
             ▼ SongStructure
    ┌──────────────────────┐
    │  Show Compiler       │  ← profile-based mapping
    │  (arc, treatments,   │
    │   transitions)       │
    └────────┬─────────────┘
             │
             ▼ ShowTimeline (JSON)
    ┌──────────────────────┐
    │  Show Cache          │  ← file hash + profile fingerprint
    └────────┬─────────────┘
             │
             ▼
    ┌──────────────────────┐
    │  LocalShowSession    │  ← new orchestrator
    │  (playlist manager,  │
    │   background compile)│
    └────────┬─────────────┘
             │
      ┌──────┴──────┐
      │             │
      ▼             ▼
  AudioPlayer   ShowPlaybackRuntime
  (sounddevice)  tick(position_seconds)
  speakers out       │
                     ▼
            ┌──────────────────────┐
            │  SegmentRenderer     │  ← intent → per-segment RGB
            └────────┬─────────────┘
                     │
              ┌──────┴──────┐
              │             │
              ▼             ▼
          Govee LAN     Govee BLE
          (UDP 4003)    (GATT packets)
```

---

## What v3 Reuses (Unchanged)

- `render.py` — SegmentRenderer (SOLID, PULSE, BREATHE, SCROLL, WAVE, GRADIENT)
- `output/govee_lan.py`, `output/govee_ble.py`, `MultiGoveeLanAdapter` — device transport
- `output/auto_detect.py`, `output/discovery.py`, `output/roles.py` — device config and role classification
- `profile.py`, `profiles/` — YAML mood profiles, hot-reload, profile rotation
- `mood.py` — mood vocabulary (CHILL, GROOVE, HYPE, DROP)
- `effects.py` — effect presets and palettes
- `device_health.py` — health monitoring
- `config_watcher.py` — device config hot-reload
- `session.py` — session orchestration, device probing
- `show/runtime.py` — `ShowPlaybackRuntime.tick(t)` (position-agnostic)
- `show/player.py` — `AudioPlayer` (local playback + position tracking)
- `analyzer/` — entire analysis pipeline (ffmpeg decode, feature extraction, BPM, sections)
- `compiler/` — entire compilation pipeline (arc, treatments, transitions, assembler)
- `cache.py` — `ShowCache` (track ID + profile fingerprint)
- `spotify/` — kept as optional mode, not removed

---

## Non-Goals

- **Audio streaming to external sinks** — The system plays audio locally via system speakers. Streaming audio over network/Bluetooth A2DP is out of scope. Users who want audio on a different device should use system-level audio routing (e.g., virtual audio cable, Bluetooth speaker pairing at OS level).
- **Manual show editor** — No GUI or hand-tweaking of compiled timelines.
- **New effect modes** — Same 6 render modes from v2. New effects are orthogonal.
- **New device protocols** — Same Govee LAN/BLE stack. No new hardware support.
- **Removing Spotify support** — Spotify mode remains fully functional. This plan _adds_ local mode alongside it.
