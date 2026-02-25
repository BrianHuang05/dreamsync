# Next Steps: Live Validation

All bug fixes (Bugs 1-3) are implemented and pass automated testing (352 tests, 0 failures).
What remains is live validation in a real bar environment.

---

## Combined Live Validation Protocol

Bugs 1-3 interact: fewer false boundaries (Bug 1) means fewer BPM resets (Bug 2)
and fewer mood flashes (Bug 3). A single test session validates all three.

### Test procedure

1. Start a session with `--debug-mood` enabled
2. Play a known playlist with 3-4 distinct tracks (varied tempos)
3. Run for 10-15 minutes
4. Collect the debug log output
5. Run `python scripts/boundary_detect.py <logfile>` to analyze boundaries

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
- **Missed real boundaries**: Lower `min_song_seconds` to 90 or `silence_threshold_rms` to 0.010

---

## Other Hardware Testing

The following features are unit tested but not yet validated on hardware:

1. **Auto-detect role classification** — Run `dreamsync session --config devices.yaml` and verify:
   - LAN devices always assigned `realtime`
   - BLE devices classified by measured latency: `realtime` (<20ms), `follower` (20-200ms), `slow` (>200ms)
   - Unreachable devices logged and skipped

2. **Infinite session mode** — Run `dreamsync session --config devices.yaml --debug-mood` for 10+ minutes and verify:
   - Ctrl+C cleanly shuts down all devices
   - No memory growth or degraded performance over time
   - Song boundaries fire and state resets work across multiple songs

---

## Future Work

- **Crossfade-aware boundaries**: Some players crossfade tracks (audio never hits silence). Could detect BPM discontinuities or spectral centroid jumps as an alternative trigger.
- **Per-song telemetry**: Log BPM/mood/energy stats per song (between boundaries) for post-session analysis.
- **Device config hot-reload**: Watch `devices.yaml` for changes and add/remove devices without restarting the session.
