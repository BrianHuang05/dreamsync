# Next Steps

## Active — Validation Tests 11–13 (no hardware needed)

### What to do

Run a single 30-minute live test with music playing through system audio. This covers three validation tests simultaneously:

- **Test 11: Song boundary detection** — boundaries fire at actual song transitions, not mid-song
- **Test 12: Mood & effect cycling** — mood state machine visits multiple states, effects cycle
- **Test 13: Resource stability** — no memory leaks or hangs over 30 minutes

No Govee hardware needed — UDP frames silently drop on unreachable IPs. The full audio pipeline runs normally.

### How to run

**Step 1:** Queue a 30+ minute playlist with 6–8 distinct songs. Mix tempos (90 BPM chill → 128 house → 170 DnB). Include at least one quiet-intro song and one crossfade if possible. Note the number of song transitions (songs − 1).

**Step 2:** Start the 30-min run:

```bash
mkdir -p out/longrun
python -m dreamsync govee-live \
  --device 10.126.166.180:7:primary:ptreal \
  --duration 1800 \
  --debug-mood \
  --crossfade-detect \
  --telemetry-dir out/longrun/boundary-30m \
  2>&1 | tee out/longrun/boundary-30m-console.log
```

**Step 3 (optional, separate terminal):** Monitor memory:

```bash
while true; do
  echo "$(date +%H:%M:%S) $(ps aux | grep 'dreamsync govee-live' | grep -v grep | awk '{print "RSS=" $6 "KB"}')" \
    >> out/longrun/memory-30m.log
  sleep 30
done
```

### How to analyze

After the run completes (or Ctrl+C), run these analysis commands:

**Song boundaries (test 11):**

```bash
# Count boundary events
grep -c "Song boundary" out/longrun/boundary-30m-console.log

# Show timestamps
grep "Song boundary" out/longrun/boundary-30m-console.log

# Count telemetry song files (each boundary starts a new file)
ls out/longrun/boundary-30m/session-*/song-*.jsonl | wc -l
```

**Mood & effects (test 12):**

```bash
python -c "
import json, glob
from collections import Counter
moods = Counter()
effects = Counter()
total = 0
for f in sorted(glob.glob('out/longrun/boundary-30m/session-*/song-*.jsonl')):
    for line in open(f):
        row = json.loads(line)
        m = row.get('mood')
        e = row.get('effect')
        if m:
            moods[m] += 1
            total += 1
        if e:
            effects[e] += 1
print(f'Total frames: {total}')
print()
print('Mood distribution:')
for mood, count in moods.most_common():
    print(f'  {mood}: {count} ({100*count/total:.1f}%)')
print()
print(f'Unique effects seen: {len(effects)}')
for effect, count in effects.most_common(10):
    print(f'  {effect}: {count}')
"
```

**Resource stability (test 13):**

```bash
cat out/longrun/memory-30m.log
```

(On Windows the `ps aux` pattern may not work — use Task Manager or `Get-Process` in PowerShell instead.)

### Pass criteria

**Test 11 — Song boundaries:**
- Boundary count matches actual song transitions (±1 for crossfades)
- No false boundaries mid-song (especially during quiet breakdowns)
- Each `song-NNN.jsonl` file corresponds to roughly one song duration
- Crossfade boundaries (if any) logged as `[crossfade]` not `[silence]`

**Test 12 — Mood & effects:**
- At least 2 distinct mood states visited (ideally 3+ with varied music)
- No single mood > 90% of total frames (would indicate stuck state)
- At least 3 distinct effects cycled through
- Effect names change over time (not stuck on one effect)

**Test 13 — Resource stability:**
- RSS memory does not grow by more than 100 MB over the 30-minute run
- No `MemoryError` or `OSError` in the console log
- Process exits cleanly (no zombie threads)

### If something fails

| Problem | Where to look | What to change |
|---|---|---|
| Too many false boundaries | `SongBoundaryDetector` in `src/dreamsync/live.py:666–721` — raise `silence_threshold_rms` (default 0.015) or `min_song_frames` (default ~120s) | |
| No boundaries detected | Same class — lower `silence_threshold_rms`, check that music is actually playing through system audio loopback | |
| Crossfade boundaries missing | `CrossfadeBoundaryDetector` in `src/dreamsync/live.py:752–846` — lower vote `threshold` (default 0.50) or adjust signal weights in `CrossfadeConfig` (line 723) | |
| Mood stuck on CHILL | `MoodClassifier` in `src/dreamsync/mood.py:41–161` — lower `chill_energy_ceiling` (default 0.20). Check `--debug-mood` output for energy values | |
| Effects not cycling | `EffectCycler` in `src/dreamsync/effects.py:140–321` — check `cycle_interval` (default 16s). Mood pools defined at line 106 | |
| Memory leak | Check `deque` buffers in `LiveBpmEstimator` (`src/dreamsync/live.py`) — all should have `maxlen`. Check telemetry writer flushes per-song | |

### Key file reference

