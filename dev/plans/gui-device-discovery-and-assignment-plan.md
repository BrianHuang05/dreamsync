# GUI Device Discovery And Assignment Plan

## Goal

Add a GUI workflow for finding physical Govee devices, identifying them in the room, assigning them to spatial positions, and generating or updating `devices.yaml`.

The intended user experience:

- Open a `Device Discovery` page.
- Scan LAN and/or BLE devices.
- See discovered devices, with unassigned devices separated from devices already present in the config.
- Sort unassigned BLE devices by RSSI, strongest first.
- Click `Identify` for any device to flash it blue for a few seconds.
- Assign name, protocol/type, segment count, role, transport, and room position.
- Save the result to `devices.yaml`.
- Continue fine placement in the existing spatial editor.

## Current State

Already present:

- CLI LAN scan via `dreamsync.output.discovery.scan_devices`.
- CLI BLE scan via `dreamsync.output.govee_ble.scan_ble_devices`.
- `DeviceService` can load, validate, and save spatial placements for an existing config.
- `save_device_config` can write normal `DeviceConfig` entries.
- GUI has a spatial editor for existing `devices.yaml` content.

Missing:

- GUI discovery page.
- Unified LAN/BLE discovered-device model.
- GUI scan workflow.
- GUI identify/blue-flash action.
- Config generation/update from discovered devices.
- Assignment state for unassigned devices.

## V1 Scope

V1 should favor a reliable, testable vertical slice:

- Add backend discovery service:
  - LAN scan.
  - BLE scan.
  - Merge with existing config entries.
  - Sort BLE devices by RSSI descending.
  - Generate config entries from user assignments.
  - Identify LAN/BLE devices by flashing blue.
- Add GUI page:
  - Scan buttons for LAN, BLE, and both.
  - Discovered device table/list.
  - Existing assignment indicator.
  - Editable fields for selected device.
  - `Identify` button.
  - `Add / Update Assignment` button.
  - `Save Config` button.
- Integrate with existing config path:
  - If GUI was opened with `--config`, update that file.
  - If no config exists, require choosing/creating a config path in a later phase.

## Explicit Non-Goals For V1

- No automatic room placement inference.
- No persistent pairing database separate from `devices.yaml`.
- No guaranteed BLE model/protocol detection beyond defaults and editable fields.
- No continuous scan loop.
- No destructive removal of existing config entries from the discovery page.
- No pipeline interaction.

## Backend Design

Add `src/dreamsync/gui/services/device_discovery_service.py`.

Data model:

```python
@dataclass(frozen=True)
class DiscoveredDeviceEntry:
    key: str
    source: str              # "lan" | "ble"
    name: str
    address: str
    sku: str = ""
    device_id: str = ""
    rssi: int | None = None
    assigned: bool = False
    existing_name: str = ""
```

Service methods:

- `scan_lan(timeout=5.0) -> list[DiscoveredDeviceEntry]`
- `scan_ble(timeout=10.0) -> list[DiscoveredDeviceEntry]`
- `scan_all(...) -> list[DiscoveredDeviceEntry]`
- `merge_with_config(discovered, config_path) -> list[DiscoveredDeviceEntry]`
- `assignment_to_config(...) -> DeviceConfig`
- `upsert_device_config(path, config) -> None`
- `identify(entry, seconds=4.0, color="#0000ff") -> None`

Identify behavior:

- LAN:
  - Send rapid blue/off or blue/dim pulses via `GoveeLanAdapter`.
  - Use `ptreal` by default for segment-capable devices.
  - Never leave the device permanently on if the identify loop errors.
- BLE:
  - Use `GoveeBleAdapter` in a short-lived mode.
  - Start adapter, send blue/off pulses, stop adapter.
  - Default protocol is `segment`; user can later change to `bulb` in config.

## GUI Design

Add `src/dreamsync/gui/widgets/device_discovery_panel.py`.

Controls:

- Scan LAN
- Scan BLE
- Scan All
- Identify
- Add / Update Assignment
- Save Config
- Device list/table
- Assignment fields:
  - name
  - type: `lan`, `ble`, `auto`
  - segments
  - transport: `ptreal`, `razer`, `colorwc`
  - BLE protocol: `segment`, `bulb`
  - role: blank, `primary`, `accent`
  - brightness scale
  - x/y/z position

Preferred layout:

- Left: discovered devices.
- Right: selected-device assignment form.
- Bottom: status/log line.

## Integration Points

In `main_window.py`:

- Instantiate `DeviceDiscoveryService`.
- Build discovery panel.
- Add as a new top-level tab: `Device Discovery`.
- Wire scan buttons to service methods.
- Wire identify button to service `identify`.
- Wire assignment/save buttons to update `devices.yaml`.
- After saving config, reload the existing spatial scene so the spatial editor sees new devices.

## Testing

Backend unit tests:

- LAN scan maps `GoveeDevice` to `DiscoveredDeviceEntry`.
- BLE scan maps `GoveeBleDevice` and sorts by RSSI.
- Merge marks devices already present in config as assigned.
- Upsert preserves unrelated config entries.
- Upsert updates an existing address instead of duplicating.
- Assignment serializes x/y/z placement.
- Identify can be tested with injected fakes, without real network/BLE.

GUI smoke tests:

- Discovery panel builds.
- Required object names exist.
- Scan buttons are present.
- Assignment fields are present.

Manual tests:

- LAN scan finds LAN devices.
- BLE scan finds BLE devices sorted by RSSI.
- Identify flashes selected LAN device blue.
- Identify flashes selected BLE device blue.
- Assigning and saving creates valid `devices.yaml`.
- Existing spatial editor reloads the saved device layout.

## Implementation Order

1. Backend service and tests.
2. Discovery panel widget and GUI smoke test.
3. Main-window tab integration.
4. Identify action integration with safe short-lived pulses.
5. Config upsert and spatial reload.
6. Manual connected-device testing.

## Decision Points

- Whether BLE identify should use `segment` as the default for unknown Govee devices or ask the user before flashing.
- Whether config creation without an existing `--config` path should be implemented in V1 or deferred.
- Whether room assignment should use raw x/y/z fields only in V1 or include preset buttons such as left/right/front/back/top/bottom.
