# Device Health Monitor

Runtime detection of device state changes — periodic re-probing, offline/online transitions, latency reclassification, and optional auto-discovery of new devices on the network.

**Current state:** `config_watcher.py` reacts to manual YAML file edits only. There is no periodic re-probing, connectivity monitoring, or automatic role reclassification at runtime.

---

## Architecture Overview

```
                    ┌──────────────────────────┐
                    │    DeviceHealthMonitor    │
                    │  (daemon thread, ~30s)    │
                    ├──────────────────────────┤
                    │ • periodic LAN probe      │
                    │ • BLE connection check     │
                    │ • latency trending         │
                    │ • role reclassification    │
                    │ • auto-discovery (opt-in)  │
                    └────────┬─────────────────┘
                             │ callbacks
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
   on_device_offline   on_device_online   on_role_changed
   (stop sending)      (resume / add)     (swap adapter)
          │                  │                  │
          └──────────────────┼──────────────────┘
                             ▼
                    MultiGoveeLanAdapter
                    (replace_devices / replace_ble_followers)
```

The monitor runs on its own daemon thread, probes devices at a configurable interval, and fires callbacks when device state changes. The live loop and audio pipeline are never blocked.

---

## Step 0 — Per-device health state dataclass

**File:** `src/dreamsync/output/auto_detect.py`

Add a mutable health state alongside the existing frozen `DetectedDevice`:

```python
@dataclass
class DeviceHealth:
    """Mutable runtime health state for a single device."""
    address: str
    role: str                          # current classified role
    status: str = "online"             # "online" | "offline" | "degraded"
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_probe_at: float = 0.0         # monotonic time
    last_seen_at: float = 0.0          # monotonic time of last successful probe
    latency_history: list[float] = field(default_factory=list)  # rolling window of median latencies
    offline_since: float | None = None
```

**Thresholds** (constants or a small config dataclass):

| Constant | Default | Meaning |
|---|---|---|
| `PROBE_INTERVAL_S` | 30 | Seconds between health probes |
| `OFFLINE_THRESHOLD` | 3 | Consecutive failed probes → offline |
| `ONLINE_THRESHOLD` | 2 | Consecutive successes after offline → online |
| `LATENCY_WINDOW` | 10 | Rolling window size for latency history |
| `ROLE_RECLASS_THRESHOLD` | 3 | Consecutive probes at new latency tier → reclassify |
| `DISCOVERY_INTERVAL_S` | 120 | Seconds between network scans (if enabled) |

---

## Step 1 — Instrument `GoveeLanAdapter.send_frame` with success/failure tracking

**File:** `src/dreamsync/output/govee_lan.py`

Currently `_default_udp_transport` catches `OSError` and logs a warning, but the result is discarded. We need to surface transport failures.

1. Add a `last_send_ok: bool` attribute to `GoveeLanAdapter.__init__`.
2. Wrap the transport call in `send_frame()` with a try/except:

```python
def send_frame(self, colors: list[tuple[int, int, int]]) -> bool:
    # ... existing rate-limit + brightness logic ...
    try:
        self._transport(payload, self.config.device_ip, self.config.device_port)
        self.last_send_ok = True
    except OSError as exc:
        _logger.warning("send_frame failed for %s: %s", self.config.device_ip, exc)
        self.last_send_ok = False
    return True  # (rate-limit gate already passed)
```

This is a minimal, non-breaking change. The existing `_default_udp_transport` already catches errors — the new instrumentation catches anything it misses and sets a flag the health monitor can read.

For BLE, `GoveeBleAdapter` already tracks `_state.connected` and `_state.reconnect_attempts`, which the monitor can read directly.

---

## Step 2 — `DeviceHealthMonitor` class

**File:** `src/dreamsync/device_health.py` (new)

```python
class DeviceHealthMonitor:
    def __init__(
        self,
        multi_adapter: MultiGoveeLanAdapter,
        device_configs: list[DeviceConfig],
        *,
        probe_interval: float = 30.0,
        probe_packets: int = 5,       # lightweight probe (not 100 like startup)
        probe_rate: float = 10.0,
        enable_discovery: bool = False,
        discovery_interval: float = 120.0,
        on_device_offline: Callable[[str], None] | None = None,
        on_device_online: Callable[[str, DetectedDevice], None] | None = None,
        on_role_changed: Callable[[str, str, str], None] | None = None,  # addr, old_role, new_role
        on_device_discovered: Callable[[GoveeDevice], None] | None = None,
    ) -> None: ...

    def start(self) -> None: ...      # launch daemon thread
    def stop(self) -> None: ...       # set stop event, join
    def get_health(self, address: str) -> DeviceHealth | None: ...
    def get_all_health(self) -> dict[str, DeviceHealth]: ...
```

### Probe loop (runs on daemon thread)

