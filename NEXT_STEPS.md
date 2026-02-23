# Next Steps: Latency-Based Role Detection & Infinite Loop Mode

These tasks should be completed after BLE mood-follower support is fully validated in production use.

## 1. Latency-Based Automatic Role Detection

### Goal

Automatically detect whether each device in the fleet should serve as a **realtime renderer** (LAN, <20ms latency) or a **slow follower** (BLE, >50ms latency) based on a measured latency test at startup.

### Current State

Today, device roles are configured manually:
- LAN devices: `--device IP:SEGMENTS:ROLE:TRANSPORT` (role = primary/accent)
- BLE devices: `--ble-device ADDRESS:PROTOCOL` (always mood follower)

The user must know which devices are LAN-capable and which are BLE-only.

### Proposed Design

#### Device Configuration File

Replace CLI flags with a YAML/JSON device configuration file:

```yaml
# devices.yaml
devices:
  - name: "Living Room Strip"
    address: "10.126.166.180"        # IP or BLE address
    type: auto                        # auto-detect LAN vs BLE
    segments: 7
    transport: ptreal                 # hint for LAN devices

  - name: "TV Backlight"
    address: "10.126.166.156"
    type: auto
    segments: 25
    transport: razer

  - name: "Desk Bulb"
    address: "D0:C9:07:C5:14:45"
    type: auto
    protocol: bulb                    # hint for BLE devices

  - name: "Side Strip"
    address: "C7:90:80:C6:44:74"
    type: auto
    segments: 15
```

#### Startup Latency Test (60 seconds)

At startup, before entering the main audio loop:

1. **Attempt LAN contact** for each device: send a UDP `scan` packet to port 4001 and listen for a response. Devices that respond are LAN-capable.

2. **Attempt BLE contact** for remaining devices: connect via bleak, write a power-on packet, measure round-trip time.

3. **Measure sustained latency** over 60 seconds:
   - LAN devices: send 100 UDP packets, measure response time
   - BLE devices: write 100 GATT packets at 5 Hz, measure write latency
   - Record min, median, P95, max for each device

4. **Classify each device** based on measured latency:
   - **Realtime** (median <20ms): Full segment rendering at 30 Hz
   - **Follower** (median 20-200ms): Mood follower at 5 Hz
   - **Slow** (median >200ms): Mood follower at 2 Hz
   - **Unreachable**: Skip, log warning

5. **Report classification** to the user before starting the main loop.

#### Implementation Location

```
src/dreamsync/output/auto_detect.py   — New module for latency testing and role assignment
```

Key function signatures:

```python
@dataclass
class DetectedDevice:
    name: str
    address: str
    connection_type: Literal["lan", "ble", "unreachable"]
    latency_median_ms: float
    latency_p95_ms: float
    role: Literal["realtime", "follower", "slow"]
    transport: TransportMode | BleProtocol | None

async def detect_device_roles(
    devices: list[DeviceConfig],
    test_duration: float = 60.0,
    test_rate_hz: float = 5.0,
) -> list[DetectedDevice]:
    """Run latency tests on all devices and assign roles."""
    ...

def build_multi_adapter_from_detected(
    detected: list[DetectedDevice],
    render_mode: RenderMode,
    mirror: bool,
    brightness: float,
    fps: int,
) -> MultiGoveeLanAdapter:
    """Build a MultiGoveeLanAdapter with appropriate adapters and renderers."""
    ...
```

## 2. Infinite Loop Mode

### Goal

Replace the `--duration` parameter with an infinite loop that runs until Ctrl+C or a stop signal.

### Current State

`run_live_to_govee()` in `live.py` accepts `duration_seconds` and exits after that time. This was appropriate for testing but not for production use.

### Proposed Design

#### New Entry Point

```
src/dreamsync/session.py   — New module for the production session runner
```

Key changes:

```python
def run_session(
    config_path: Path,
    sample_rate: int = 44100,
    channels: int = 1,
    audio_device: int | None = None,
    auto_cycle: bool = True,
    debug_mood: bool = False,
) -> None:
    """Run an infinite DreamSync session.

    1. Load device config from YAML/JSON file
    2. Run 60-second latency detection
    3. Build multi-adapter with auto-assigned roles
    4. Enter infinite audio-reactive loop (Ctrl+C to stop)
    5. Gracefully shut down all devices on exit
    """
    ...
```

#### Signal Handling

```python
import signal

_running = True

def _handle_sigint(sig, frame):
    global _running
    _running = False
    print("\nShutting down...")

signal.signal(signal.SIGINT, _handle_sigint)

# Main loop
while _running:
    # ... process audio, render, send frames ...
```

#### CLI Integration

New CLI command:

```
python -m dreamsync session --config devices.yaml [--audio-device N] [--debug-mood]
```

This replaces the current `govee-live` command for production use. `govee-live` remains available for testing with explicit device flags and duration.

## 3. Implementation Order

1. **`auto_detect.py`** — Latency testing and role classification
2. **`session.py`** — Infinite loop session runner with device config loading
3. **CLI `session` command** — New subparser in `cli.py`
4. **Tests** — Unit tests for auto-detection logic (mocked network/BLE)
5. **Documentation** — Update usage examples

## 4. Dependencies on BLE Implementation

Both features depend on BLE mood-follower support being stable:

- Auto-detection needs `GoveeBleAdapter` to measure BLE latency
- Infinite session needs reliable BLE reconnection (keep-alive packets, backoff)
- Device config file needs to support both LAN and BLE device specs

The BLE implementation is currently complete and passing all tests. The keep-alive mechanism (added during this research) should be validated in a long-running session (30+ minutes) before building on top of it.
