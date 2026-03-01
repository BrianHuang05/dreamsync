# BLE Health Monitor — Reconnection Fixes

Test 9 (offline/online power cycle) revealed that LAN devices reconnect reliably but BLE devices sometimes fail to reconnect even after >1 minute. This document captures the root causes and proposed fixes.

---

## Root Causes

### 1. Health monitor only probes LAN devices

`DeviceHealthMonitor._probe_device()` calls `probe_lan_device()` exclusively. BLE devices stored in `MultiAdapter._ble_followers` are never added to the health state dict during initialization (`_init_health_state` iterates `adapter.config.device_ip`, which is LAN-only). BLE devices are completely invisible to the health monitor.

**File:** `src/dreamsync/device_health.py` — `_probe_device()`, `_init_health_state()`

### 2. No pause/resume mechanism for BLE adapters

When a LAN device goes offline, the health monitor sets `adapter.paused = True` via `_set_adapter_paused()`. This method matches on `adapter.config.device_ip`, so it never hits BLE adapters. BLE adapters have no equivalent pause/resume — their background async loop keeps running and retrying independently.

**File:** `src/dreamsync/device_health.py` — `_set_adapter_paused()`

### 3. BLE reconnection is uncoordinated

The BLE adapter's `_async_loop()` has its own reconnect logic with capped backoff (2–5s). This runs independently of the health monitor — there's no coordination between the two systems. The health monitor can't tell the BLE adapter to stop retrying or to force a fresh connection.

**File:** `src/dreamsync/output/govee_ble.py` — `_async_loop()` reconnect block

### 4. Stale BleakClient handle / no re-scan

On reconnect, a new `BleakClient` is created with the same MAC address, but no fresh BLE discovery scan runs. After a power cycle, the device may:
- Take time to re-advertise its BLE services
- Appear with updated GATT handles that the cached address doesn't resolve
- Require an active scan to become visible again

The reconnect logic just retries `client.connect()` on the same MAC, which can silently fail if the OS BLE stack has stale cached state.

**File:** `src/dreamsync/output/govee_ble.py` — `BleakClient` instantiation in reconnect path

---

## Proposed Fixes

### Fix 1: Add BLE health probing

Extend `DeviceHealthMonitor` to include BLE devices in the health state. A BLE probe can attempt a short GATT read or check `client.is_connected` status. `auto_detect.py` already has `probe_ble_device()` at line 251 that could be reused or adapted.

- Add BLE followers to `_init_health_state()` keyed by MAC address
- Add `_probe_ble_device()` that checks connection status or does a lightweight GATT ping
- Route probes by device type (LAN vs BLE) in `_probe_device()`

### Fix 2: Add pause/resume for BLE adapters

Extend `_set_adapter_paused()` to also iterate `_ble_followers` and match on MAC address. When paused, the BLE adapter's async loop should stop reconnect attempts and release the `BleakClient` handle cleanly.

### Fix 3: Force BLE re-scan on reconnect

When the BLE adapter detects a disconnection, instead of immediately retrying `connect()` on the cached MAC:
1. Release the old `BleakClient` completely
2. Run a short BLE discovery scan (`BleakScanner.discover(timeout=5)`)
3. Confirm the target MAC is visible in scan results
4. Only then create a new `BleakClient` and attempt connection

This ensures the OS BLE stack has fresh advertisement data and avoids stale cache issues.

### Fix 4: Coordinate health monitor with BLE reconnection

When the health monitor detects a BLE device has come back online (via probe), it should:
1. Unpause the adapter
2. Signal the adapter to attempt a fresh connection (with re-scan)
3. Wait for confirmation before marking the device as fully online

This prevents the race condition where the adapter's independent retry loop and the health monitor's state machine disagree about device status.
