# DreamSync v3 — Pre-sequenced Show Engine

## Vision

v3 replaces the v2 Director's frame-by-frame guessing with **pre-computed show sequences**. Instead of reacting to audio in real time, v3 analyzes each song _before it plays_, detects its macro structure (verse, pre-chorus, chorus, bridge, drop, outro), and compiles a complete lighting timeline. The runtime then simply plays back the timeline in sync with audio.

Spotify queue integration means songs can be analyzed while the _previous_ track is still playing, so transitions are seamless — even when songs are added to the queue in real time.

---

## Goals

1. **Structural awareness** — Detect macro-scale song sections (16-bar verse, 4-bar pre-chorus, 16-bar chorus, 8-bar bridge, etc.) and map each section to a distinct lighting treatment, so the show feels intentional rather than reactive.
2. **Pre-sequenced playback** — Compile a per-song show timeline (section → effect + palette + parameters) ahead of time. No runtime guessing.
3. **Spotify queue lookahead** — Poll Spotify's queue in real time. When a new song appears, fetch its audio features and structure, compile its show, and cache it — all before the song starts playing.
4. **Zero-latency transitions** — Because the show is pre-compiled, section transitions happen _exactly_ on the beat, not after a detection delay.
5. **Same stack** — Reuse the v2 renderer, device output layer, profiles, and mood system wholesale. No new hardware protocols, no new dependencies beyond Spotify API access.

---

## How v3 Differs from v2

| | v2 (Director) | v3 (Show Sequencer) |
|---|---|---|
| Decision timing | Per-frame (~33 ms) | Per-song (compiled before playback) |
| Structural knowledge | None — reacts to instantaneous energy/BPM | Full song structure: verse, chorus, bridge, drop, bar counts |
| Section transitions | Emergent (mood hysteresis, ~2–4 s lag) | Exact (timestamped to the bar) |
| BPM source | Live estimation (autocorrelation + IOI histogram) | Spotify audio features or offline beat tracking (exact, stable) |
| Lookahead | 0 — current frame only | Entire song; queue provides multi-song lookahead |
| Runtime cost | DSP + Director + Renderer every frame | Renderer playback only (timeline seek + frame emit) |

---

## Feature Overview

### 1. Spotify Queue Watcher

- Polls the Spotify Web API (`/me/player/queue`) on a short interval.
- When a new track ID appears in the queue, triggers the analysis/compile pipeline for that track.
- Tracks queue additions in real time — if a user adds a song mid-session, the watcher picks it up and compiles its show before it starts playing.
- Monitors current playback position (`/me/player`) to synchronize show playback with the actual track timestamp.

### 2. Song Structure Analyzer

The core new capability. Given a track, produce a structural map:

- **Bar/beat grid** — Absolute timestamps for every beat and downbeat. Source: Spotify audio analysis API (bars, beats, sections) or offline beat tracking via librosa.
- **Section segmentation** — Identify repeating structural units: intro, verse, pre-chorus, chorus, bridge, drop, outro. Uses Spotify's `sections` plus pitch/timbre self-similarity to refine boundaries.
- **Macro pattern detection** — Recognize common song forms:
  - 16 bars of verse in 4/4 → 4 bar pre-chorus → 16 bar chorus
  - 8-bar intro → verse → chorus → verse → chorus → bridge → chorus → outro
  - EDM: buildup → drop → breakdown → drop
- **Per-section feature summary** — Energy, valence, danceability, tempo, loudness per section (from Spotify audio features + sections data). These feed into mood/palette selection.

### 3. Show Compiler

Takes the structural map and produces a **show timeline** — a list of timed cues:

- Each section maps to a **lighting treatment**: effect mode (SCROLL, PULSE, BREATHE, WAVE, etc.), color palette, intensity curve, speed, and transition style.
- Uses the existing v2 profile system (YAML mood → effect pools) to select treatments. Section energy/mood maps to the same CHILL / GROOVE / HYPE / DROP vocabulary.
- Transitions between sections are compiled with explicit crossfade or hard-cut timing, aligned to bar boundaries.
- The compiler can apply **narrative arc** rules: build energy across verse → pre-chorus → chorus, reset at bridge, peak at final chorus.
- Output format: a JSON timeline of timestamped cues that the runtime can seek into at any point.

### 4. Show Cache

- Compiled shows are cached by Spotify track ID.
- If a song has been compiled before, skip re-analysis.
- Cache invalidation: profile change or manual flush.

### 5. Playback Runtime

- Replaces the v2 Director in the main loop.
- Reads the current Spotify playback position, seeks into the compiled timeline, and emits the corresponding `LightingIntent` to the existing v2 SegmentRenderer.
- The renderer, device adapters (Govee LAN / BLE), health monitor, and profile system are **unchanged** from v2.
- Handles pauses, seeks, and track skips by re-syncing to the Spotify playback position.

### 6. Fallback to v2 Director

- If Spotify is not connected or a track can't be analyzed in time, fall back to v2's live reactive Director seamlessly.
- The runtime can switch between sequenced and reactive modes mid-session without restarting.

---

## What v3 Reuses from v2 (Unchanged)

- `render.py` — SegmentRenderer (SOLID, PULSE, BREATHE, SCROLL, WAVE, GRADIENT)
- `output/govee_lan.py`, `output/govee_ble.py`, `MultiGoveeLanAdapter` — device transport
- `output/auto_detect.py`, `output/discovery.py`, `output/roles.py` — device config and role classification
- `profile.py`, `profiles/` — YAML mood profiles, hot-reload, profile rotation
- `mood.py` — mood vocabulary (CHILL, GROOVE, HYPE, DROP) — used by the compiler
- `effects.py` — effect presets and palettes — used by the compiler
- `device_health.py` — health monitoring
- `config_watcher.py` — device config hot-reload
- `session.py` — session orchestration, device probing
- `audio/system_input.py` — not used for analysis, but still available for v2 fallback
- `dsp/features.py` — not used for real-time detection, but available for v2 fallback

## What v3 Adds

- Spotify Web API client (queue polling, playback position, audio features/analysis)
- Song structure analyzer (section segmentation, macro pattern detection)
- Show compiler (structural map → timed lighting cues)
- Show cache (track ID → compiled timeline)
- Playback runtime (timeline player synced to Spotify position)
- v2 fallback switch

---

## Non-Goals for v3

- **Manual show editor** — No GUI or hand-tweaking of compiled timelines (future consideration).
- **Local audio file analysis** — v3 targets Spotify-connected sessions. Offline file analysis is a separate future project.
- **New effect modes** — v3 uses the same 6 render modes from v2. New effects are orthogonal.
- **New device protocols** — No new hardware support. Same Govee LAN/BLE stack.
