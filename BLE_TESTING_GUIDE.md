# BLE Mood Followers: Testing & Validation Guide

This guide walks through the hands-on testing steps for the new BLE adapter code. Each step produces data you should save to a file or paste to Claude for analysis. Do them in order — each step gates the next.

---

## Prerequisites

### 1. Install bleak

```bash
pip install bleak
# or, from the project root:
pip install -e ".[ble]"
```

Verify it imported cleanly:

```bash
python -c "from importlib.metadata import version; print(version('bleak'))"
```

**Save to:** nothing — just confirm it prints a version (0.21+).

### 2. Confirm your Bluetooth adapter

Open Windows Settings > Bluetooth & devices. Make sure Bluetooth is ON.
If you have both a built-in adapter and a USB dongle, note which one you want to use (bleak uses the system default).

---

## Phase 1: Discovery — Can We See Devices?

### Test 1A: BLE scan

Turn on any Govee BLE devices you own (bulbs, portables, strips without LAN). Make sure they're **not** connected to the Govee Home app (BLE is single-connection — if the app holds the link, bleak can't connect).

```bash
python -m dreamsync govee-ble-scan --timeout 15
```

**What to look for:**
- Each device prints as `{"name":"Govee_HXXXX_YYYY","address":"AA:BB:CC:DD:EE:FF","rssi":-60}`
- The `name` tells you the model (e.g. `Govee_H6159`, `ihoment_H6127`)
- RSSI should be > -80 for reliable connections (closer to 0 = stronger)

**Save to:** `data/ble_scan_results.json`

```bash
python -m dreamsync govee-ble-scan --timeout 15 > data/ble_scan_results.json
```

**Give to Claude:** paste the full output. Claude will:
- Identify which device models you have
- Cross-reference against known BLE protocol families
- Flag any devices that might use a different GATT service UUID

**If no devices found:**
- Make sure the Govee Home app is fully closed (force-quit from system tray)
- Check that the device is powered on and in pairing range
- Try `--timeout 30` for slower-advertising devices
- Some devices only advertise for 60s after power-on, then go quiet

### Test 1B: Raw bleak scan (fallback diagnostic)

If `govee-ble-scan` finds nothing, run a raw scan to see ALL BLE devices:

```python
# save as scripts/raw_ble_scan.py
import asyncio
from bleak import BleakScanner

async def main():
    devices = await BleakScanner.discover(timeout=10.0)
    for d in sorted(devices, key=lambda x: x.rssi or -999, reverse=True):
        print(f"  {d.address}  RSSI={d.rssi:>4}  {d.name or '(unnamed)'}")
    print(f"\nTotal: {len(devices)} devices")

asyncio.run(main())
```

```bash
python scripts/raw_ble_scan.py > data/raw_ble_scan.txt
```

**Give to Claude:** paste the output. This helps determine if the device advertises with an unexpected name prefix.

---

## Phase 2: Single-Device Connection — Does the Protocol Work?

Pick one device from the scan results. You need its address (the `AA:BB:CC:DD:EE:FF` part).

### Test 2A: Static color test

```bash
python -m dreamsync govee-ble-test --address AA:BB:CC:DD:EE:FF --color "#ff0000" --duration 10
```

**What to observe:**
- "Connecting..." should resolve within ~5 seconds
- "Connected. Setting color..." should appear
- The device should turn RED
- After 10 seconds, the device should turn off

**Possible outcomes and what they mean:**

| Outcome | Meaning | Next step |
|---------|---------|-----------|
| Device turns red | Protocol works as-is | Proceed to Test 2B |
| Device turns on but wrong color | Color command byte order may differ | Save log, give to Claude |
| Device turns on but stays white/previous | `33 05 02` color command not recognized | Need to try `33 05 15 01` segment variant or sniff with nRF Connect |
| "Connected" but no visible change | Manual mode packet may be wrong, or device needs different init sequence | See troubleshooting below |
| "Failed to connect" | BLE pairing/range issue | Close Govee app, move closer, retry |
| Python exception/traceback | bleak or Windows BLE stack issue | Save full traceback, give to Claude |

