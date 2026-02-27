# DreamSync — Home Validation Tests (No Music)

All tests below run against real hardware with **no music playing**. They validate system infrastructure — role classification, color profiles, and device health — independently from the audio pipeline.

> **Prerequisites:**
> - Govee LAN device(s) powered on and connected to your network
> - Python virtual environment activated (`.venv`)
> - `devices.yaml` created in the project root (see [Device Config](#device-config) below)

---

## Device Config

Create `devices.yaml` in the project root before running session-mode tests:

```yaml
devices:
  - name: "LED strip small"
    address: "10.126.166.180"
    type: lan
    segments: 7
    transport: ptreal
    role: primary

  - name: "LED strip large"
    address: "10.126.166.156"
    type: lan
    segments: 25
    transport: razer
    role: primary
```

Adjust IPs/segments to match your actual devices.

---

## Phase 1 — Auto-Detect Role Classification

**Goal:** Verify that `detect_all_devices()` correctly probes LAN devices, measures latency, and assigns roles.

### 1.1 Unit tests (sanity check)

```bash
python -m pytest tests/test_auto_detect.py -v
```

**Pass:** All 24 tests pass.

### 1.2 LAN device scan

Confirm devices are reachable on the network:

```bash
python -m dreamsync govee-scan
```

**Pass:** Both devices appear with their IPs and SKUs.

### 1.3 Session auto-detect (short run)

Run a session with auto-detect and debug output but no music. Ctrl+C after observing the detection report (~10s):

```bash
python -m dreamsync session --config devices.yaml --debug-mood --telemetry-dir out/role-test
```

**Pass criteria:**
- Detection report prints for each device: IP, latency stats (median, p95), classified role
- LAN devices classified as `realtime` (median latency < 20ms)
- Unreachable devices (if any) show `role: unreachable` and are skipped
- No crash on startup; Ctrl+C shuts down cleanly

### 1.4 Connectivity test (solid color)

Verify each device actually receives frames:

```bash
# Small strip (7 segments, ptreal)
python -m dreamsync govee-test --device-ip 10.126.166.180 --segments 7 --transport ptreal --color "#00ff00" --duration 5 --pattern walk

# Large strip (25 segments, razer)
python -m dreamsync govee-test --device-ip 10.126.166.156 --segments 25 --transport razer --color "#0000ff" --duration 5 --pattern rainbow
```

**Pass:** Each device lights up with the specified pattern and turns off after 5 seconds.

---

## Phase 2 — Color Profile System

### 2a — Basic Profile Loading

**Goal:** Verify a profile loads, palettes resolve, and the EffectCycler uses profile-defined colors.

#### 2a.1 Unit tests

```bash
python -m pytest tests/test_profile.py -v
```

**Pass:** All 63 tests pass.

#### 2a.2 List available profiles

```bash
python -m dreamsync profiles --verbose
```

**Pass:** All 8 built-in profiles listed with name, description, and tags.

#### 2a.3 Validate a profile

```bash
python -m dreamsync profile-validate aurora
```

**Pass:** Prints profile name, moods (chill, drop, groove, hype), palette names, and "No harmony warnings."

#### 2a.4 Live profile test (with device, no music)

Run with a profile and debug output. Even without music, the system will enter CHILL mood and render effects:

```bash
python -m dreamsync govee-live --device 10.126.166.180:7:primary:ptreal --duration 60 --profile aurora --debug-mood
```

**Pass criteria:**
- Console shows `Loaded profile: Aurora Borealis`
- Debug output shows palette names from the aurora profile (e.g., `aurora_green`, `aurora_purple`) — NOT default built-in palette names
- Device displays aurora-themed colors (greens, purples, pinks)
- Effects cycle from the profile's CHILL effect pool (wave_drift, slow_breathe)
- Ctrl+C shuts down cleanly

#### 2a.5 Different profile comparison

Run with a visually distinct profile to confirm the profile is actually being used:

```bash
python -m dreamsync govee-live --device 10.126.166.180:7:primary:ptreal --duration 30 --profile neon_city --debug-mood
```

**Pass:** Colors are visually different from the aurora test (hot pinks, electric blues, lime greens). Debug output shows neon_city palette names.

### 2b — Hot-Swap

**Goal:** Verify editing a profile YAML mid-session triggers a live reload.

#### 2b.1 Unit tests (ProfileWatcher)

```bash
python -m pytest tests/test_profile.py -v -k "watcher"
```

**Pass:** ProfileWatcher tests pass.

#### 2b.2 Live hot-swap test

**Step 1:** Start a session with profile watching enabled:

```bash
python -m dreamsync session --config devices.yaml --profile aurora --debug-mood
```

**Step 2:** While the session is running, open `src/dreamsync/profiles/aurora.yaml` in a text editor and change one of the palette colors (e.g., change `#003300` to `#ff0000` in `aurora_green`).

**Step 3:** Save the file and watch the console.

**Pass criteria:**
- Within ~2 seconds of saving, console shows a profile reload message
- Device colors visibly change to reflect the edited palette
- No crash or error on reload

**Step 4:** Introduce a YAML syntax error (e.g., delete a colon). Save.

**Pass:** Console logs a warning about the invalid YAML. Old profile remains active. Device keeps running.

**Step 5:** Fix the syntax error and save again.

**Pass:** Console shows profile reloaded. Colors update to the corrected version.

### 2c — Profile Rotation

**Goal:** Verify timed rotation through multiple profiles.

#### 2c.1 Unit tests (ProfileRotation)

```bash
python -m pytest tests/test_profile.py -v -k "rotation"
```

**Pass:** ProfileRotation tests pass.

#### 2c.2 Live rotation test

Run with 3 profiles rotating every 30 seconds (shortened for testing):

```bash
python -m dreamsync govee-live --device 10.126.166.180:7:primary:ptreal --duration 120 --profile-rotation aurora,neon_city,midnight_rave --rotation-interval 30 --debug-mood
```

**Pass criteria:**
- Console shows `Profile rotation: 3 profiles, rotating every 30s`
- At ~30s: `[rotation] switched to profile: Neon City` — device colors shift to neon tones
- At ~60s: `[rotation] switched to profile: Midnight Rave` — device colors shift to purple/blue neons
- At ~90s: back to `Aurora Borealis` — device colors shift to greens/purples
- Each switch causes a visible color change on the device

---

## Phase 3 — Device Health Monitor

### 3.1 Unit tests

```bash
python -m pytest tests/test_device_health.py -v
```

**Pass:** All 29 tests pass.

### 3.2 Health monitor cold start (probe lifecycle)

Run the health monitor with a short probe interval and observe the probe logs:

```bash
python -m dreamsync session --config devices.yaml --health-monitor --health-interval 15 --debug-mood
```

**Pass criteria:**
- Health probe logs appear every ~15 seconds showing device status + latency
- All devices show `online` status
- No thread errors or leaks in the console
- Ctrl+C cleanly shuts down the health thread + all devices

### 3.3 Offline detection

**Step 1:** Start the session with health monitoring:

```bash
python -m dreamsync session --config devices.yaml --health-monitor --health-interval 15 --debug-mood
```

**Step 2:** Power off (or unplug) one of your Govee devices while the session is running.

**Pass criteria:**
- After ~45 seconds (3 failed probes at 15s interval): `WARNING: Device <IP> went offline`
- The offline device's adapter is paused (no UDP error spam in logs)
- The other device(s) continue receiving frames normally

### 3.4 Online recovery

**Step 1:** With the device still offline from test 3.3, power the device back on.

**Pass criteria:**
- After ~30 seconds (2 successful probes): `INFO: Device <IP> back online`
- Device resumes receiving frames immediately — colors appear on the restored device
- No manual intervention required

### 3.5 Health + telemetry

Run with telemetry enabled to verify health snapshots are written:

```bash
python -m dreamsync session --config devices.yaml --health-monitor --health-interval 15 --telemetry-dir out/health-test --debug-mood
```

Let it run for ~60 seconds (at least 4 probe cycles), then Ctrl+C.

**Verify telemetry:**

```bash
python -c "
import json, glob
for f in glob.glob('out/health-test/session-*/song-*.jsonl'):
    with open(f) as fh:
        for line in fh:
            row = json.loads(line)
            if row.get('kind') == 'device_health':
                print(json.dumps(row, indent=2))
"
```

**Pass:** At least 2-3 `device_health` rows with per-device status, role, latency, and failure counts.

### 3.6 Discovery scan (optional)

If you have additional Govee devices on the network not listed in `devices.yaml`:

```bash
python -m dreamsync session --config devices.yaml --health-monitor --health-discovery --debug-mood
```

**Pass:** `INFO: New device discovered: X.X.X.X (HXXXX)` if an unlisted Govee device is found.

---

## Quick Reference — Full Test Sequence

Run these in order for a complete validation pass:

- [x] **1. Unit tests** (all systems)
- [x] **2. Network scan**
- [x] **3. Device connectivity**
- [x] **4. Auto-detect + role classification**
- [x] **5. Profile basic** (aurora + neon_city comparison)
- [x] **6. Profile hot-swap** (edit aurora.yaml while running)
- [ ] **7. Profile rotation** (3 profiles, 30s intervals)
- [ ] **8. Health monitor** (probe lifecycle + telemetry)
- [ ] **9. Offline/online** (power cycle a device during test 8)

```bash
# 1. Unit tests (all systems) ✅
python -m pytest tests/test_auto_detect.py tests/test_profile.py tests/test_device_health.py -v

# 2. Network scan ✅
python -m dreamsync govee-scan

# 3. Device connectivity ✅
python -m dreamsync govee-test --device-ip 10.126.166.180 --segments 7 --transport ptreal --color "#00ff00" --duration 5 --pattern walk

# 4. Auto-detect + role classification ✅
python -m dreamsync session --config devices.yaml --debug-mood --telemetry-dir out/role-test

# 5. Profile basic ✅
python -m dreamsync profiles --verbose
python -m dreamsync profile-validate aurora
python -m dreamsync govee-live --device 10.126.166.180:7:primary:ptreal --duration 60 --profile aurora --debug-mood

# 6. Profile hot-swap (edit aurora.yaml while running) ✅
python -m dreamsync session --config devices.yaml --profile aurora --debug-mood

# 7. Profile rotation
python -m dreamsync govee-live --device 10.126.166.180:7:primary:ptreal --duration 120 --profile-rotation aurora,neon_city,midnight_rave --rotation-interval 30 --debug-mood

# 8. Health monitor
python -m dreamsync session --config devices.yaml --health-monitor --health-interval 15 --debug-mood --telemetry-dir out/health-test

# 9. Offline/online (power cycle a device during test 8)
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `govee-scan` finds no devices | Check devices are powered on and on same subnet. Try power-cycling the device. |
| `govee-test` sends frames but device stays dark | Device may be in a bad state — power cycle it. Check transport matches the model (ptreal for H612F, razer for H808A). |
| Session fails with "No reachable devices" | All devices failed probing. Check IPs in `devices.yaml`. Run `govee-scan` to verify. |
| Profile not loading | Check spelling matches a built-in name. Run `python -m dreamsync profiles` to see available names. |
| Hot-swap not triggering | `ProfileWatcher` polls every 2s. Ensure you're editing the correct file (the one under `src/dreamsync/profiles/`). Ensure session was started with `--profile` (not `--profile-rotation`). |
| Health monitor offline detection too slow | Reduce `--health-interval` (e.g., `10`). Default offline threshold is 3 probes. |