```
every PROBE_INTERVAL_S:
    for each known device address:
        stats = probe_lan_device(ip, num_packets=5, rate_hz=10)  # fast probe
        health = self._health[address]
        health.last_probe_at = now

        if stats.count == 0:
            health.consecutive_failures += 1
            health.consecutive_successes = 0
            if health.consecutive_failures >= OFFLINE_THRESHOLD and health.status != "offline":
                health.status = "offline"
                health.offline_since = now
                log.warning("Device %s went offline", address)
                fire on_device_offline(address)
        else:
            health.consecutive_successes += 1
            health.consecutive_failures = 0
            health.last_seen_at = now
            health.latency_history.append(stats.median_ms)
            trim to LATENCY_WINDOW

            if health.status == "offline" and health.consecutive_successes >= ONLINE_THRESHOLD:
                health.status = "online"
                health.offline_since = None
                log.info("Device %s back online", address)
                # re-probe with full packets, rebuild adapter for this device
                fire on_device_online(address, re_detected_device)

            # latency reclassification
            new_role = classify_role_from_window(health.latency_history)
            if new_role != health.role and stable_for(ROLE_RECLASS_THRESHOLD):
                old = health.role
                health.role = new_role
                log.info("Device %s reclassified: %s → %s", address, old, new_role)
                fire on_role_changed(address, old, new_role)

    if enable_discovery and time_since_last_scan >= DISCOVERY_INTERVAL_S:
        new_devices = scan_devices(timeout=3.0)
        known_ips = set of current device addresses
        for dev in new_devices:
            if dev.ip not in known_ips:
                log.info("New device discovered: %s (%s)", dev.ip, dev.sku)
                fire on_device_discovered(dev)
```

### Design decisions

- **Lightweight probes:** 5 packets at 10 Hz = 0.5s per device. A 5-device setup takes ~2.5s, well within the 30s interval.
- **Thread-safe:** Health state is per-address in a `dict[str, DeviceHealth]`. The daemon thread is the only writer; readers (telemetry, debug output) get snapshots via `get_all_health()` which returns a shallow copy.
- **Fire-and-forget callbacks:** Callbacks run on the monitor thread. They should be fast (just log + set a flag or enqueue). Heavy work (re-probing for online devices, rebuilding adapters) can be dispatched to a short-lived thread if needed.
- **BLE health:** For BLE devices, skip UDP probing — instead read `_state.connected` and `_state.reconnect_attempts` from `GoveeBleAdapter`. If `not connected and reconnect_attempts > OFFLINE_THRESHOLD`, mark offline. BLE adapters already handle their own reconnection, so the monitor just tracks and reports state.

---

## Step 3 — Offline device handling

When a device goes offline, we need to stop wasting cycles sending it frames.

**Option A — Pause flag on adapter (recommended, simplest)**

Add `paused: bool = False` to `GoveeLanAdapter`. When `paused`, `send_frame()` returns immediately without sending. The monitor sets `paused = True` on offline, `paused = False` on online.

```python
# In GoveeLanAdapter.send_frame():
if self.paused:
    return False
```

This avoids touching `MultiGoveeLanAdapter.devices` list (no rebuild, no locking). The device stays in the list so it can be resumed instantly.

**Option B — Remove from device list**

Call `multi_adapter.replace_devices(...)` with the device removed. More disruptive — requires rebuild to bring it back online. Reserve this for `remove device from config` scenarios (already handled by ConfigWatcher).

### Online device restoration

When a device comes back online:
1. Set `adapter.paused = False`
2. Call `adapter.turn_on()` + `adapter.set_brightness(brightness)` to re-activate it
3. Log the restoration event
4. If the role changed while offline, may need to rebuild the adapter triple (see Step 4)

---

## Step 4 — Latency-based role reclassification

When the rolling latency window shifts a device across a classification boundary (e.g., `realtime` → `follower`), the monitor fires `on_role_changed`.

The handler needs to:
1. Find the device triple in `multi_adapter.devices`
2. Create a new triple with the updated `DeviceRole`
3. Call `multi_adapter.replace_devices(updated_list)`

This reuses the same adapter and renderer — only the role tag changes, which affects how `transform_intent` processes the device's frames.

**Note:** LAN devices are currently hardcoded to `"realtime"` in `detect_all_devices`. For reclassification to work, we must use the actual latency-based classification for LAN devices too, or at least allow the health monitor to override the role. The simplest approach: the monitor's `on_role_changed` callback directly swaps the role in the device triple, bypassing the hardcoded default.

---

## Step 5 — Auto-discovery of new devices

**Optional, gated behind `--health-discovery` CLI flag.**

When `scan_devices()` finds an IP not in the current device list:
1. Fire `on_device_discovered(dev)` callback
2. Default handler: log the discovery at INFO level, take no further action
3. With `--health-auto-add`: probe the new device, classify role, build adapter, add to `MultiGoveeLanAdapter` via `replace_devices()`

Auto-add is intentionally aggressive and should be off by default. Users who want it can opt in.

---

## Step 6 — Integration with session lifecycle

**File:** `src/dreamsync/session.py`

Wire the monitor into `run_session()` alongside the existing `ConfigWatcher`:

```python
# After building multi_adapter and before starting the live loop:
health_monitor = DeviceHealthMonitor(
    multi_adapter=multi_adapter,
    device_configs=configs,
    probe_interval=args.health_interval,       # default 30
    enable_discovery=args.health_discovery,     # default False
    on_device_offline=lambda addr: _handle_offline(multi_adapter, addr),
    on_device_online=lambda addr, dev: _handle_online(multi_adapter, addr, dev, brightness),
    on_role_changed=lambda addr, old, new: _handle_role_change(multi_adapter, addr, old, new),
)
health_monitor.start()

# ... run_live_to_govee(...) ...

# In finally block:
health_monitor.stop()
```

The monitor and ConfigWatcher are independent — ConfigWatcher handles manual YAML edits, the monitor handles runtime state. If a YAML edit triggers a full rebuild, the monitor should re-read the new device list (ConfigWatcher can notify it, or the monitor can diff against `multi_adapter.get_device_addresses()` each cycle).

---

## Step 7 — Telemetry integration

**File:** `src/dreamsync/telemetry.py`

Add a periodic health snapshot to the telemetry stream:

```python
{
    "kind": "device_health",
    "t": 142.5,
    "devices": {
        "10.126.166.180": {
            "status": "online",
            "role": "realtime",
            "latency_ms": 8.2,
            "consecutive_failures": 0
        },
        "10.126.166.181": {
            "status": "offline",
            "offline_since_s": 45.3,
            "consecutive_failures": 4
        }
    }
}
```

Written once per probe cycle (every 30s) to the current song's JSONL file. The `inspect_telemetry.py` script can then show device uptime and latency trends.

---

## Step 8 — CLI flags

**File:** `src/dreamsync/cli.py`

Add to the `govee-live` and `session` subcommands:

| Flag | Type | Default | Description |
|---|---|---|---|
| `--health-monitor` | bool | `False` | Enable periodic device health probing |
| `--health-interval` | float | `30.0` | Seconds between health probes |
| `--health-discovery` | bool | `False` | Scan for new devices on the network |
| `--health-auto-add` | bool | `False` | Automatically add discovered devices |

When `--health-monitor` is not set, no health thread is started (zero overhead for users who don't need it).

---

## Step 9 — Debug output

When `--debug-mood` is active, log health events inline with existing debug output:

```
[142.5s] HEALTH: 10.126.166.180 online (8.2ms) | 10.126.166.181 OFFLINE (45s)
[172.5s] HEALTH: 10.126.166.180 online (9.1ms) | 10.126.166.181 online (RESTORED, 12.3ms)
[202.5s] HEALTH: 10.126.166.180 degraded→follower (median 85ms, was 8ms)
```

---

## Step 10 — Tests

**File:** `tests/test_device_health.py`

| Test | What it validates |
|---|---|
| `test_healthy_device_stays_online` | Probe returns samples → status stays "online" |
| `test_offline_after_threshold` | N consecutive empty probes → fires `on_device_offline` |
| `test_online_after_recovery` | Offline device returns samples → fires `on_device_online` |
| `test_no_flapping` | Single success during offline streak doesn't trigger online |
| `test_role_reclassification` | Latency shift sustained for N probes → fires `on_role_changed` |
| `test_role_no_flap` | Brief latency spike doesn't trigger reclassification |
| `test_paused_adapter_skips_send` | `adapter.paused = True` → `send_frame` returns False, no UDP |
| `test_unpaused_adapter_resumes` | `adapter.paused = False` → frames sent again |
| `test_discovery_finds_new_device` | Mock `scan_devices` returns unknown IP → callback fired |
| `test_discovery_ignores_known` | Known IPs not reported as new |
| `test_ble_health_from_state` | Reads `_state.connected` / `reconnect_attempts` correctly |
| `test_health_telemetry_snapshot` | Health snapshot written to JSONL with correct schema |
| `test_config_watcher_rebuild_resets_health` | After ConfigWatcher rebuild, monitor picks up new device list |
| `test_stop_joins_thread` | `stop()` terminates the daemon thread within timeout |
| `test_concurrent_probe_and_send` | Health probe doesn't block frame sending (thread safety) |

All tests use mock transports and `probe_lan_device` patches — no real UDP.

---

## Implementation Order

1. **Step 0** — `DeviceHealth` dataclass + constants
2. **Step 1** — Instrument `GoveeLanAdapter.send_frame` (+ `paused` flag from Step 3)
3. **Step 2** — `DeviceHealthMonitor` core class with probe loop
4. **Step 3** — Offline/online handlers (pause/unpause adapters)
5. **Step 10** — Tests for Steps 0-3 (validate core before integration)
6. **Step 4** — Role reclassification logic + tests
7. **Step 6** — Session integration
8. **Step 8** — CLI flags
9. **Step 7** — Telemetry integration
10. **Step 9** — Debug output
11. **Step 5** — Auto-discovery (optional, can defer)

---

## What this does NOT cover

- **Audio pipeline health** — microphone disconnects, sample rate drift, etc. (separate concern)
- **Device firmware updates** — out of scope
- **Per-device effect customization based on health** — e.g., reducing FPS for degraded devices (future enhancement)
- **Web dashboard** — health data is in telemetry JSONL; visualization is a separate tool
