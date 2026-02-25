# Bug 1 Investigation: Song Boundary Detector Over-Triggers

## Summary

The `SongBoundaryDetector` in `src/dreamsync/live.py` (line 335) fires 16 song boundaries in a 600-second (10-minute) session — one every ~37 seconds on average. A real bar playlist should produce 3-5 boundaries at most (songs are typically 3-5 minutes long). The over-triggering causes cascading problems: every false boundary resets BPM, mood, and effect state, producing jarring visual glitches (Bug 3) and compounding BPM lock-in delays (Bug 2).

**Test data sources:**
- `long_bar_test.txt` / `out/10mintest.txt` — 600s session, `min_song_seconds=30.0` (old), 16 boundaries
- `bar_test_post_fix.txt` — 300s session, `min_song_seconds=45.0` (current), 5 boundaries

---

## 1. Per-Boundary Timing Analysis (long_bar_test.txt, 600s session)

Boundaries #1-#4 occurred before the debug log's `t=160.0s` start (the session started at t=0 but the log file only captures from ~t=160s). Boundaries #5-#16 are visible in the log. The `govee t=` telemetry lines adjacent to each boundary give the approximate wall-clock time.

| Boundary | Approx. Time (s) | Delta from Previous (s) | Energy Before | Mood Before |
|----------|------------------|------------------------|---------------|-------------|
| #5       | ~195-196         | —                      | 0.41          | chill       |
| #6       | ~226-227         | ~31                    | 0.28          | chill       |
| #7       | ~261-262         | ~35                    | 0.35          | chill       |
| #8       | ~299-300         | ~38                    | 0.26          | chill       |
| #9       | ~334             | ~35                    | 0.46          | groove      |
| #10      | ~377             | ~43                    | 0.30          | chill       |
| #11      | ~410             | ~33                    | 0.64          | chill       |
| #12      | ~443-444         | ~33                    | 0.41          | groove      |
| #13      | ~475             | ~31                    | 0.44          | chill       |
| #14      | ~506             | ~31                    | 0.33          | chill       |
| #15      | ~536-537         | ~31                    | 0.75          | groove      |
| #16      | ~569             | ~32                    | 0.80          | hype        |

**Key observation:** Every single inter-boundary gap is 31-43 seconds, tightly clustered around the `min_song_seconds=30.0` floor. This is the smoking gun: the detector fires at the *minimum* allowed interval, not at actual song transitions.

