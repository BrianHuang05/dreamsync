# Plan: Issue 5 — Brightness & Device Roles (Device Brightness Calibration)

Priority: P2 | Effort: Low | Affects: strips overpower bulbs physically

---

## Deliverable 1: Per-Device Brightness Scale in Device Config

### Prerequisites
- Read `src/dreamsync/output/roles.py` — `DeviceRole`, `transform_intent()` (current role-based intensity scaling)
- Read `src/dreamsync/director.py` — `LightingIntent` dataclass (intensity field, 0.0–1.0)
- Understand device config format: `devices.yaml` specifies per-device IP, model, segments, role

### Problem
`transform_intent()` applies a flat 0.6x multiplier for ACCENT devices and 1.0x for PRIMARY. This is a role-based distinction, not a physical brightness calibration. In the real room:
- LED strips at close range (under cabinet, desk edge) are physically much brighter per perceived unit than bulbs across the room
- A strip at 60% intensity still overpowers a bulb at 100% due to proximity and LED density
- The ACCENT 0.6x multiplier is about visual role (background vs foreground), not about equalizing perceived brightness

There's no way for the user to say "my strip should run at 40% max because it's too close."

### Solution

1. **Add `brightness_scale` field to device config**:
   ```yaml
   # devices.yaml
   devices:
     - ip: "192.168.1.100"
       model: "H6159"
       segments: 15
       role: primary
       brightness_scale: 0.4    # NEW: physical brightness calibration (0.0–1.0)
     - ip: "192.168.1.101"
       model: "H6001"
       segments: 1
       role: primary
       brightness_scale: 1.0    # bulb at full brightness
   ```

2. **Apply brightness_scale in `transform_intent()`**:
   ```python
   # src/dreamsync/output/roles.py

   def transform_intent(
       intent: LightingIntent,
       role: DeviceRole,
       brightness_scale: float = 1.0,  # NEW parameter
   ) -> LightingIntent:
       # Apply role transform first
       if role == DeviceRole.ACCENT:
           intent = LightingIntent(
               mode=EffectMode.AMBIENT,
               intensity=intent.intensity * 0.6,
               speed=min(intent.speed, 0.25),
               bpm=intent.bpm,
               color=intent.color,
           )

       # Then apply brightness_scale
       if brightness_scale != 1.0:
           intent = LightingIntent(
               mode=intent.mode,
               intensity=intent.intensity * max(0.0, min(1.0, brightness_scale)),
               speed=intent.speed,
               bpm=intent.bpm,
               color=intent.color,
           )

       return intent
   ```

3. **Thread brightness_scale through the adapter/runtime pipeline**. Wherever `transform_intent()` is called, pass the device's `brightness_scale`:
   ```python
   # In the multi-adapter or wherever intent is transformed before sending
   for adapter, renderer, role in self.devices:
       device_intent = transform_intent(intent, role, brightness_scale=adapter.brightness_scale)
       ...
   ```

4. **Default brightness_scale** to 1.0 if not specified in config (backward compat).

### Files to Modify
- `src/dreamsync/output/roles.py` — add `brightness_scale` parameter to `transform_intent()`
- Device config loader (wherever `devices.yaml` is parsed) — read `brightness_scale` field
- Multi-adapter or equivalent — pass `brightness_scale` when calling `transform_intent()`

### Unit Tests
- `python -m pytest dev/tests/test_output_roles.py -v`
- New tests:
  - `test_brightness_scale_reduces_intensity` — `transform_intent(intent(intensity=0.8), PRIMARY, brightness_scale=0.4)` → intensity=0.32
  - `test_brightness_scale_default_noop` — `transform_intent(intent, PRIMARY)` → intensity unchanged (default 1.0)
  - `test_brightness_scale_stacks_with_accent` — `transform_intent(intent(intensity=1.0), ACCENT, brightness_scale=0.5)` → intensity = 1.0 * 0.6 * 0.5 = 0.30
  - `test_brightness_scale_clamped` — `transform_intent(intent, PRIMARY, brightness_scale=1.5)` → treated as 1.0 (clamped)
  - `test_brightness_scale_zero` — `transform_intent(intent, PRIMARY, brightness_scale=0.0)` → intensity=0.0 (device off)

