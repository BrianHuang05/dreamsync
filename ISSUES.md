# DreamSync — Known Issues

Identified from 10-minute dry-run test at a bar (2026-02-24), playing live audio through system loopback with `--debug-mood` and a fake device IP.

Test output: `out/10mintest.txt` (600s session, 16 boundaries detected)

---

## Bug 1: Song boundary detector over-triggers (low priority, blocked on Bug 2)

**Symptom**: 16 boundaries in 10 minutes (one every ~37 seconds). Real playlist should produce 3-5 at most. Every gap between consecutive boundaries is 31-43 seconds — just above the `min_song_seconds=30.0` floor.

**Root cause**: Quiet musical passages (breakdowns, soft sections, ambient transitions) drop below `silence_threshold_rms=0.005` for long enough to satisfy `min_silence_seconds=0.8`, and the 30-second song floor is too short to filter them out. Bar ambient noise may also contribute — the mic captures silence between system audio tracks but also room noise, making the threshold boundary fuzzy.

**Location**: `SongBoundaryDetector` in `src/dreamsync/live.py`

**Proposed fix** (after Bug 2 is resolved):
- Raise `min_song_seconds` from 30.0 to 90-120 seconds
- Consider raising `silence_threshold_rms` from 0.005 to 0.01-0.02 (needs testing — too high and it won't detect real track gaps)
- Alternatively, add an explicit cooldown timer independent of frame counting

---

## Bug 2: BPM lock-in time grows progressively across session (high priority)

**Symptom**: After each song boundary reset, the time for BPM to lock onto a non-zero value increases monotonically:
- Boundary #5: ~4 seconds to lock
- Boundary #8: ~16 seconds
- Boundary #12: ~33 seconds
- Boundary #15: ~51 seconds

By the end of the session, BPM takes nearly a minute to establish after a reset, making the system unresponsive.

**Root cause (suspected)**: `stream_t` advances continuously through the session and is never reset at song boundaries. `LiveBpmEstimator.reset()` sets `last_update_t = -1e9`, but `stream_t` (which is passed as the `t` parameter to `bpm_estimator.update()`) is at 300+s by mid-session. Some interaction between the advancing wall time and the reset state likely causes the estimator's onset buffer to need more frames before triggering a BPM update, or the `min_update_interval` / `min_frames` checks interact poorly with the large `t` values after reset.

**Location**: `LiveBpmEstimator.update()` in `src/dreamsync/live.py`, and the `stream_t` variable in `run_live_to_govee()`.

**Key state to investigate**:
- `stream_t` is not reset at boundaries — should it be? Or should the estimator track its own internal time?
- `onset_env` is cleared but `min_frames` still requires ~3 seconds of data before any BPM estimate — this is expected. The question is why it takes 4s at boundary #5 but 51s at boundary #15.
- Check whether `_detect_beats_adaptive()` or `_estimate_bpm()` behaves differently with high `t` values or with many prior resets.

---

## Bug 3: Mood resets to HYPE after song boundary (minor)

**Symptom**: After every boundary reset, the first few frames classify as `mood=hype` with high energy (0.6-0.8) before settling down. Combined with over-triggering (Bug 1), this causes constant jarring hype flashes.

**Root cause**: `MoodClassifier.reset()` sets `mood = Mood.CHILL`, which is correct. However, `Director.reset()` clears the EMA accumulators and normalization state (`_rms_floor=0.0`, `_rms_ceil=0.001`). The first non-silent audio frame after reset produces a very high normalized energy (because the floor/ceiling haven't calibrated yet), which the mood classifier interprets as HYPE.

**Location**: `Director._compute_energy()` and `Director._update_ema()` in `src/dreamsync/director.py`

**Proposed fix**: After reset, the Director's energy normalization needs a warmup period. Options:
- Seed `_rms_floor` / `_rms_ceil` with reasonable defaults instead of 0.0/0.001
- Use the existing `warmup_seconds` mechanism (Director already has an 8-second warmup path in `update()`) — but reset needs to re-trigger it by resetting `_last_t` (which it does) and ensuring `stream_t` or internal time is handled correctly
- The warmup path returns AMBIENT mode, which is the correct default behavior

**Note**: BPM dropping to 0.0 after reset is expected and fine — AMBIENT mode doesn't depend on BPM.