**Save to:** `data/ble_test_red.txt`

```bash
python -m dreamsync govee-ble-test --address AA:BB:CC:DD:EE:FF --color "#ff0000" --duration 10 2>&1 | tee data/ble_test_red.txt
```

### Test 2B: Multiple color test

Run these in sequence to confirm RGB channels are correct:

```bash
python -m dreamsync govee-ble-test --address AA:BB:CC:DD:EE:FF --color "#ff0000" --duration 3
python -m dreamsync govee-ble-test --address AA:BB:CC:DD:EE:FF --color "#00ff00" --duration 3
python -m dreamsync govee-ble-test --address AA:BB:CC:DD:EE:FF --color "#0000ff" --duration 3
python -m dreamsync govee-ble-test --address AA:BB:CC:DD:EE:FF --color "#ffffff" --brightness 50 --duration 3
```

**What to observe:**
- Red, green, blue, then dim white — in that order
- If red and blue are swapped, the device uses BGR byte order (Claude can patch the packet builder)
- If brightness doesn't visibly change on the white test, the brightness packet may not be taking effect

**Save to:** just note the visual result for each (R/G/B correct? brightness works?)

### Test 2C: Verbose logging

For deeper diagnostics, enable Python logging:

```bash
python -c "
import logging, time
logging.basicConfig(level=logging.DEBUG)
from dreamsync.output.govee_ble import GoveeBleAdapter, GoveeBleConfig
cfg = GoveeBleConfig(address='AA:BB:CC:DD:EE:FF')
a = GoveeBleAdapter(cfg)
a.start()
time.sleep(6)
a.send_color(0, 255, 0, 100)
time.sleep(5)
a.stop()
" 2>&1 | tee data/ble_verbose_log.txt
```

**Give to Claude:** paste the full output. Claude will check:
- Connection timing
- Whether GATT writes succeed or throw exceptions
- Whether the device disconnects unexpectedly

---

## Phase 3: Protocol Variants — If the Default Doesn't Work

If Test 2A shows the device turns on but doesn't respond to color commands, we need to try alternative packet formats. This section is **only needed if Phase 2 fails**.

### Test 3A: Enumerate GATT services

```python
# save as scripts/ble_enumerate_gatt.py
import asyncio
from bleak import BleakClient

ADDRESS = "AA:BB:CC:DD:EE:FF"  # <-- replace with your device

async def main():
    async with BleakClient(ADDRESS, timeout=15.0) as client:
        print(f"Connected: {client.is_connected}")
        for service in client.services:
            print(f"\nService: {service.uuid}")
            for char in service.characteristics:
                props = ", ".join(char.properties)
                print(f"  Char: {char.uuid}  [{props}]")
                for desc in char.descriptors:
                    print(f"    Desc: {desc.uuid}")

asyncio.run(main())
```

```bash
python scripts/ble_enumerate_gatt.py > data/gatt_services.txt
```

**Give to Claude:** paste the full output. Claude will:
- Confirm whether `00010203-0405-0607-0809-0a0b0c0d1910` / `...2b11` are present
- Identify the correct write characteristic if different
- Spot any vendor-specific services that hint at a different protocol version

### Test 3B: Try the segment color command instead

If `33 05 02` (whole-device) doesn't work, the device might only accept the segment-addressed variant `33 05 15 01`:

```python
# save as scripts/ble_try_segment_color.py
import asyncio
from bleak import BleakClient
from dreamsync.output.govee_lan import build_ptreal_power_packet, build_ptreal_segment_packets
from dreamsync.output.govee_ble import GOVEE_BLE_CHAR_UUID, build_ble_manual_mode_packet

ADDRESS = "AA:BB:CC:DD:EE:FF"  # <-- replace

async def main():
    async with BleakClient(ADDRESS, timeout=15.0) as client:
        # Power on
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(True), response=False)
        await asyncio.sleep(0.5)

        # Manual mode
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ble_manual_mode_packet(), response=False)
        await asyncio.sleep(0.3)

        # Try segment color: all 15 segments red
        colors = [(255, 0, 0)] * 15
        packets = build_ptreal_segment_packets(colors)
        for pkt in packets:
            await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, pkt, response=False)
            await asyncio.sleep(0.05)

        print("Sent segment color packets. Device should be red.")
        await asyncio.sleep(10)

        # Power off
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(False), response=False)

asyncio.run(main())
```

