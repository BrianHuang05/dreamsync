# Next Steps: Bug Fixes Summary and Remaining Work

All code changes for Bugs 1, 2, and 3 have been implemented and pass automated testing
(352 tests, 0 failures). What remains is live validation in a real bar environment.

---

## Bug 1: Song Boundary Detector Over-Triggers

**Status**: All 5 fixes implemented in `src/dreamsync/live.py`

| Fix | Change | Impact |
|---|---|---|
| 1. Raise min_song_seconds | 45s → 120s | Primary gate; max ~5 boundaries per 600s |
| 2. Raise silence_threshold_rms | 0.005 → 0.015 | Filters micro-dips during audible music |
| 3. Raise min_silence_seconds | 0.8s → 2.0s | Requires 2s sustained silence |
| 4. Explicit cooldown timer | New 90s cooldown | Safety net against rapid re-triggering |
| 5. Sustained post-silence energy | 3-frame confirmation | Filters single noise spikes |

### Remaining work

- [ ] **Live bar test (10 min)**: Count boundaries. Target: 2-4 per 600s (was 16).
- [ ] **False positive check**: Confirm all boundaries correspond to real track gaps.
- [ ] **False negative check**: Play a known playlist, verify all real transitions detected.
- [ ] **Verify inter-boundary delta**: Target >120s mean (was ~34s).

---

## Bug 2: BPM Post-Lock Instability

**Status**: All 5 fixes implemented in prior commits (`537e8b8`, `596e1d5`)

| Fix | Change | Impact |
|---|---|---|
| 1. Extend min_frames | 3s → 5s | More reliable first estimate |
| 2. Widen analysis window | 12s → 20s | More data for autocorrelation |
| 3. Strengthen inertia | 4 → 6 confirms + proportional scaling | Filters 30-50 BPM jumps |
| 4. Confidence-weighted blend | Beat count scales IOI vs autocorrelation weight | Better signal fusion |
| 5. EMA smoothing | 0.7/0.3 blend for within-range changes | Dampens ±5 BPM oscillation |

### Remaining work

- [ ] **Re-test with all fixes active**: Prior Round 2 test was before Bug 1 boundary
      changes. Fewer false boundaries means fewer BPM resets, which should further
      improve stability.
- [ ] **Measure distinct BPM values per segment**: Target <5 (was 11.3 pre-fix, 7.8 after
      Round 1).
- [ ] **Measure max jump magnitude**: Target <15 BPM.
- [ ] **If still unstable**: Consider reducing `max_jump_bpm` from 6.0 to 4.0, or adding
      a cumulative drift rate limiter.

---

## Bug 3: Mood Resets to HYPE After Song Boundary

**Status**: All 3 fixes implemented in `src/dreamsync/director.py`, `src/dreamsync/mood.py`,
`src/dreamsync/live.py`

| Fix | Change | Impact |
|---|---|---|
| 1. Relative warmup guard | `_reset_t` tracks last reset; warmup is relative | Primary fix; forces AMBIENT for 8s after every reset |
| 2. Seed normalization defaults | Reset seeds: rms_floor=0.01, rms_ceil=0.10, flux_max=10.0, onset_max=0.05 | Prevents 0.80 energy spike on first frame |
| 3. Dwell time after reset | `MoodClassifier.reset(t)` sets proper dwell time | CHILL locked for 4s after every reset |

### Remaining work

- [ ] **Live bar test with `--debug-mood`**: Verify `mode=ambient` for 8s after each
      boundary.
- [ ] **Verify first-frame energy**: Should be <0.20 after boundary (was 0.50-0.81).
- [ ] **Verify mood stability**: No HYPE within 8s of any boundary. First mood after
      boundary should be CHILL (was HYPE 100% of the time).
- [ ] **Verify session startup unchanged**: First 8s of a new session should behave
      identically to before.

---

## Combined Live Validation Protocol

All three bugs interact: fewer false boundaries (Bug 1) means fewer BPM resets (Bug 2)
and fewer mood flashes (Bug 3). A single test session validates all three.

### Test procedure

1. Start a session with `--debug-mood` enabled
2. Play a known playlist with 3-4 distinct tracks (varied tempos)
3. Run for 10-15 minutes
4. Collect the debug log output

### Metrics to capture

| Metric | Bug | Target | Pre-fix |
|---|---|---|---|
| Boundaries per 600s | Bug 1 | 2-4 | 16 |
| Mean inter-boundary delta | Bug 1 | 150-300s | 34s |
| Distinct BPM values per segment | Bug 2 | <5 | 11.3 |
| Max single BPM jump | Bug 2 | <15 BPM | 30-50 BPM |
| First-frame energy after boundary | Bug 3 | <0.20 | 0.50-0.81 |
| First mood after boundary | Bug 3 | CHILL | HYPE (100%) |
| Seconds in CHILL after boundary | Bug 3 | >=8 | 0 |

### If issues remain

- **Too many boundaries**: Lower `silence_threshold_rms` or raise `min_silence_seconds`
- **BPM still jumpy**: Reduce `max_jump_bpm` to 4.0 or add drift rate limiter
- **Mood still flashes**: Increase `warmup_seconds` or `min_dwell_seconds`
- **Missed real boundaries**: Lower `min_song_seconds` to 90 or `silence_threshold_rms`
  to 0.010
