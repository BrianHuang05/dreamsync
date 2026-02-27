# Hot-Reload Device Config

## Goal

Watch `devices.yaml` for changes during an active session and add/remove devices without restarting. The audio loop keeps running uninterrupted.

---

## Current State

- `load_device_config(path)` is a pure parser — safe to re-call at any time
- `detect_all_devices()` + `build_multi_adapter()` are stateless factories
- `GoveeLanAdapter` has no persistent state — ephemeral UDP per send
- `GoveeBleAdapter` has clean `start()`/`stop()` lifecycle methods
- `MultiGoveeLanAdapter.devices` and `._ble_followers` are plain mutable lists
- **No file watching, no thread safety on device lists, no per-device activate/deactivate**

---

## Design

### Architecture

```
┌──────────────┐  mtime change   ┌──────────────────┐
│  ConfigWatch │ ──────────────► │  diff old vs new  │
│  (thread)    │                 │  DeviceConfig[]   │
└──────────────┘                 └────────┬─────────┘
                                          │
                          ┌───────────────┼───────────────┐
                          ▼               ▼               ▼
                     add devices    remove devices   update devices
                     (probe+build)  (stop+remove)    (remove+re-add)
                          │               │               │
                          └───────────────┼───────────────┘
                                          ▼
                                ┌──────────────────┐
                                │ MultiGoveeLan    │
                                │ atomic list swap  │
                                └──────────────────┘
```

### File: `src/dreamsync/config_watcher.py` (new)

Single new module, ~120 lines.

#### `ConfigWatcher` class

```python
class ConfigWatcher:
    def __init__(
        self,
        config_path: Path,
        multi_adapter: MultiGoveeLanAdapter,
        *,
        poll_interval: float = 2.0,        # seconds between mtime checks
        probe_packets: int = 10,            # quick probe for hot-add (not full 100)
        probe_rate: float = 5.0,
        render_mode: RenderMode = RenderMode.SCROLL,
        mirror: bool = True,
        brightness: float = 1.0,
        fps: int = 30,
    ):
        ...

    def start(self) -> None:       # launch daemon watcher thread
    def stop(self) -> None:        # signal thread to exit, join
    def _watch_loop(self) -> None: # poll mtime, on change: reload + diff + apply
```

#### Reload cycle (inside `_watch_loop`)

1. **Poll** `config_path.stat().st_mtime` every `poll_interval` seconds
2. On change, call `load_device_config(config_path)` inside a try/except (malformed YAML → log warning, skip)
3. **Diff** old configs vs new configs, keyed by `address` (the unique device identifier):
   - **Added**: address in new but not old
   - **Removed**: address in old but not new
   - **Changed**: address in both but config fields differ (segments, transport, protocol, role, max_fps)
   - **Unchanged**: skip
4. **Apply** changes (see below)

#### Diff function (pure, testable)

```python
def diff_device_configs(
    old: list[DeviceConfig],
    new: list[DeviceConfig],
) -> tuple[list[DeviceConfig], list[DeviceConfig], list[DeviceConfig]]:
    """Returns (added, removed, changed) device configs."""
```

Key by `address` (normalized: strip whitespace, lowercase). A "changed" device is one where the address matches but any other field differs.

#### Apply: add devices

1. Call `detect_all_devices(added_configs, probe_packets, probe_rate)` — quick 10-packet probe
2. Call `build_multi_adapter(detected, ...)` to get triples and BLE followers
3. Activate new LAN adapters individually: `adapter.turn_on()`, `adapter.set_brightness(brightness)`
4. Start new BLE adapters: `adapter.start()`
5. Atomic swap into `multi_adapter` (see thread safety below)

#### Apply: remove devices

1. Find matching entries in `multi_adapter.devices` and `._ble_followers` by IP/address
2. LAN: call `adapter.turn_off()` (best-effort, fire-and-forget)
3. BLE: call `adapter.stop()` (joins thread, max 5s)
4. Atomic swap to remove from lists

#### Apply: changed devices

Treat as remove + add. Simple, avoids partial-update bugs.

---

### Thread Safety on `MultiGoveeLanAdapter`

The audio thread iterates `self.devices` and `self._ble_followers` in `send_frame()` on every frame (~30 Hz). The watcher thread mutates these lists on config change.

**Strategy: snapshot swap (no lock needed)**

```python
# In MultiGoveeLanAdapter.send_frame (already exists):
devices = self.devices  # grab reference — safe even if self.devices is reassigned

# In ConfigWatcher._apply:
# Build new complete list, then single atomic assignment
self._multi.devices = new_devices_list
self._multi._ble_followers = new_ble_list
```