**Give to Claude:** report whether this worked. If it does but `33 05 02` doesn't, Claude will update the adapter to use segment packets for that device family.

---

## Phase 4: Latency Benchmarking

Only proceed here if Phase 2 passed (device responds to color commands).

### Test 4A: Write latency measurement

```python
# save as scripts/ble_latency_bench.py
import asyncio
import time
from bleak import BleakClient
from dreamsync.output.govee_ble import GOVEE_BLE_CHAR_UUID, build_ble_color_packet, build_ble_manual_mode_packet
from dreamsync.output.govee_lan import build_ptreal_power_packet, build_ptreal_brightness_packet

ADDRESS = "AA:BB:CC:DD:EE:FF"  # <-- replace

async def main():
    async with BleakClient(ADDRESS, timeout=15.0) as client:
        # Init
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(True), response=False)
        await asyncio.sleep(0.5)
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ble_manual_mode_packet(), response=False)
        await asyncio.sleep(0.3)
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_brightness_packet(100), response=False)
        await asyncio.sleep(0.1)

        # Benchmark: 100 color writes, alternating red/blue
        latencies = []
        for i in range(100):
            color = (255, 0, 0) if i % 2 == 0 else (0, 0, 255)
            t0 = time.perf_counter()
            await client.write_gatt_char(
                GOVEE_BLE_CHAR_UUID,
                build_ble_color_packet(*color),
                response=False,
            )
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000)  # ms
            await asyncio.sleep(0.05)  # ~20 Hz

        # Power off
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(False), response=False)

        # Report
        import json
        results = {
            "writes": len(latencies),
            "min_ms": round(min(latencies), 2),
            "max_ms": round(max(latencies), 2),
            "avg_ms": round(sum(latencies) / len(latencies), 2),
            "median_ms": round(sorted(latencies)[len(latencies) // 2], 2),
            "p95_ms": round(sorted(latencies)[int(len(latencies) * 0.95)], 2),
            "total_sec": round(sum(latencies) / 1000, 2),
        }
        print(json.dumps(results, indent=2))

        # Also dump raw latencies for histogram
        with open("data/ble_latencies_raw.json", "w") as f:
            json.dump(latencies, f)

asyncio.run(main())
```

```bash
python scripts/ble_latency_bench.py | tee data/ble_latency_results.json
```

**Give to Claude:** paste the JSON summary AND mention what you visually observed (did the device flicker red/blue rapidly? did it stall?). Claude will:
- Determine if latency is within the 200ms target
- Recommend the optimal `max_fps` setting
- Identify if write-without-response is working (should be <5ms per write)

**Success criteria:**
- avg < 50ms per write
- p95 < 100ms
- Visual color changes are perceptible at 20 Hz

### Test 4B: Sustained throughput

```python
# save as scripts/ble_throughput_bench.py
import asyncio
import time
from bleak import BleakClient
from dreamsync.output.govee_ble import GOVEE_BLE_CHAR_UUID, build_ble_color_packet, build_ble_manual_mode_packet
from dreamsync.output.govee_lan import build_ptreal_power_packet

ADDRESS = "AA:BB:CC:DD:EE:FF"  # <-- replace

async def main():
    async with BleakClient(ADDRESS, timeout=15.0) as client:
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(True), response=False)
        await asyncio.sleep(0.5)
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ble_manual_mode_packet(), response=False)
        await asyncio.sleep(0.3)

        # Sustained writes for 10 seconds — no sleep between writes
        count = 0
        errors = 0
        t_start = time.perf_counter()
        while time.perf_counter() - t_start < 10.0:
            color = (count % 256, (count * 3) % 256, (count * 7) % 256)
            try:
                await client.write_gatt_char(
                    GOVEE_BLE_CHAR_UUID,
                    build_ble_color_packet(*color),
                    response=False,
                )
                count += 1
            except Exception as e:
                errors += 1
                await asyncio.sleep(0.1)
        elapsed = time.perf_counter() - t_start

        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(False), response=False)

        import json
        print(json.dumps({
            "writes": count,
            "errors": errors,
            "elapsed_sec": round(elapsed, 2),
            "writes_per_sec": round(count / elapsed, 1),
        }, indent=2))

asyncio.run(main())
```

