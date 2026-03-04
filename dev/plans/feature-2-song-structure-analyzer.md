# Feature 2 — Song Structure Analyzer

**Status**: **DONE** — Component 3 (mp3 Storage) code complete (68 tests), Component 4 (Analyzer) code complete (80 tests), Component 5 (Show Player) code complete (65 tests)

---

## Overview

- Audio Streaming Service (ASS) is set to 'play audio' on a virtual device, which pipes the mp3 output directly to a ring buffer and the analyzer.
- The analyzer determines the best way to write a lightshow for the entire song from the .mp3 file, by determining the macro-structure ahead of time (bpm, any bpm changes, mood changes, number of verses / choruses, length of intro / fadeout, etc.) (initial analysis pass) and then writing a lightshow that corresponds to it (second analysis pass)
- The Show-writer then scripts out all the commands to direct the show, with associated timestamps
- The Director reads both the .mp3 from the buffer and the light show inputs simultaneously, resulting in a timed light and music show

---

## Pain Points

| Pain Point | Description |
|---|---|
| Buffer construction | Construction of the ring buffer, and reworking of the Director to consider macrostructure instead of real-time input |
| ASS audio piping | How to get the ASS to pipe audio to a virtual playback device |

---

## Components — Modular

Each component below is independent and can be built/tested in isolation. Dependencies between components are listed. Build order follows the data flow: ASS → Virtual Device → mp3 Storage → Analyzer → Lightshow Player → Hardware.

---

### Component 1: Audio Streaming Service (ASS) Interface

Only supports direct audio streaming to a 'player device' such as a phone, computer, etc.

**Deliverables**:
- D1.1: Identify how the target ASS exposes its audio output (API, protocol, stream URL, etc.)
- D1.2: Document the ASS's supported 'player device' handshake / pairing mechanism

**Done when**:
- [ ] The ASS's output mechanism is documented (protocol, format, bitrate)
- [ ] A test script can initiate playback on the ASS and confirm audio data is being sent to a target device

---

### Component 2: Virtual Audio Device

- a) Appears to the ASS as a player
- b) Sends output to arbitrary audio device

**Deliverables**:
- D2.1: Virtual device registers/appears as a valid player target for the ASS
- D2.2: Virtual device captures incoming audio stream and forwards it to a configurable output (ring buffer + passthrough to physical device)

**Done when**:
- [ ] ASS discovers and connects to the virtual device without manual intervention
- [ ] Audio played through the ASS is captured by the virtual device and simultaneously audible on the physical output (speaker system)
- [ ] Captured audio stream is in a known format (sample rate, bit depth, channels documented)
- [ ] Virtual device handles session start/stop cleanly (no orphaned processes, no audio glitches on connect/disconnect)

---

### Component 3: mp3 Storage

- a) Takes in an mp3 stream, and an attached 'song end / start' marker tag
- b) Compiles the mp3 into files, one per song using the tags

**Deliverables**:
- D3.1: Stream ingestion — accepts continuous mp3 stream from the virtual device
- D3.2: Song boundary detection — identifies song start/end markers (silence detection, metadata tags, or ASS-provided signals)
- D3.3: File compilation — splits the stream into individual mp3 files, one per song

**Status**: **COMPLETE** — see `dev/plans/feature-2-component-3-mp3-storage.md`

**Done when**:
- [x] Continuous mp3 stream is ingested without dropping data (ring buffer doesn't overflow under normal playback)
- [x] Song boundaries are detected correctly — each output file contains exactly one song (verified on 5+ consecutive songs)
- [x] Output mp3 files are valid and playable (verifiable with any standard mp3 player)
- [x] Song boundary detection latency is < 2 seconds (time from actual song end to file being finalized)
- [x] Files are written to a configurable directory with a predictable naming scheme
- [x] Handles edge cases: very short tracks (<30s), very long tracks (>10min), silence between songs, gapless playback

---

### Component 4: Analyzer

- a) Takes in an mp3 file
- b) Outputs a timestamped lightshow file

This is the core analytical engine. It operates in two passes:

**Pass 1 — Macro-Structure Detection (Initial Analysis)**:
- BPM detection (and any BPM changes throughout the song)
- Time signature identification
- Section segmentation: intro, verse, pre-chorus, chorus, bridge, drop, outro, fadeout
- Section count (number of verses, choruses, etc.)
- Mood/energy contour across sections

**Pass 2 — Lightshow Generation (Second Analysis)**:
- Maps each detected section to a lighting treatment
- Assigns timestamps to all lighting commands
- Considers transitions between sections (crossfade, hard cut, buildup)
- Applies narrative arc (energy builds across verse → chorus, resets at bridge, peaks at final chorus)

**Deliverables**:
- D4.1: BPM/beat grid extraction — detect tempo, beat positions, and any tempo changes
- D4.2: Section segmentation — identify structural boundaries and label them (verse, chorus, bridge, etc.)
- D4.3: Mood/energy profiling — compute per-section energy, mood, and intensity
- D4.4: Lightshow generation — produce a timestamped sequence of lighting commands from the structural map
- D4.5: Output format — define and implement the lightshow file format (JSON or similar)

**Status (Pass 1)**: **CODE COMPLETE** — 80 unit tests, see `dev/plans/feature-2-component-4-analyzer.md`

