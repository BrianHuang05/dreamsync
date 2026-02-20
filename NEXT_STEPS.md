# Next Steps — Govee LAN Direct Control

## Completed
- [x] Commit razer protocol fix — `c09858b` (correct header format, activation/deactivation packets)
- [x] Per-device transport selection — `--device` spec supports `IP:SEGMENTS:ROLE:TRANSPORT` so H612F can use ptreal (7 IC segments) while H808A uses razer (25 per-LED)
- [x] Multi-device live test — run both devices simultaneously with `govee-live` and music

## Up Next
- [ ] Merge `govee-lan-direct` branch to `main`
- [ ] Effect tuning — adjust scroll fade rates and injection widths for 25-segment strips
- [ ] Auto-discovery integration — wire `govee-scan` into device config to eliminate manual IP entry