```bash
python scripts/ble_throughput_bench.py | tee data/ble_throughput_results.json
```

**Give to Claude:** paste the results. Claude will determine the device's maximum write rate and whether the default 5 Hz `max_fps` is too conservative or needs to be lowered.

---

## Phase 5: Live Mood Following — End-to-End Test

Only proceed here if Phase 2 and Phase 4 passed.

### Test 5A: BLE-only live session (single bulb)

```bash
python -m dreamsync govee-live \
  --ble-device D0:C9:07:C5:14:45:bulb \
  --duration 60 \
  --debug-mood \
  --jsonl data/ble_live_solo_60s.jsonl 2>&1 | tee data/ble_live_solo_60s_console.txt
```

Play music with clear dynamics (quiet intro, loud chorus, beat drop). While it runs:

**Observe and note:**
1. Does the BLE device change color on beat?
2. Does brightness track the music energy (dim during quiet, bright during loud)?
3. Is there visible lag between a beat hit and the color change?
4. Does the device disconnect or flicker unexpectedly?
5. What mood transitions do you see in the `--debug-mood` output?

**Give to Claude:** paste:
1. The console output (mood transitions + telemetry)
2. Your observation notes (lag, color accuracy, disconnects)
3. What song you played

Claude will analyze the JSONL for:
- Update rate achieved to BLE device
- Mood transition timing vs music dynamics
- Any error patterns in the logs

### Test 5B: Mixed LAN + BLE live session

This is the real goal — BLE devices following alongside your existing LAN strips:

```bash
python -m dreamsync govee-live \
  --device 10.126.166.180:7:primary:ptreal \
  --device 10.126.166.156:25:primary:razer \
  --ble-device C7:90:80:C6:44:74:segment \
  --ble-device D0:C9:07:C5:14:45:bulb \
  --duration 120 \
  --debug-mood \
  --jsonl data/mixed_lan_ble_120s.jsonl 2>&1 | tee data/mixed_lan_ble_120s_console.txt
```

**Observe and note:**
1. Do the LAN strips still animate normally (scroll/pulse/wave)?
2. Does the BLE strip/bulb roughly match the current mood color?
3. Is there a noticeable delay between the LAN strips changing color and the BLE devices catching up?
4. Does adding BLE cause any stutter or frame drops on the LAN devices?
5. During a DROP event, do all devices react together?

### Test 5C: Full fleet — all BLE devices + LAN

```bash
python -m dreamsync govee-live \
  --device 10.126.166.180:7:primary:ptreal \
  --device 10.126.166.156:25:primary:razer \
  --ble-device C7:90:80:C6:44:74:segment \
  --ble-device D0:C9:07:C5:14:45:bulb \
  --ble-device 98:17:3C:09:7E:ED:bulb \
  --ble-device D0:C9:07:95:15:DB:bulb \
  --ble-device D0:C9:07:70:AD:A3:bulb \
  --ble-device D0:C9:07:95:14:8B:bulb \
  --ble-device 98:17:3C:07:BA:47:bulb \
  --duration 120 \
  --debug-mood \
  --jsonl data/full_fleet_120s.jsonl 2>&1 | tee data/full_fleet_120s_console.txt
```

**Observe:** Do all BLE devices change at roughly the same time? Is there visible stagger between devices? Does the LAN animation degrade?

---

## Phase 6: Troubleshooting Playbook

### Device doesn't respond to any commands

