# BLE-Only Govee Devices as Mood Followers: Research Report

## Deliverable 1: BLE Protocol Document

### GATT Identifiers

All documented Govee lighting products share the same GATT service and write characteristic UUIDs. This is confirmed across every community reverse-engineering source and validated against DreamSync's own fleet of 11 BLE devices.

| Identifier | UUID |
|---|---|
| **Service** | `00010203-0405-0607-0809-0a0b0c0d1910` |
| **Write Characteristic** | `00010203-0405-0607-0809-0a0b0c0d2b11` |
| **Notify/Read Characteristic** | `00010203-0405-0607-0809-0a0b0c0d2b10` |

No authentication or pairing is required. Any BLE client within range can control any Govee device.

### Packet Format

All commands use a fixed 20-byte packet structure:

```
Byte:  [0]    [1]     [2]      [3..18]        [19]
       IDENT  CMD     SUB      PAYLOAD+PAD    XOR_CHECKSUM
```

| Field | Size | Description |
|---|---|---|
| Identifier | 1 byte | `0x33` (commands), `0xAA` (keep-alive) |
| Command | 1 byte | `0x01`=power, `0x04`=brightness, `0x05`=color/mode |
| Sub-command | 1 byte | Varies by command type |
| Payload | 16 bytes | Command-specific data, zero-padded |
| Checksum | 1 byte | XOR of bytes 0-18 |

### Command Reference

#### Power On/Off (`33 01`)

```
ON:  33 01 01 [16 zero bytes] [XOR]
OFF: 33 01 00 [16 zero bytes] [XOR]
```

#### Brightness (`33 04`)

```
33 04 [0x00-0xFF] [16 zero bytes] [XOR]
```

Value maps: 0 = off, 255 = max. Percentage conversion: `raw = int(pct * 255 / 100)`.

#### Color Commands (`33 05 xx`)

Three protocol variants exist for different device families:

| Variant | Header | Devices | Description |
|---|---|---|---|
| Manual color | `33 05 02 RR GG BB` | H6001, H6127, H6159 | Whole-device single color |
| Bulb color | `33 05 0D RR GG BB` | H6006, H615B | Bulb-specific direct color |
| Segment color | `33 05 15 01 RR GG BB [pad] [7-byte bitmask]` | H617A, H612F, H6199 | Per-segment with bitmask |

#### Keep-Alive (`AA 01`)

```
AA 01 [17 zero bytes] [XOR]
```

Must be sent every ~2 seconds to prevent automatic disconnection during idle periods.

### Comparison to ptreal (BLE-over-LAN) Packets

The ptreal transport in `govee_lan.py` wraps the exact same 20-byte BLE packets in a JSON envelope for UDP transmission:

```python
# LAN: JSON-wrapped, base64-encoded
{"msg":{"cmd":"ptReal","data":{"command":["MwUVAf8AAAAAAAAAAAB/AA=="]}}}

# BLE: raw 20-byte packet written to GATT characteristic
b'\x33\x05\x15\x01\xff\x00\x00\x00\x00\x00\x00\x00\x00\x7f\x00...'
```

**The packets are byte-identical.** The existing `build_ptreal_power_packet()`, `build_ptreal_brightness_packet()`, and `build_ptreal_segment_packets()` functions from `govee_lan.py` are reused directly by the BLE adapter. This confirms the hypothesis that ptreal = "BLE-over-LAN."