Python's GIL guarantees that reference assignment is atomic. The audio thread either sees the old list or the new list — never a half-mutated list. No lock required.

Add two helper methods to `MultiGoveeLanAdapter`:

```python
def replace_devices(self, new_devices: list[tuple[GoveeLanAdapter, SegmentRenderer, DeviceRole]]) -> None:
    """Atomically replace the device list."""
    self.devices = new_devices

def replace_ble_followers(self, new_followers: list[GoveeBleAdapter]) -> None:
    """Atomically replace the BLE follower list."""
    self._ble_followers = new_followers
```

---

### Changes to Existing Files

#### `src/dreamsync/session.py`

- Import `ConfigWatcher`
- After building `multi_adapter`, create and start a `ConfigWatcher`
- In the `finally` block, stop the watcher before deactivating devices
- Add `--hot-reload` / `--no-hot-reload` passthrough (default: enabled)

```python
# After multi_adapter.activate(...)
watcher = ConfigWatcher(config_path, multi_adapter, ...)
watcher.start()
try:
    run_live_to_govee(...)
finally:
    watcher.stop()
    multi_adapter.deactivate()
```

#### `src/dreamsync/output/govee_lan.py` — `MultiGoveeLanAdapter`

- Add `replace_devices()` and `replace_ble_followers()` methods (trivial, 2 lines each)
- Ensure `send_frame` reads `self.devices` into a local variable at the top (it already does `for adapter, renderer, role in self.devices` which grabs the list reference once — but verify)
- Expose a way to look up devices by address for the diff/remove logic:
  - Add a `get_device_addresses() -> list[str]` helper that returns `[adapter.config.device_ip for adapter, _, _ in self.devices]`

#### `src/dreamsync/cli.py`

- Add `--hot-reload` / `--no-hot-reload` flag to `session` subcommand (default: `True`)
- Forward to `run_session()`

#### `src/dreamsync/output/auto_detect.py`

No changes needed. `detect_all_devices` and `build_multi_adapter` are already stateless and re-callable.

---

### Logging

All reload events logged at INFO level:

```
Config change detected, reloading devices.yaml
  + Adding device: "LED strip (small)" at 10.126.166.180
  - Removing device: "Old strip" at 10.126.166.200
  ~ Updating device: "LED strip (large)" at 10.126.166.156 (segments: 15 → 25)
Config reload complete: 2 devices active (1 added, 1 removed, 1 updated)
```

Parse errors logged at WARNING, no state change:

```
Config reload failed: devices.yaml parse error at line 12: expected int for 'segments'
```

---

## Test Plan

All tests in `tests/test_config_watcher.py`, no hardware needed.

### `diff_device_configs` (pure function, ~6 tests)

1. No changes → empty adds/removes/changed
2. One device added → appears in added list
3. One device removed → appears in removed list
4. Device with changed segments → appears in changed list
5. Address normalization (case, whitespace)
6. Multiple simultaneous adds, removes, changes

### `ConfigWatcher` integration (~7 tests)

7. Watcher detects mtime change and calls reload
8. Malformed YAML → warning logged, devices unchanged
9. Added device appears in multi_adapter.devices after reload
10. Removed device disappears from multi_adapter.devices after reload
11. Changed device is replaced (old adapter stopped, new one started)
12. BLE device add → `start()` called; remove → `stop()` called
13. `stop()` cleanly exits watcher thread

### Thread safety (~2 tests)

14. Concurrent `send_frame` + `replace_devices` → no crash, no partial state
15. Rapid successive config changes → only latest state applied

---

## Implementation Order

1. **`diff_device_configs`** — pure function + tests (no dependencies)
2. **`MultiGoveeLanAdapter.replace_devices/replace_ble_followers`** + address helpers + tests
3. **`ConfigWatcher`** class + tests (mocks `MultiGoveeLanAdapter`, filesystem)
4. **Wire into `session.py`** + CLI flag
5. **Update IMPLEMENTATION.md and NEXT_STEPS.md**

---

## Scope Boundaries

**In scope:**
- File mtime polling on a background thread
- Diff by device address, apply adds/removes/changes
- Atomic list swap for thread safety
- Unit tests with mocked adapters

**Out of scope (future work):**
- `watchdog`/`inotify` for push-based file watching (polling is simpler, fast enough at 2s)
- Hot-reloading audio parameters (sample rate, frame size, etc.)
- Hot-reloading effect/mood tuning parameters
- GUI or API for live device management