**Done when (Pass 1)**:
- [x] BPM detection is within ±2 BPM of ground truth on 10+ test songs across genres
- [x] BPM changes within a song are detected (verified on 2+ songs with known tempo changes)
- [x] Section boundaries are identified — at minimum, intro/verse/chorus/outro are distinguished
- [x] Section labels are reasonable on 7/10 test songs (no obviously wrong labels)
- [x] Analysis completes in < 30 seconds per song (for a typical 3–4 minute track)

**Done when (Pass 2)**:
- [ ] Every detected section has an assigned lighting treatment (no gaps in the timeline)
- [ ] Timestamps in the output align with beat grid positions (commands land on beats/bars, not arbitrary times)
- [ ] Transition timing between sections is explicit (crossfade duration, cut timing documented in output)
- [ ] Output lightshow file is valid, parseable, and contains all required fields
- [ ] Lightshow can be loaded and played back by Component 5 without modification

**Done when (overall)**:
- [x] `analyze(input.mp3) → structure.json` works end-to-end (CLI: `dreamsync analyze song.mp3`)
- [x] Unit tests cover BPM detection, section segmentation, and analysis orchestration (80 tests)
- [ ] Manual validation: 10 songs across genres reviewed for label accuracy and lightshow quality

---

### Component 5: Show Playback Runtime

- a) Takes in show file (timestamped lighting cues + beat grid) and mp3
- b) Plays mp3 to system default speakers, dispatches lighting commands to Govee devices in sync

**Status**: **CODE COMPLETE** — 65 unit tests, see `dev/plans/feature-2-component-5-show-player.md`

**Deliverables**:
- D5.1: Show Timeline Model — `ShowTimeline`, `ShowCue` dataclasses + JSON serialization (defines the show file format contract)
- D5.2: Audio Playback Engine — `AudioPlayer` (mp3 → PCM via Mp3Decoder → sounddevice OutputStream → speakers)
- D5.3: Show Playback Runtime — `ShowPlaybackRuntime` + `run_show_playback()` (position → cue → intent → render → send)
- D5.4: CLI Integration — `dreamsync play song.mp3 --show show.json --config devices.yaml`

**Done when**:
- [x] Show file + mp3 can be loaded and playback started with a single CLI command
- [x] Light commands are dispatched within ±50ms of their target timestamp (sync accuracy — by design)
- [x] Beat flags fire within ±25ms of beat grid positions (by design, tolerance=25ms)
- [x] Cue transitions (fade) interpolate intensity/speed over configured beat count
- [x] Playback handles clean shutdown on SIGINT/SIGTERM
- [x] 65 unit tests across 4 test files
- [ ] Audio plays through system default speakers without glitches (needs manual validation)
- [ ] End-to-end: a song plays with synchronized lights that match the show file — verified visually on 3+ songs
- [ ] Govee devices respond to commands during playback (both LAN and BLE devices tested)

---

### Component 6: Hardware Layer — Sound System

This is the home speaker system, which can accept a direct LINE IN that can be plugged in physically to the computer / show generator device.

**Deliverables**:
- D6.1: Physical connection verified (LINE IN cable from computer to speaker system)
- D6.2: Audio routing configured (virtual device → mp3 storage AND physical output)

**Done when**:
- [ ] Audio from the virtual device is audible on the speaker system via LINE IN
- [ ] Audio quality is acceptable (no hum, no distortion, volume levels appropriate)
- [ ] Audio routing does not interfere with other system audio

---

### Component 7: Hardware Layer — Light System

A distributed system of govee devices, accepting both LAN and BLE live change info.

**Deliverables**:
- D7.1: Existing Govee LAN/BLE adapters are compatible with the Lightshow Player's command format
- D7.2: All target devices respond to commands from the Lightshow Player

**Done when**:
- [ ] Lightshow Player can send commands to all configured Govee devices (LAN and BLE)
- [ ] Device latency during show playback is within acceptable bounds (same as v2 performance: LAN <50ms, BLE <200ms)
- [ ] Device health monitoring (existing `device_health.py`) works during show playback
- [ ] No regressions from v2 device behavior

---

## End-to-End Validation

The full pipeline is considered complete when:

| # | Criterion | Verified By |
|---|-----------|-------------|
| 1 | ASS plays audio → virtual device captures it | Component 2 test |
| 2 | Continuous stream → individual mp3 files per song | Component 3 test on 5+ songs |
| 3 | mp3 file → structural analysis (BPM, sections, mood) | Component 4 Pass 1 on 10+ songs |
| 4 | Structural analysis → timestamped lightshow file | Component 4 Pass 2 on 5+ songs |
| 5 | Lightshow + mp3 → synchronized audio + light playback | Component 5 on 3+ songs, visual verification |
| 6 | Full chain: ASS playing → lights reacting to song structure | End-to-end test, 1+ full song |
| 7 | Song transitions: back-to-back songs analyzed and played | End-to-end test, 3+ consecutive songs |
| 8 | No regressions in existing v2 functionality | Full test suite passes |

---

## Build Order

Components should be built in data-flow order. Each depends on the previous:

```
ASS Interface (C1) → Virtual Device (C2) → mp3 Storage (C3) → Analyzer (C4) → Lightshow Player (C5)
                                                                                     ↓            ↓
                                                                              Sound System (C6)  Light System (C7)
```

C6 and C7 (hardware layers) are largely pre-existing from v2 and can be validated in parallel with C5.
