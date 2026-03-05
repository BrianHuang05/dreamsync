# Phase 1: Audio Routing & Environment Setup

## Goal

Install VB-Audio Virtual Cable and configure Windows audio routing so that a target application's audio flows through the virtual cable, ready for capture.

## Prerequisites

- Windows 11 machine
- Administrator access for driver installation
- Target audio application installed (e.g., Spotify, browser)

## Deliverables

| ID  | Deliverable                        | Plan                                          |
|-----|------------------------------------|-----------------------------------------------|
| 1.1 | Install VB-Audio Virtual Cable     | [p1.1-install-vb-cable.md](phase1/p1.1-install-vb-cable.md) |
| 1.2 | Configure Application Audio Routing | [p1.2-configure-audio-routing.md](phase1/p1.2-configure-audio-routing.md) |
| 1.3 | Verify Audio Signal Path           | [p1.3-verify-audio-signal.md](phase1/p1.3-verify-audio-signal.md) |

## Deliverable Dependencies

```
1.1 Install VB Cable
  |
  v
1.2 Configure Routing
  |
  v
1.3 Verify Signal
```

## Tests

- [x] After 1.1: "CABLE Input" appears in Playback devices, "CABLE Output" appears in Recording devices
- [x] After 1.2: Programmatic audio routing via `audio_router.ps1 start/stop` toggles default output to CABLE Input
- [x] After 1.3: 440 Hz test tone verified flowing through cable (RMS=9993, Peak=32763)

## Completion Criteria

1. VB-Audio Virtual Cable driver installed and system rebooted
2. Both CABLE Input (playback) and CABLE Output (recording) devices visible in Windows Sound settings
3. Target application audio routed to CABLE Input
4. CABLE Output recording device shows real-time audio activity when target application plays
