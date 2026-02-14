# Direct Govee LAN Control — Project Plan

## Why

LedFx audio-reactive effects own the full render loop — they listen to audio and decide what pixels to show. Dreamsync can only tweak their palette, not drive the visual. This defeats the purpose of dreamsync's beat detection, mood transitions, and musical-structure awareness. The fix: cut out LedFx entirely and send per-segment RGB frames directly to the Govee hardware over UDP.

## What we have today

| Component | Status |
|-----------|--------|
| Audio capture + feature extraction | Working (`live.py`, `dsp/`) |
| Beat detection + live BPM estimation | Working (`LiveBpmEstimator`) |
| Director state machine (ambient/pulse/motion/ripple) | Working (`director.py`) |
| Beat ripple controller (color cycling on beats) | Working (`basic_controller.py`) |
| Beat flash controller (brightness pulse on beats) | Working (`basic_controller.py`) |
| Device discovery (multicast scan) | Working (`scripts/pingsweeper.py`) |
| LedFx output adapter | Working but architecturally wrong — LedFx inserts its own audio processing |

## Govee LAN protocol summary

All commands are JSON over UDP.

| Port | Direction | Purpose |
|------|-----------|---------|
| 4001 | Client → multicast `239.255.255.250` | Device discovery |
| 4002 | Device → client | Responses (scan replies, status) |
| 4003 | Client → device IP | **All control commands** |

### Whole-strip commands (simple, one color)

```json
{"msg": {"cmd": "turn", "data": {"value": 1}}}
{"msg": {"cmd": "brightness", "data": {"value": 75}}}
{"msg": {"cmd": "colorwc", "data": {"color": {"r": 255, "g": 0, "b": 128}, "colorTemInKelvin": 0}}}
```

### Per-segment streaming (`razer` command)

This is the key capability. Sends N RGB triplets in a single packet, mapped to the device's physical segments. This is the same protocol LedFx uses internally to drive Govee strips.

```json
{"msg": {"cmd": "razer", "data": {"pt": "<base64_encoded_packet>"}}}
```

**Packet structure** (binary, then base64-encoded):

```
Byte 0:    0xBB           magic
Byte 1:    0x00           reserved
Byte 2:    variant        0xFA=dreamview, 0x0E=chroma, 0x20=govee
Byte 3:    0xB0           reserved
Byte 4:    stretch        0x00=discrete segments, 0x01=stretch/interpolate
Byte 5:    count          number of RGB triplets
Bytes 6+:  R,G,B, R,G,B, ...
Last byte: XOR checksum of all preceding bytes
```

- `stretch=0x01` interpolates between segment colors to fill the full strip smoothly — this is how you get a gradient/wave from a small number of segments.
- Practical frame rate: 30-40 FPS. No server-side throttle; limited by device processing speed.
- Activation: set brightness to 100 first, wait ~100ms, then start streaming.

### Device segment count

Each Govee model has a fixed number of addressable segments (e.g., TV backlights ~15, strip lights ~10-20). The segment count can be found in the Govee app under "Light Panel Layout." We'll need to make this configurable per-device.

## Architecture

```
Audio Input
    │
    ▼
LiveBpmEstimator ──► beat events + BPM
    │
    ▼
Controller (ripple/flash/director)
    │
    ▼
LightingIntent { mode, intensity, speed, bpm, color }
    │
    ▼
SegmentRenderer ──► frame buffer: [(R,G,B)] × N segments   ← NEW
    │
    ▼
GoveeLanAdapter ──► UDP razer packet to device              ← NEW
```

### What changes

| Layer | Before (LedFx) | After (Direct LAN) |
|-------|-----------------|---------------------|
| Rendering | LedFx effect (audio-reactive, uncontrollable) | `SegmentRenderer` — dreamsync owns every pixel |
| Transport | HTTP POST/PUT to LedFx REST API | UDP `razer` packet to device port 4003 |
| Frame rate | ~2-3 FPS (rate-limited API calls) | 30-40 FPS (UDP streaming) |
| Latency | ~50-300ms (HTTP round-trip) | ~1-5ms (UDP fire-and-forget) |
| Dependency | LedFx process must be running | None — direct to hardware |

## Implementation phases

### Phase 1: Govee LAN transport layer

**Goal:** Send arbitrary segment colors to a Govee device over UDP.

**Files:**

| File | What |
|------|------|
| `src/dreamsync/output/govee_lan.py` | `GoveeLanConfig`, `GoveeLanAdapter` — UDP socket, packet encoding, `razer` command builder, XOR checksum, base64 encoding |
| `src/dreamsync/output/discovery.py` | Refactor pingsweeper into importable module: `scan_devices() → list[GoveeDevice]` with IP, SKU, device ID |
| `tests/test_output_govee_lan.py` | Unit tests with a mock UDP socket — verify packet structure, checksum, base64 encoding, frame rate limiting |

**Key decisions:**
- Variant byte: start with `0xFA` (dreamview), make configurable if needed
- Stretch mode: `0x01` (interpolate) for smooth gradients from few segments
- Segment count: configurable via CLI `--segments N`, default 15
- Frame interval: 33ms (30 FPS), configurable

