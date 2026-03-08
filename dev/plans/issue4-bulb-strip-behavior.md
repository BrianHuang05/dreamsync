# Plan: Issue 4 — Bulb vs Strip Behavior (Device-Type-Aware Rendering)

Priority: P1 | Effort: Low | Affects: bulbs look washed out, single-segment strips expose timing errors

---

## Deliverable 1: Sharper Pulse Decay for Single-Segment Devices (Bulbs)

### Prerequisites
- Read `src/dreamsync/render.py` — `SegmentRenderer._render_pulse()`, `_pulse_decay` field (default 6.0)
- Read `src/dreamsync/effects.py` — `EFFECTS["beat_pulse"].params["pulse_decay"] = 6.0`, `EFFECTS["drop_blast"].params["pulse_decay"] = 4.0`
- Understand the decay math: `brightness *= exp(-decay * dt)`. At decay=6.0 and 120 BPM (dt=0.5s between beats), brightness after one beat = `exp(-6.0 * 0.5) = 0.05` (5%). Sounds sharp, but the 200Hz render loop sends 100 frames per beat, and the exponential curve means brightness is >50% for the first 115ms, creating a visible glow that masks the next beat's attack.

### Problem
At `pulse_decay=6.0`, the brightness envelope is:
- t=0ms: 100% (beat fires)
- t=50ms: 74%
- t=100ms: 55%
- t=150ms: 41%
- t=200ms: 30%
- t=250ms: 22%
- t=500ms: 5%

The bulb stays visibly lit (>20%) for 250ms — half the beat interval at 120 BPM. This creates overlapping glow between beats, making the bulb appear to breathe smoothly rather than flash sharply. On strips, spatial motion (scroll/wave) adds visual rhythm even with this decay, but single-color bulbs have no spatial dimension to compensate.

### Solution

1. **Increase default pulse_decay** for the `beat_pulse` and `drop_blast` effect presets:
   ```python
   # src/dreamsync/effects.py
   "beat_pulse": EffectPreset(
       name="beat_pulse",
       render_mode=RenderMode.PULSE,
       color_palette=PALETTES["vivid"],
       params={"pulse_decay": 12.0},  # was 6.0
   ),
   "drop_blast": EffectPreset(
       name="drop_blast",
       render_mode=RenderMode.PULSE,
       color_palette=PALETTES["fire"],
       params={"pulse_decay": 10.0},  # was 4.0
   ),
   ```

   At decay=12.0:
   - t=0ms: 100%
   - t=50ms: 55%
   - t=100ms: 30%
   - t=150ms: 17%
   - t=200ms: 9%

   The bulb drops below 20% at ~130ms — a clean, punchy flash.

2. **Add a per-device decay override** in `SegmentRenderer.__init__()`:
   ```python
   @dataclass
   class SegmentRenderer:
       segments: int
       mode: RenderMode = RenderMode.SOLID
       mirror: bool = True
       pulse_decay_override: float | None = None  # NEW: per-device override

       _pulse_decay: float = field(default=6.0, init=False, repr=False)

       def __post_init__(self) -> None:
           ...
           if self.pulse_decay_override is not None:
               self._pulse_decay = self.pulse_decay_override
   ```

   This allows the device setup code to set sharper decay for bulbs (segments=1) and softer decay for strips.

3. **In `_render_pulse`**, prefer `params["pulse_decay"]` (already does this) but fall back to the override:
   ```python
   def _render_pulse(self, intent, dt, beat, params=None):
       decay = self._pulse_decay  # instance default (possibly overridden)
       if params and "pulse_decay" in params:
           decay = params["pulse_decay"]  # cue-level override wins
       ...
   ```
   This is already the behavior — no change needed here, just documenting the priority chain: `params > pulse_decay_override > class default`.

### Files to Modify
- `src/dreamsync/effects.py` — increase `pulse_decay` in `beat_pulse` (6→12) and `drop_blast` (4→10)
- `src/dreamsync/render.py` — add `pulse_decay_override` field to `SegmentRenderer`

### Unit Tests
- `python -m pytest dev/tests/test_render.py dev/tests/test_effects.py -v`
- New/updated tests:
  - `test_pulse_decay_12_drops_below_20pct_by_130ms` — render pulse at decay=12, verify pixel brightness < 51 (20% of 255) at t=130ms after beat
  - `test_pulse_decay_override_takes_effect` — SegmentRenderer(segments=1, pulse_decay_override=15.0), render pulse → uses 15.0 not 6.0
  - `test_pulse_params_override_wins` — renderer with pulse_decay_override=15.0, but params={"pulse_decay": 8.0} → uses 8.0
  - `test_default_decay_values_updated` — EFFECTS["beat_pulse"].params["pulse_decay"] == 12.0

### Completion Criteria
- Default pulse decay for beat_pulse is 12.0 (was 6.0)
- Bulb brightness drops below 20% within 130ms of a beat at 120 BPM
- Per-device decay override available for future device-type-aware setup

---

## Deliverable 2: Device-Type-Aware Effect Selection

### Prerequisites
- Deliverable 1 (pulse_decay_override mechanism exists)
- Read `src/dreamsync/output/roles.py` — `DeviceRole`, `transform_intent()`
- Understand device setup: devices are configured with segment count and role in `devices.yaml`

