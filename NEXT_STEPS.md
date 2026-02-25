# Next Steps

## Completed

### Song Boundary Detection — Live Validated (2/25)

Bug fixes 1-3 confirmed working in a 15-minute bar test (bar-test-3, 5 boundaries):

| Metric | Target | Actual | Status |
|---|---|---|---|
| Boundaries per 900s | 3-6 | 5 | Pass |
| Inter-boundary delta | >120s | 122-189s | Pass |
| BPM after boundary | 0.0 | 0.0 (all 5) | Pass |
| First mood after boundary | CHILL | CHILL (all 5) | Pass |
| Effect after boundary | CHILL pool | All CHILL pool, varied | Pass |
| Palette after boundary | CHILL pool | All CHILL pool, varied | Pass |
| Stability after boundary | Low | 0.014-0.240 | Pass |

---

## Hardware Testing (requires lights)

1. **Auto-detect role classification** — Run `dreamsync session --config devices.yaml` and verify:
   - LAN devices always assigned `realtime`
   - BLE devices classified by measured latency: `realtime` (<20ms), `follower` (20-200ms), `slow` (>200ms)
   - Unreachable devices logged and skipped

2. **Infinite session mode** — Run `dreamsync session --config devices.yaml --debug-mood` for 10+ minutes and verify:
   - Ctrl+C cleanly shuts down all devices
   - No memory growth or degraded performance over time
   - Song boundaries fire and state resets work across multiple songs

---

## Software Tasks (no lights needed)

1. **Per-song telemetry** — Log BPM/mood/energy stats per song (between boundaries) for post-session analysis. Can be developed and tested with `--debug-mood` dry runs against a fake IP.

2. **Crossfade-aware boundaries** — Some players crossfade tracks (audio never hits silence). Could detect BPM discontinuities or spectral centroid jumps as an alternative trigger. Can be prototyped and unit tested without hardware.

3. **Device config hot-reload** — Watch `devices.yaml` for changes and add/remove devices without restarting the session. Core file-watching logic can be built and tested without hardware.