**Smoke test:**
```bash
python -m dreamsync govee-test --device-ip 192.168.x.x --segments 15 --color '#ff0000'
```
Send a solid red to all segments for 5 seconds, then turn off.

### Phase 2: Segment renderer

**Goal:** Convert `LightingIntent` into a frame of N RGB segment values.

**Files:**

| File | What |
|------|------|
| `src/dreamsync/render.py` | `SegmentRenderer` class — maintains a frame buffer, applies visual effects |
| `tests/test_render.py` | Unit tests for each render mode |

**Render modes:**

| Mode | Visual | How it works |
|------|--------|--------------|
| `solid` | Whole strip = one color | All segments set to `intent.color` at `intent.intensity` brightness |
| `pulse` | Flash on beat, fade between | All segments flash to `intent.color`, exponential decay to dim between beats |
| `scroll` | Color wave scrolling from center | New color injected at center on each beat, previous colors shift outward, fade with distance |
| `breathe` | Slow sine-wave brightness | All segments same color, brightness oscillates with BPM |

The `scroll` mode is the big win — this is what LedFx's scroll effect couldn't do under our control. Each beat pushes a new color into the center of the strip, and previous colors scroll outward and fade. Dreamsync owns the entire animation.

**Frame buffer mechanics for `scroll` mode:**
```
Beat 1 (red):    [ dim dim dim RED RED RED dim dim dim ]  (mirror from center)
Beat 2 (green):  [ dim dim RED GRN GRN GRN RED dim dim ]  (red shifts out)
Beat 3 (blue):   [ dim RED GRN BLU BLU BLU GRN RED dim ]  (older colors shift further)
```
- Each frame: shift all segments outward by `speed` pixels, inject new color at center
- Between beats: just shift + fade, no new color injection
- `intensity` controls overall brightness
- `speed` controls scroll rate (pixels per frame, derived from BPM)
- `mirror=true` means center-outward; `mirror=false` means left-to-right

### Phase 3: CLI integration

**Goal:** New CLI command that runs the full pipeline without LedFx.

**Files:**

| File | What |
|------|------|
| `src/dreamsync/cli.py` | New `govee-live` command, `govee-test` smoke test command |
| `src/dreamsync/live.py` | New `run_live_to_govee()` function — audio capture → controller → renderer → UDP |

**CLI:**
```bash
# Full live mode — audio → beats → scroll animation → Govee device
python -m dreamsync govee-live \
    --device-ip 192.168.1.23 \
    --segments 15 \
    --duration 120 \
    --render-mode scroll \
    --colors '#ff0000,#00ff00,#0000ff' \
    --brightness 0.8

# Smoke test — solid color for N seconds
python -m dreamsync govee-test \
    --device-ip 192.168.1.23 \
    --segments 15 \
    --color '#ff0000' \
    --duration 5

# Discovery
python -m dreamsync govee-scan
```

**Parameters:**
- `--device-ip` — target device (from `govee-scan`)
- `--segments` — number of addressable segments on the device
- `--render-mode` — `solid`, `pulse`, `scroll`, `breathe`
- `--fps` — frame rate (default 30)
- `--brightness` — global brightness 0-1
- `--colors` — comma-separated hex colors to cycle on beats
- `--mirror` — scroll from center outward (default true)

### Phase 4: Multi-device support

**Goal:** Drive multiple Govee devices simultaneously with roles.

Reuse the existing `DeviceRole` (primary/accent) and `transform_intent` infrastructure. Each device gets its own `GoveeLanAdapter` + `SegmentRenderer`.

```bash
python -m dreamsync govee-live \
    --device 192.168.1.23:15 \
    --device 192.168.1.24:10:accent \
    --duration 120
```

## What stays, what goes

| Component | Keep | Remove | Notes |
|-----------|------|--------|-------|
| `director.py` | Yes | | State machine still drives mode transitions |
| `basic_controller.py` | Yes | | Beat ripple + flash controllers still generate intents |
| `live.py` | Modify | | Add `run_live_to_govee()`, keep LedFx functions for backwards compat |
| `output/ledfx.py` | Keep | | Still works for anyone using LedFx; not on the critical path |
| `output/roles.py` | Yes | | Device roles reused for multi-device |
| `output/govee_lan.py` | | | **New** — UDP transport |
| `output/discovery.py` | | | **New** — device scanner |
| `render.py` | | | **New** — segment renderer |
| LedFx process dependency | | Remove from live workflow | No longer needed for the primary use case |

## Verification

### Phase 1 (transport)
```bash
python -m unittest tests.test_output_govee_lan -v
python -m dreamsync govee-test --device-ip <IP> --segments 15 --color '#ff0000'
```

### Phase 2 (renderer)
```bash
python -m unittest tests.test_render -v
```

### Phase 3 (integration)
```bash
python -m dreamsync govee-live --device-ip <IP> --segments 15 --duration 60 --render-mode scroll
```
Observe: beats change the scroll color, wave scrolls smoothly from center outward, no LedFx process running.

### Phase 4 (multi-device)
```bash
python -m dreamsync govee-live --device <IP1>:15 --device <IP2>:10:accent --duration 60
```
