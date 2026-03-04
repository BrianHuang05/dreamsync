# v3 Capability Assessment: MP3 In → Audio Out + BLE Commands

## Goal

Confirm whether the v3 project can take a **service-agnostic MP3 stream** as input and output a **live audio stream** + **BLE commands**.

---

## Can it take a service-agnostic MP3 stream as input?

**Partially (~80%).** The analysis/compilation pipeline is fully service-agnostic:

- `decode_mp3()` uses ffmpeg — works with any MP3/WAV/FLAC/OGG
- `analyze_song()` is pure local DSP (beat grid, section segmentation, mood)
- `compile_show()` is pure data transformation (SongStructure → ShowTimeline)
- `ShowCache` works with any track ID scheme

**The gap:** The v3 runtime (`SpotifyShowSession`, `PositionInterpolator`) is Spotify-coupled for **position tracking** and **track change detection**. Without Spotify, the system doesn't know *where* in the song you are to drive the timeline.

### Component Dependency Matrix

| Component | Requires Spotify | Requires Audio File | Offline Capable |
|---|---|---|---|
| `decode_mp3()` | No | Yes (any ffmpeg format) | Yes |
| `OfflineFeaturePipeline` | No | No (takes raw PCM) | Yes |
| `GlobalBpmEstimator` | No | No (takes feature rows) | Yes |
| `SectionSegmenter` | No | No (takes feature rows) | Yes |
| `analyze_song()` | No | Yes (any format via ffmpeg) | Yes |
| `NarrativeArcPlanner` | No | No (pure data) | Yes |
| `TreatmentSelector` | No | No (pure data) | Yes |
| `TransitionPlanner` | No | No (pure data) | Yes |
| `compile_show()` | No | No (pure data) | Yes |
| `ShowPlaybackRuntime` | No | No (needs position) | Yes (if position provided) |
| `ShowCache` | No | No (pure storage) | Yes |
| `PositionInterpolator` | **Yes** (requires progress_ms) | No | No |
| `SpotifyShowSession` | **Yes** (requires queue watcher) | No | No |
| `SpotifyQueueWatcher` | **Yes** (requires Spotify API) | No | No |
| `AudioPlayer` (show/player.py) | No | Yes (MP3 decode + local playback) | Yes |

---

## Can it output BLE commands?

**Yes.** `GoveeBleAdapter` sends 20-byte GATT packets (color, brightness, power, keep-alive) to Govee bulbs/strips via `bleak`. Fully functional and decoupled from audio source.

### BLE Protocol Details

- Service UUID: `00010203-0405-0607-0809-0a0b0c0d1910`
- Write Characteristic: `00010203-0405-0607-0809-0a0b0c0d2b11`
- Commands: set color (RGB), brightness, power on/off, segment color, keep-alive
- Rate: ~5 Hz, background daemon thread with asyncio
- Targets: Govee bulbs, portables, BLE-only strips

### LAN Protocol (also available)

- UDP port 4003, three transport modes:
  - **Razer/DreamView**: binary packets, per-LED control, 30 Hz
  - **ptReal**: BLE packets wrapped in JSON, per-segment, 20 Hz
  - **COLORWC**: whole-strip single color JSON, 10 Hz fallback

---

## Can it output a live audio stream?

**No.** The system is exclusively a **lighting control system**. Audio is only:

- **Consumed** for feature analysis (v2 live mode via `sounddevice` capture)
- **Played locally** on system speakers (`AudioPlayer` in `show/player.py` uses `sounddevice.OutputStream`)

No audio data is ever sent to Govee devices or streamed externally. All device communication is RGB color commands only.

---

## Architecture: What Exists Today

```
┌──────────────────────────────────┐
│  MP3/WAV File (Service-Agnostic) │
└──────────────┬───────────────────┘
               │
               ▼
     ┌───────────────────┐
     │  ffmpeg Decoder    │  ← any format, no Spotify
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
    │  Playback Runtime    │  ← needs position source
    │  tick(t) → Intent    │
    └────────┬─────────────┘
             │
      ┌──────┴──────┐
      │             │
      ▼             ▼
  SPOTIFY       AudioPlayer
  (v3 today)    (local playback)
  position via   position via
  API polling    sounddevice
      │             │
      └──────┬──────┘
             │
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

## Gaps for Service-Agnostic MP3 → Audio Out + BLE

| Requirement | Status | Gap |
|---|---|---|
| MP3 input (service-agnostic) | ~80% done | Runtime needs a non-Spotify position provider |
| BLE lighting commands | Done | — |
| LAN lighting commands | Done | — |
| Live audio stream output | Not built | No audio streaming/output infrastructure |

### What's Needed

1. **Generic playback session** — Replace `SpotifyShowSession` with a session that uses `AudioPlayer` for position tracking instead of Spotify API. The `AudioPlayer` + `ShowPlaybackRuntime` combo is close: it already plays MP3 locally and tracks position. The missing piece is wiring that position into the v3 show runtime without Spotify in the loop.

2. **Audio output streaming** — If the goal is to stream audio to an external sink (not just system speakers), a new audio transport layer is needed. Options:
   - Pipe `sounddevice.OutputStream` to a network sink
   - Add a Bluetooth A2DP audio profile (separate from BLE lighting)
   - Stream via HTTP/RTSP/RTP to a receiver
   - Use system audio routing (e.g., virtual audio cable) as a workaround

3. **Track change detection without Spotify** — Need a way to detect when one song ends and the next begins. Options:
   - Playlist/queue file that lists MP3s in order
   - Directory watcher that picks up new files
   - Manual CLI commands to load next track