1. Run GATT enumeration (Test 3A), give output to Claude
2. The device may use a different characteristic UUID
3. Some Govee devices require a BLE "pairing" handshake (0x33 0x17 auth sequence) — Claude can add this if needed

### Device responds but colors are wrong

1. Note the exact mapping: what color did you request vs what appeared?
2. Common variants:
   - **BGR instead of RGB** — bytes 3-5 are swapped
   - **HSV instead of RGB** — command `33 05 02` expects H,S,V not R,G,B on some models
   - **Different sub-command** — some models use `33 05 04` for color
3. Give Claude the model number + observed behavior, it will patch the packet builder

### Connection drops during live session

1. Check RSSI (should be > -75)
2. Try increasing `reconnect_delay` in `GoveeBleConfig`
3. Windows BLE stack is known to drop connections under heavy Wi-Fi load — try disabling 2.4GHz Wi-Fi or moving the BLE device closer
4. Check if the device has a firmware update in the Govee app

### bleak won't install or import

```bash
# Windows: ensure you have the WinRT backend
pip install bleak[winrt]

# If that fails, check Python version (need 3.11+) and Windows version (need 10 1709+)
python --version
winver
```

---

## Data Files Summary

After completing all phases, you should have these files in `data/`:

| File | Phase | What Claude needs it for |
|------|-------|--------------------------|
| `ble_scan_results.json` | 1A | Device identification, model cross-reference |
| `raw_ble_scan.txt` | 1B | Fallback if scan finds nothing |
| `ble_test_red.txt` | 2A | Protocol validation |
| `ble_verbose_log.txt` | 2C | Connection diagnostics |
| `gatt_services.txt` | 3A | GATT structure (only if Phase 2 fails) |
| `ble_latencies_raw.json` | 4A | Latency histogram, optimal FPS tuning |
| `ble_latency_results.json` | 4A | Summary stats |
| `ble_throughput_results.json` | 4B | Max write rate |
| `ble_live_solo_60s.jsonl` | 5A | End-to-end mood following analysis |
| `ble_live_solo_60s_console.txt` | 5A | Mood transitions + telemetry |
| `mixed_lan_ble_120s.jsonl` | 5B | Mixed LAN+BLE coordination analysis |
| `mixed_lan_ble_120s_console.txt` | 5B | Console output for mixed session |

---

## What to Bring Back to Claude

After each phase, start a new Claude conversation (or continue this one) with:

1. **The data file(s)** from that phase
2. **Your observation notes** — what you saw, what felt wrong, what felt right
3. **The device model** from the scan (e.g. "Govee H6159 bulb")

Claude will:
- Analyze the data for anomalies
- Suggest protocol adjustments if the device doesn't respond correctly
- Tune `max_fps`, `reconnect_delay`, brightness floor, color mapping
- Update the adapter code for any device-specific quirks
- Recommend whether to proceed to the next phase or debug further

### Key Questions Claude Can Answer From Your Data

- "Is this device fast enough for mood following?" (from Phase 4 latency data)
- "Why isn't the color changing?" (from Phase 3 GATT dump + Phase 2 logs)
- "The BLE device lags behind the LAN strips" (from Phase 5 JSONL — Claude can compare timestamps)
- "Should I increase the update rate?" (from Phase 4 throughput + Phase 5 visual observations)
- "The device disconnects every 30 seconds" (from Phase 2C verbose log — Claude checks reconnect patterns)

---

## After Validation: Next Code Changes

Once you've confirmed BLE works end-to-end, the likely next steps are:

1. **Protocol variant support** — if your device needs different command bytes, Claude adds a `BleProtocolVariant` enum
2. **Optimal FPS tuning** — adjust `GoveeBleConfig.max_fps` based on your latency data
3. **Brightness curve** — the current linear 30-100 mapping may need gamma correction for perceptual smoothness
4. **Auto-discovery integration** — combine `govee-scan` (LAN) and `govee-ble-scan` (BLE) into a unified `govee-discover` command
5. **Config file** — save device addresses + roles to a TOML/JSON config instead of typing long CLI flags every time
