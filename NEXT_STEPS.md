# Next Steps — Govee LAN Direct Control

## Completed
- [x] Commit razer protocol fix (correct header format, activation/deactivation packets)

## Up Next
- [ ] Per-device transport selection — extend `--device` spec to `IP:SEGMENTS:ROLE:TRANSPORT` so H612F can use ptreal (7 IC segments) while H808A uses razer (25 per-LED)
- [ ] Multi-device live test — run both devices simultaneously with `govee-live` and music
- [ ] Merge `govee-lan-direct` branch to `main`
- [ ] Effect tuning — adjust scroll fade rates and injection widths for 25-segment strips
- [ ] Auto-discovery integration — wire `govee-scan` into device config to eliminate manual IP entry