### Completion Criteria
- `brightness_scale` in device config controls per-device max brightness
- Role-based scaling and brightness_scale stack multiplicatively
- Missing brightness_scale defaults to 1.0 (no change to existing behavior)
- Device config schema documented

---

## Deliverable 2: Auto-Role Assignment by Device Type

### Prerequisites
- Deliverable 1 (brightness_scale mechanism exists)
- Issue 4 Deliverable 2 (`DeviceType` enum and `infer_device_type()` exist)

### Problem
If the user doesn't explicitly assign roles and brightness_scale in the config, all devices get PRIMARY role with 1.0 brightness. The user has to manually figure out good values for each device. Reasonable defaults based on device type would reduce setup friction.

### Solution

1. **Add default brightness_scale by device type**:
   ```python
   # src/dreamsync/output/roles.py

   DEFAULT_BRIGHTNESS_SCALE: dict[DeviceType, float] = {
       DeviceType.BULB: 1.0,
       DeviceType.STRIP_SINGLE: 0.5,
       DeviceType.STRIP_MULTI: 0.4,
   }

   DEFAULT_ROLE: dict[DeviceType, DeviceRole] = {
       DeviceType.BULB: DeviceRole.PRIMARY,
       DeviceType.STRIP_SINGLE: DeviceRole.ACCENT,
       DeviceType.STRIP_MULTI: DeviceRole.PRIMARY,
   }

   def default_device_config(device_type: DeviceType) -> tuple[DeviceRole, float]:
       """Return sensible defaults for role and brightness_scale."""
       return (
           DEFAULT_ROLE.get(device_type, DeviceRole.PRIMARY),
           DEFAULT_BRIGHTNESS_SCALE.get(device_type, 1.0),
       )
   ```

2. **Apply defaults during device config loading** when fields are absent:
   ```python
   # In config loader
   for device_cfg in config["devices"]:
       device_type = infer_device_type(device_cfg["segments"], device_cfg.get("model", ""))
       if "role" not in device_cfg:
           device_cfg["role"], _ = default_device_config(device_type)
       if "brightness_scale" not in device_cfg:
           _, device_cfg["brightness_scale"] = default_device_config(device_type)
   ```

3. **Explicit config always wins** — these are only defaults for missing fields.

### Files to Modify
- `src/dreamsync/output/roles.py` — add `DEFAULT_BRIGHTNESS_SCALE`, `DEFAULT_ROLE`, `default_device_config()`
- Device config loader — apply defaults for missing role/brightness_scale

### Unit Tests
- `python -m pytest dev/tests/test_output_roles.py -v`
- New tests:
  - `test_default_bulb_config` — `default_device_config(BULB)` → (PRIMARY, 1.0)
  - `test_default_strip_multi_config` — `default_device_config(STRIP_MULTI)` → (PRIMARY, 0.4)
  - `test_default_strip_single_config` — `default_device_config(STRIP_SINGLE)` → (ACCENT, 0.5)
  - `test_explicit_config_overrides_defaults` — device config with role="primary" and brightness_scale=0.7 → uses those, not defaults

### Completion Criteria
- Devices without explicit role/brightness_scale get sensible defaults
- Bulbs default to full brightness, strips default to reduced brightness
- Explicit user config always takes priority over defaults

---

## Summary

| # | Deliverable | Files | Effort |
|---|-------------|-------|--------|
| 1 | Per-device brightness_scale | `roles.py`, config loader, multi-adapter | Low |
| 2 | Auto-role assignment by device type | `roles.py`, config loader | Low |

Recommended order: 1 → 2 (2 depends on 1's mechanism and Issue 4's DeviceType)