| Component | File | Key classes/lines |
|---|---|---|
| Song boundary (silence) | `src/dreamsync/live.py` | `SongBoundaryDetector` (line 666), `.update(rms)` returns bool |
| Song boundary (crossfade) | `src/dreamsync/live.py` | `CrossfadeBoundaryDetector` (line 752), `CrossfadeConfig` (line 723) |
| Mood state machine | `src/dreamsync/mood.py` | `MoodClassifier` (line 41), `MoodConfig` (line 14), `Mood` enum (line 7) |
| Effect cycling | `src/dreamsync/effects.py` | `EffectCycler` (line 140), `MOOD_EFFECTS` (line 106), `MOOD_PALETTES` (line 34) |
| Telemetry | `src/dreamsync/telemetry.py` | `SongTelemetryWriter` (line 12), `.write_frame()`, `.on_boundary()`, `.close()` |
| Main loop | `src/dreamsync/live.py` | `run_live_to_govee()` (line 1320), boundary check (line 1459), mood update (line 1555) |
| CLI flags | `src/dreamsync/cli.py` | `--crossfade-detect` (line 225), `--debug-mood`, `--telemetry-dir` |
| BPM analysis script | `scripts/analyze_bpm.py` | Per-song breakdown, 30s windowed view, harmonic ratios |
| Unit tests (boundaries) | `tests/test_song_boundary.py` | 14 tests for silence detector |
| Unit tests (crossfade) | `tests/test_crossfade_boundary.py` | 13 tests for crossfade detector |
| Unit tests (mood) | `tests/test_mood.py` | Mood transitions, DROP detection, hysteresis |
| Unit tests (effects) | `tests/test_effects.py` | Preset selection, timing, mood changes |
| Unit tests (telemetry) | `tests/test_telemetry.py` | JSONL writing, file rotation, summaries |

### Full validation progress

- [x] 1–6. Unit tests, scan, connectivity, auto-detect, profiles, hot-swap
- [ ] 7–9. Profile rotation, health monitor, offline/online — **requires hardware**
- [x] 10. BPM stability — **PASSED** (stdev 18.0, 0 unstable songs, 0% outliers)
- [ ] 11. Song boundary detection — **ready to run** (30 min, no hardware)
- [ ] 12. Mood & effect cycling — **shared with test 11**
- [ ] 13. Resource stability — **shared with test 11**

### After tests 11–13

- Hardware tests 7–9 when devices available
- Tuning pass (mood thresholds, effect weights)

---

## Mk I — Completed Features

| Feature | Date | Tests |
|---|---|---|
| Song boundary detection (silence-gap) | 2/25 (live validated) | 14 |
| Per-song telemetry | 2/25 | 11 |
| Crossfade-aware boundaries | 2/25 | 13 |
| Config file watcher (YAML edit → re-probe) | 2/26 | 21 |
| Noise-robust onset detection (Phase 1+2) | 2/26 | 15 |
| HPSS percussive onset (Phase 3a) | 2/26 | 10 |

## Mk II — Completed Features

| Feature | Date | Tests |
|---|---|---|
| Color profile system (YAML loader, validator, 8 built-ins) | 2/26 | 63 |
| Profile-aware EffectCycler (palette/effect/param overrides) | 2/26 | 8 |
| Profile CLI (`--profile`, `--auto-profile`, `profiles`, `profile-validate`) | 2/26 | — |
| Profile hot-reload (ProfileWatcher → `set_profile()`) | 2/26 | 7 |
| Profile rotation (`--profile-rotation`, timed swap) | 2/26 | 4 |
| Device health monitor (probe loop, offline/online, role reclass, discovery) | 2/26 | 31 |

See `plans/color-profiles.md` and `plans/device-health-monitor.md` for architecture details.

---

## Future — Pre-programmed Light Shows (separate project)

A different project entirely: instead of reacting to live audio, **compile** a deterministic light show from a known playlist ahead of time.

### How it differs from DreamSync

| | DreamSync (Mk I/II) | Show Compiler |
|---|---|---|
| Input | Live audio stream | Spotify playlist + offline audio files |
| Analysis | Real-time (~5ms budget) | Offline (unlimited time, full song context) |
| Decisions | Director makes mood/effect choices at runtime | All decisions made at compile time, optionally hand-tweaked |
| Output | Direct device control | Timeline file (JSON/binary) → player runtime |
| Runtime | Detection + rendering + output | Playback only (seek to timestamp, send frame) |

### Rough architecture

1. **Playlist import** — Spotify API to get track list, BPM, sections, audio features; or local audio analysis via librosa
2. **Offline analysis** — Run beat detection, mood classification, section segmentation on full tracks with lookahead (knows what's coming)
3. **Show compiler** — Map sections → effects + palettes (using Mk II profiles), generate a frame-by-frame timeline
4. **Editor** — Optional manual tweaking: move boundaries, override colors, add cues
5. **Player runtime** — Sync to Spotify playback position (or local audio), read timeline, send frames to devices

### What it reuses from DreamSync

- Effect renderers (`render.py`) — same SCROLL, PULSE, BREATHE, etc.
- Device output layer (`govee_lan.py`, `govee_ble.py`, `MultiGoveeLanAdapter`)
- Device config and auto-detect (`auto_detect.py`, `config_watcher.py`)
- Mood color profiles (Mk II)

### What it does NOT reuse

- Real-time audio capture and feature extraction
- Live BPM estimator (would use offline analysis instead)
- Director's runtime decision-making (replaced by the compiled timeline)
- Song boundary detection (known from playlist metadata)