**Statistics on inter-boundary deltas (boundaries #5-#16):**
- Min: 31s
- Max: 43s
- Mean: 33.9s
- Median: 32.5s
- Std: 3.8s

The tight clustering around 31-35 seconds means the silence condition is met almost continuously, and the only thing gating boundary detection is the `min_song_frames` floor.

---

## 2. Post-Fix Comparison (bar_test_post_fix.txt, 300s session, min_song_seconds=45.0)

| Boundary | Approx. Time (s) | Delta from Previous (s) | Energy Before | Mood Before |
|----------|------------------|------------------------|---------------|-------------|
| #1       | ~39              | —                      | 0.28          | groove      |
| #2       | ~71              | ~32                    | 0.16          | drop        |
| #3       | ~123-124         | ~53                    | 0.17          | groove      |
| #4       | ~245-246         | ~122                   | 0.24          | chill       |
| #5       | ~300 (end)       | ~55                    | 0.10          | chill       |

**Observation:** Raising `min_song_seconds` to 45.0 reduced boundaries from 16/600s to 5/300s, but the rate is still too high — 5 boundaries in 5 minutes is still one per minute. Boundaries #1-#2 are only 32 seconds apart (violating the 45s floor — this may indicate boundary #1 triggered very close to a session-start silence). The gap between #3 and #4 (122s) looks like a real song transition. The others are likely still false positives.

---

## 3. Director Energy Analysis Near Boundaries

The `energy=` values in the debug log are the Director's **normalized composite energy** (not raw RMS). Raw RMS is not logged, but the normalized energy still tells us what the audio character was like before each boundary.

**Pre-boundary energy patterns (long_bar_test, 10-15 lines before each boundary):**

| Boundary | Energy Range (10 lines before) | Pattern |
|----------|-------------------------------|---------|
| #5       | 0.30 - 0.41                   | Steady moderate — music playing, not silent |
| #6       | 0.21 - 0.39                   | Low-moderate — soft passage or breakdown |
| #7       | 0.25 - 0.46                   | Low-moderate — soft passage |
| #8       | 0.20 - 0.30                   | Low — soft section, but NOT silence |
| #9       | 0.22 - 0.46                   | Low-moderate — music still playing |
| #10      | 0.25 - 0.70                   | Wide range — brief loud burst in soft section |
| #11      | 0.31 - 0.64                   | Moderate — music clearly playing |
| #12      | 0.30 - 0.42                   | Moderate — music clearly playing |
| #13      | 0.17 - 0.60                   | Wide range — soft but not silent |
| #14      | 0.27 - 0.39                   | Moderate — music playing |
| #15      | 0.29 - 0.75                   | Moderate-high — music CLEARLY playing |
| #16      | 0.33 - 0.80                   | High — LOUD music right before boundary |

**Critical finding:** In many cases, the Director energy is 0.3-0.8 right before the boundary fires. This means the audio is clearly audible music, not silence. The raw RMS briefly dips below `silence_threshold_rms=0.005` for ~0.8 seconds (about 69 frames at 44100/512) during musical passages — this is enough to arm the silence detector. Then as soon as a single frame comes back above the threshold, the boundary fires.

Boundary #16 is particularly egregious: energy is 0.80 (HYPE mood) one frame before the boundary fires. This means a sub-second RMS dip occurred during an otherwise loud, energetic passage.

---

## 4. Root Cause Analysis

### Root Cause 1: `silence_threshold_rms=0.005` is far too low
**Code reference:** `src/dreamsync/live.py:340`

The threshold of 0.005 RMS is essentially near digital silence (~-46 dBFS). At a bar with system audio loopback, normal musical content can momentarily dip below this threshold during:
- Vocal-only passages (low RMS between drum hits)
- Synth pad transitions
- Brief pauses between notes/phrases
- DJ crossfade zones
- Breakdowns and buildups before drops

These are **not** song boundaries — they are normal musical dynamics. The threshold should be significantly higher to only trigger on actual gaps between tracks.

### Root Cause 2: `min_silence_seconds=0.8` is too short
**Code reference:** `src/dreamsync/live.py:341`

Real track gaps in DJ sets are typically 1-3 seconds. Spotify/streaming crossfades are 0-5 seconds. Musical breakdowns and quiet passages can easily have 0.8-second spans below the RMS threshold. At hop_size=512, sample_rate=44100:

```
min_silence_frames = int(0.8 * 44100 / 512) = 68 frames
```

68 frames is easily met during a musical breakdown. A real track gap should require at least 1.5-2.5 seconds of near-silence.

### Root Cause 3: `min_song_seconds=45.0` is still too short
**Code reference:** `src/dreamsync/live.py:342`

Even after raising from 30.0 to 45.0, the min_song_seconds floor is the dominant gating factor. The data shows boundaries fire at almost exactly the floor interval. Real songs are 3-5 minutes (180-300 seconds). A floor of 90-120 seconds would still catch short songs while filtering out most false detections.

The inter-boundary delta distribution proves this:
- With `min_song_seconds=30`: all deltas cluster at 31-43s
- With `min_song_seconds=45`: most deltas cluster at 32-55s

The silence condition is met so frequently that the only thing preventing *continuous* boundary firing is the song-length floor.

### Root Cause 4: Silence detection logic allows brief dips to arm the trigger
**Code reference:** `src/dreamsync/live.py:353-369`

The `update()` method increments `_silent_frames` whenever `rms < silence_threshold_rms`, but resets `_silent_frames` to 0 when a non-silent frame arrives. The boundary fires on the first non-silent frame *after* enough silent frames have accumulated. Critically, the silent frames do NOT need to be contiguous — but actually looking at the code more carefully, `_silent_frames` IS reset to 0 in the `else` branch (line 368), so it does require contiguous silence.

However, the issue is that 0.8 seconds of contiguous silence at RMS < 0.005 DOES occur during normal music. A kick drum pattern at 128 BPM has inter-onset intervals of ~0.47 seconds. If the RMS threshold is low enough, the spaces between kicks in a breakdown can accumulate 0.8s of "silence."

### Root Cause 5: No cooldown timer after boundary detection
**Code reference:** `src/dreamsync/live.py:363-366`

After a boundary fires, `_frames_since_reset` is set to 0 and the cycle restarts immediately. There is no explicit cooldown beyond the `min_song_frames` floor. A dedicated cooldown (e.g., 120s wall-clock) independent of frame counting would provide a hard lower bound.

---

## 5. Quantitative Impact

| Metric | Actual | Expected | Factor |
|--------|--------|----------|--------|
| Boundaries in 600s | 16 | 3-5 | 3-5x over |
| Avg inter-boundary gap | ~37s | 180-300s | 5-8x too short |
| Min inter-boundary gap | ~31s | 180s | 6x too short |
| BPM resets in 600s | 16 | 3-5 | 3-5x over |
| Hype flashes from Bug 3 | 16 | 3-5 | 3-5x over |

Each false boundary triggers:
1. BPM reset to 0.0 (takes 4-51 seconds to re-lock per Bug 2)
2. Mood reset to CHILL, then immediate false HYPE flash (Bug 3)
3. Effect cycler reset — visual jarring
4. Director energy normalization reset — uncalibrated for ~8 seconds

---

## 6. Proposed Fix Strategy

### Immediate (high impact, low risk):

1. **Raise `min_song_seconds` to 120.0** (line 342)
   - This alone would reduce the 600s test from 16 boundaries to ~5
   - Conservative: real songs are 180-300s, so 120s still catches short tracks

2. **Raise `silence_threshold_rms` from 0.005 to 0.015** (line 340)
   - 0.005 is near digital silence; 0.015 still catches real track gaps
   - Needs testing: too high (>0.03) and it won't detect gaps in quiet venues

3. **Raise `min_silence_seconds` from 0.8 to 2.0** (line 341)
   - Real track gaps are 1-3 seconds; musical breakdowns rarely sustain below-threshold RMS for 2+ seconds
   - This filters out the brief dips that arm the current detector

### Additional (medium impact):

4. **Add explicit cooldown timer** — After any boundary detection, enforce a minimum 90-second wall-clock cooldown regardless of silence/frame counts. This provides a hard floor that cannot be gamed by the audio signal.

5. **Use percentile-based silence detection** — Instead of a fixed RMS threshold, track the running 5th percentile of RMS and trigger only when current RMS drops well below it. This adapts to venue noise floor automatically.

6. **Require sustained post-silence energy** — Instead of triggering on the first non-silent frame, require N consecutive above-threshold frames before declaring a boundary. This prevents single-frame noise spikes from triggering false boundaries after a brief quiet moment.

---

## 7. File References

| File | Lines | Description |
|------|-------|-------------|
| `src/dreamsync/live.py` | 335-370 | `SongBoundaryDetector` class — all detection logic |
| `src/dreamsync/live.py` | 537-539 | Instantiation in `run_live_to_govee()` (uses defaults) |
| `src/dreamsync/live.py` | 602-618 | Boundary handling in main loop (triggers resets) |
| `tests/test_song_boundary.py` | 1-91 | Unit tests for detector (tests use `min_song_seconds=30.0`) |
| `long_bar_test.txt` | full | 600s test data, `min_song_seconds=30.0`, 16 boundaries |
| `bar_test_post_fix.txt` | full | 300s test data, `min_song_seconds=45.0`, 5 boundaries |