Sources:
- [egold555/Govee-Reverse-Engineering](https://github.com/egold555/Govee-Reverse-Engineering)
- [BeauJBurroughs/Govee-H6127-Reverse-Engineering](https://github.com/BeauJBurroughs/Govee-H6127-Reverse-Engineering)
- [chvolkmann/govee_btled](https://github.com/chvolkmann/govee_btled)
- [Incipiens/Govee-H615B-Reverse-Engineered-Controls](https://github.com/Incipiens/Govee-H615B-Reverse-Engineered-Controls)

---

## Deliverable 2: Library Evaluation Report

### bleak — Primary Candidate

| Criterion | Assessment |
|---|---|
| **Windows 11 native** | Yes — uses WinRT backend via PyWinRT |
| **pip install** | `pip install bleak` — binary wheels available, no compiler needed |
| **Async** | asyncio-native; bridged to sync via daemon thread + queue |
| **Write latency** | 2.5-17ms per write-without-response (measured) |
| **Throughput** | Easily sustains 20+ writes/sec per device |
| **Concurrent connections** | Verified with 7 simultaneous BLE devices |
| **Active maintenance** | v2.1.1 released Dec 2025, ~2300 GitHub stars |
| **License** | MIT |

### Measured Latency (Real Hardware)

From `data/ble_latency_results.json` — 100 writes to H617A strip:

| Metric | Value |
|---|---|
| Min | 2.48 ms |
| Median | 4.10 ms |
| Average | 4.76 ms |
| P95 | 8.99 ms |
| Max | 16.96 ms |

### Measured Multi-Device Latency

From `data/ble_orchestrated_bench.json` — 7 BLE devices + 2 LAN devices, 30 rounds at 5 Hz:

| Device | Avg (ms) | Median (ms) | P95 (ms) | Max (ms) |
|---|---|---|---|---|
| H617A strip | 5.72 | 4.01 | 17.28 | 17.66 |
| H6006 bulb #1 | 7.32 | 4.56 | 17.38 | 36.80 |
| H6006 bulb #2 | 5.45 | 4.37 | 16.85 | 17.11 |
| H6006 bulb #3 | 5.85 | 4.41 | 15.04 | 16.66 |
| H6006 bulb #4 | 7.76 | 5.12 | 16.31 | 36.34 |
| H6006 bulb #5 | 6.42 | 4.23 | 15.92 | 16.11 |
| H6006 bulb #6 | 7.19 | 4.30 | 15.79 | 36.08 |
| LAN (UDP) | 0.23 | 0.21 | 0.35 | 0.57 |

**Verdict:** BLE write latency is ~20x higher than LAN UDP but still well under the 200ms threshold needed for mood following at 5 Hz.

### Alternatives

| Library | Windows | Async | Maintained | Verdict |
|---|---|---|---|---|
| **bleak** | Native | Yes | Yes | **Selected** |
| pygatt | BGAPI dongle only | No | Stale | Rejected — requires external hardware |
| bluepy | No (Linux only) | No | Stale | Rejected — no Windows support |
| pybluez | Partial | No | Stale | Rejected — difficult install, legacy |

---

## Deliverable 3: Architecture Proposal

### Existing Implementation

The BLE mood-follower system is fully implemented in the codebase:

```
src/dreamsync/output/govee_ble.py    — GoveeBleAdapter, MultiBleAdapter, packet builders
src/dreamsync/output/govee_lan.py    — MultiGoveeLanAdapter (accepts ble_followers param)
src/dreamsync/cli.py                 — --ble-device CLI flag, govee-ble-scan, govee-ble-test
tests/test_govee_ble.py              — 54 tests, all passing
scripts/ble_*.py                     — 12 prototype/diagnostic scripts
data/                                — Benchmark data from real hardware tests
```

### Architecture Design

```
                        ┌─────────────┐
                        │  Director   │
                        │ (mood/color │
                        │  /intent)   │
                        └──────┬──────┘
                               │ LightingIntent
                ┌──────────────┼──────────────┐
                │              │              │
        ┌───────▼──────┐ ┌────▼─────┐  ┌─────▼─────────┐
        │SegmentRenderer│ │SegmentR. │  │ GoveeBleAdapter│
        │   (H612F)    │ │  (H808A) │  │  (H6006 bulbs) │
        └───────┬──────┘ └────┬─────┘  └─────┬─────────┘
                │              │              │
        ┌───────▼──────┐ ┌────▼─────┐  ┌─────▼─────────┐
        │GoveeLanAdapter│ │GoveeLan  │  │ bleak GATT    │
        │  (UDP ptreal)│ │  (razer) │  │  write thread  │
        └──────────────┘ └──────────┘  └───────────────┘
```

**Key design decisions:**

1. **Simplified interface for BLE devices:** BLE mood followers receive `(color, brightness)` from `LightingIntent` — no `SegmentRenderer` needed. The `GoveeBleAdapter.emit()` method extracts color and intensity directly from the intent.

2. **Threading model:** Each BLE device runs in a dedicated daemon thread with its own asyncio event loop. The main thread pushes `(r, g, b, brightness)` tuples into a bounded `queue.Queue(maxsize=4)`. Drop-oldest semantics ensure the BLE thread always writes the latest color.

3. **Discovery:** BLE scanning via `scan_ble_devices()` uses `bleak.BleakScanner.discover()`. Separate from LAN UDP multicast discovery. Manual device addressing via `--ble-device` CLI flag.

4. **Connection management:** Persistent connections with automatic reconnection. Capped exponential backoff (max 5s). Keep-alive packets (`AA 01`) sent every 2 seconds during idle periods to prevent device disconnection.

5. **Rate limiting:** `max_fps=5.0` caps BLE writes at 5 Hz (200ms interval). This is sufficient for mood following and avoids overwhelming the BLE connection.

6. **Mixed LAN + BLE:** `MultiGoveeLanAdapter` accepts an optional `ble_followers` parameter. LAN devices get per-segment rendered frames at 30 Hz; BLE devices get mood color at 5 Hz. Both driven from the same `send_frame()` call.

### Protocol Variant Selection

```
--ble-device AA:BB:CC:DD:EE:FF          → segment protocol (default, strips)
--ble-device AA:BB:CC:DD:EE:FF:bulb     → bulb protocol (H6006 family)
```

---

## Deliverable 4: Feasibility Verdict

### Go / No-Go: **GO**

All success criteria are met:

| Criterion | Target | Measured | Status |
|---|---|---|---|
| BLE library works on Windows 11 | No special drivers | bleak uses built-in WinRT | PASS |
| Can discover Govee BLE devices | At least 1 | 11 devices discovered | PASS |
| Can set device color programmatically | Working | Confirmed on H617A, H6006 | PASS |
| Write latency <200ms | <200ms | 4-8ms median | PASS |
| Sustained 5+ updates/sec | 5 Hz | 20+ Hz achievable | PASS |
| No restructuring of LAN path | Compatible | ble_followers param added | PASS |

### Dependencies Added

- `bleak>=0.21` — optional dependency (`pip install dreamsync-music-sync[ble]`)
- No other new dependencies

### Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| BLE device disconnects during quiet music | Medium | Keep-alive packets every 2s (implemented) |
| Windows STA threading conflict (pywin32) | Low | DreamSync doesn't use pywin32 |
| BLE adapter limit (3-4 concurrent) | Low | Tested 7 concurrent successfully |
| BLE scan takes 10s, slows startup | Low | Scan is manual (--ble-device uses known addresses) |
| Govee app interferes with BLE control | Medium | Document: close Govee app before running |
| Latency spikes after idle (1-2s first write) | Low | Keep-alive eliminates idle periods |

### Discovered Device Fleet

From `data/ble_scan_results.json`:

| Device | Type | Address | BLE Name Prefix |
|---|---|---|---|
| H617A | LED strip (RGBIC) | C7:90:80:C6:44:74 | Govee_ |
| H612F | LED strip (RGBIC) | DD:6E:05:86:6A:53 | Govee_ |
| H808A | DreamView | DA:B9:84:C6:35:57 | Govee_ |
| H6199 | LED strip | D6:36:34:39:35:3C | Govee_ |
| H6097 | Lamp | D7:01:86:46:44:59 | Govee_ |
| H6006 x6 | Smart bulbs | Various | ihoment_ |

---

## Deliverable 5: Prototype Scripts

Located in `scripts/`:

| Script | Purpose |
|---|---|
| `raw_ble_scan.py` | Discover all BLE devices in range |
| `ble_enumerate_gatt.py` | Enumerate GATT services/characteristics on a device |
| `ble_try_segment_color.py` | Test segment color protocol on a strip |
| `ble_color_variants.py` | Test all color command variants |
| `ble_color_variants_rgb.py` | Test RGB color commands across protocols |
| `ble_all_variants_rainbow.py` | Rainbow test pattern across all variants |
| `ble_confirm_segment_rgb.py` | Confirm segment protocol works on specific devices |
| `ble_segment_discovery.py` | Discover segment count on a strip |
| `ble_h6006_diag.py` | H6006 bulb diagnostic: connect, set color, verify |
| `ble_h6006_color_map.py` | Map working color commands for H6006 |
| `ble_latency_bench.py` | Measure per-write latency (100 writes, both protocols) |
| `ble_throughput_bench.py` | Maximum sustained write throughput (10s burst) |
| `ble_orchestrated_bench.py` | Multi-device coordinated latency test (7 BLE + 2 LAN) |

### Quick Start

```bash
# Scan for devices
python -m dreamsync govee-ble-scan --timeout 10

# Test a single bulb
python -m dreamsync govee-ble-test --address D0:C9:07:C5:14:45 --color "#00ff00" --protocol bulb --duration 5

# Live mood-following with mixed LAN + BLE
python -m dreamsync govee-live \
    --device 10.126.166.180:7:primary:ptreal \
    --device 10.126.166.156:25:primary:razer \
    --ble-device C7:90:80:C6:44:74 \
    --ble-device D0:C9:07:C5:14:45:bulb \
    --duration 120 --debug-mood

# Latency benchmark
python scripts/ble_latency_bench.py C7:90:80:C6:44:74 segment
python scripts/ble_latency_bench.py D0:C9:07:C5:14:45 bulb
```

---

## Implementation Status

The BLE mood-follower feature is **fully implemented and tested**:

- `govee_ble.py`: 458 lines — adapter, discovery, packet builders, threading, keep-alive
- `test_govee_ble.py`: 54 tests, all passing
- CLI integration: `govee-ble-scan`, `govee-ble-test`, `--ble-device` on `govee-live`
- `pyproject.toml`: `bleak>=0.21` optional dependency (`[ble]` extra)
- Validated on real hardware: 7 BLE devices + 2 LAN devices in a 120-second mixed session