### Problem
`SegmentRenderer` and effect selection treat all devices identically. A single-segment strip (under-counter) gets SCROLL mode, which degenerates to a single pixel blinking — no spatial motion to mask timing errors. A multi-segment strip benefits from scroll/wave, while a bulb (segments=1) only benefits from pulse/breathe.

The runtime applies the same `ShowCue.render_mode` to all devices. There's no per-device effect mapping.

### Solution

1. **Add a `DeviceType` enum** and per-device effect mapping:
   ```python
   # src/dreamsync/output/roles.py

   class DeviceType(str, Enum):
       BULB = "bulb"           # single-color, no segments
       STRIP_SINGLE = "strip_single"  # strip addressed as one segment
       STRIP_MULTI = "strip_multi"    # strip with segment control

   # Map from show render_mode → device-appropriate render_mode
   DEVICE_EFFECT_MAP: dict[DeviceType, dict[str, str]] = {
       DeviceType.BULB: {
           "scroll": "pulse",     # scroll degenerates on bulbs → use pulse
           "wave": "breathe",     # wave has no spatial dimension on bulbs → breathe
           "gradient": "breathe", # gradient needs segments → breathe
           # pulse, breathe, solid pass through unchanged
       },
       DeviceType.STRIP_SINGLE: {
           "scroll": "pulse",     # single-segment scroll = pulse
           "wave": "pulse",       # single-segment wave = pulsing brightness
           "gradient": "solid",   # gradient needs ≥2 segments
       },
       DeviceType.STRIP_MULTI: {},  # all modes pass through
   }

   def adapt_render_mode(mode: str, device_type: DeviceType) -> str:
       """Map a show render_mode to the best available mode for this device type."""
       mapping = DEVICE_EFFECT_MAP.get(device_type, {})
       return mapping.get(mode, mode)
   ```

2. **Apply the mapping in the runtime** when setting render mode on cue change:
   ```python
   # src/dreamsync/show/runtime.py — _on_cue_change()
   def _on_cue_change(self, cue, t):
       ...
       # Switch render mode per device, respecting device type
       if hasattr(self._multi_adapter, "devices"):
           for _adapter, renderer, _role in self._multi_adapter.devices:
               device_type = getattr(renderer, 'device_type', None)
               if device_type is not None:
                   adapted_mode = adapt_render_mode(cue.render_mode, device_type)
                   _, render_mode = _MODE_MAP.get(adapted_mode, (EffectMode.AMBIENT, RenderMode.SOLID))
               else:
                   _, render_mode = _MODE_MAP.get(cue.render_mode, (EffectMode.AMBIENT, RenderMode.SOLID))
               renderer.mode = render_mode
   ```

3. **Add `device_type` attribute to `SegmentRenderer`**:
   ```python
   @dataclass
   class SegmentRenderer:
       segments: int
       mode: RenderMode = RenderMode.SOLID
       mirror: bool = True
       device_type: DeviceType | None = None  # NEW
       pulse_decay_override: float | None = None
   ```

4. **Auto-detect device type from segment count** during device setup:
   ```python
   def infer_device_type(segments: int, model_hint: str = "") -> DeviceType:
       if segments <= 1:
           if "bulb" in model_hint.lower() or "light" in model_hint.lower():
               return DeviceType.BULB
           return DeviceType.STRIP_SINGLE
       return DeviceType.STRIP_MULTI
   ```

### Files to Modify
- `src/dreamsync/output/roles.py` — add `DeviceType`, `DEVICE_EFFECT_MAP`, `adapt_render_mode()`, `infer_device_type()`
- `src/dreamsync/render.py` — add `device_type` field to `SegmentRenderer`
- `src/dreamsync/show/runtime.py` — use `adapt_render_mode()` in `_on_cue_change()`

### Unit Tests
- `python -m pytest dev/tests/test_output_roles.py dev/tests/test_render.py dev/tests/test_show_runtime.py -v`
- New tests:
  - `test_adapt_render_mode_bulb_scroll_to_pulse` — `adapt_render_mode("scroll", DeviceType.BULB)` → `"pulse"`
  - `test_adapt_render_mode_strip_multi_passthrough` — `adapt_render_mode("scroll", DeviceType.STRIP_MULTI)` → `"scroll"`
  - `test_adapt_render_mode_unknown_mode_passthrough` — `adapt_render_mode("solid", DeviceType.BULB)` → `"solid"`
  - `test_infer_device_type_single_segment` — `infer_device_type(1)` → `STRIP_SINGLE`
  - `test_infer_device_type_bulb_hint` — `infer_device_type(1, "H6001 Bulb")` → `BULB`
  - `test_infer_device_type_multi_segment` — `infer_device_type(15)` → `STRIP_MULTI`
  - `test_runtime_uses_adapted_mode_for_bulb` — runtime with a bulb renderer receiving "scroll" cue → renderer.mode set to PULSE

### Completion Criteria
- Bulbs never receive scroll/wave/gradient modes (mapped to pulse/breathe)
- Single-segment strips get pulse instead of degenerate scroll
- Multi-segment strips receive all modes unchanged
- Existing tests pass (devices without device_type use the original behavior)

---

## Summary

| # | Deliverable | Files | Effort |
|---|-------------|-------|--------|
| 1 | Sharper pulse decay | `effects.py`, `render.py` | Low |
| 2 | Device-type-aware effect selection | `roles.py`, `render.py`, `runtime.py` | Medium |

Recommended order: 1 (trivial constant change, immediate improvement) → 2 (structural)
