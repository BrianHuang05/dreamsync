# Coordinate-Based Spatial Mapping for Reactive + Show Playback

## Context

Today DreamSync supports **multi-device fanout**, but not true spatial placement. The runtime still follows a one-intent-many-devices model:

1. `Director` or `ShowPlaybackRuntime` produces one `LightingIntent`
2. `MultiGoveeLanAdapter.send_frame()` applies per-device role transforms
3. Each `SegmentRenderer` renders the same musical state to its own segment count

That works for mirrored or loosely coordinated setups, but it does not let us express:

- left / center / right movement
- front / center / back depth
- devices that occupy different physical regions of the room
- future spatial effects that should work for both reactive mode and compiled shows

The earlier 4-quadrant plan (`front_left`, `front_right`, `back_left`, `back_right`) would work, but it bakes room layout assumptions into enums too early. Since the likely next step is a **front-center-back x left-center-right** model, the cleaner move is to pivot now to a coordinate-based foundation.

## Decision

Use a **normalized coordinate model** for device placement, with a **3x3 grid** as the first supported runtime mapping mode.

This means:

- each device gets a spatial placement `(x, y)`
- `x` represents `left -> center -> right`
- `y` represents `front -> center -> back`
- MVP runtime mode is a 3x3 scene:
  - `front_left`, `front_center`, `front_right`
  - `center_left`, `center`, `center_right`
  - `back_left`, `back_center`, `back_right`

Why this is the right pivot:

- It supports the requested left-center-right / front-center-back model directly
- It still supports the old 4-corner concept as a subset
- It keeps future upgrades open:
  - richer geometry
  - interpolation instead of hard bucketing
  - DreamView-style room/screen semantics later

## Non-Goals for MVP

- No camera-driven or screen-capture-driven mapping
- No 2-D visual room editor
- No TV-edge calibration UI
- No automatic spatial discovery of devices
- No attempt to clone Govee Movie-Watching DreamView exactly
- No compiler-generated spatial metadata in the first pass

## Core Design Shift

The new foundation is:

```text
LightingIntent or ShowCue
    -> SpatialMapper
    -> SpatialScene (3x3 grid over normalized space)
    -> per-device placement lookup / sampling
    -> renderer.render()
    -> adapter.send_frame()
```

Instead of assigning devices to one of four named quadrants, each device gets a placement in normalized space. The MVP will still use **bucketed grid cells**, but the underlying config and runtime APIs should be coordinate-friendly from day one.

## Files to Create

| File | Purpose |
|------|---------|
| `src/dreamsync/spatial/models.py` | Coordinate models, grid cell enums/types, placement parsing helpers |
| `src/dreamsync/spatial/mapper.py` | `SpatialMapper` that expands one intent/cue into a 3x3 spatial scene |
| `src/dreamsync/spatial/grid.py` | Grid quantization, cell lookup, optional interpolation helpers |
| `src/dreamsync/spatial/patterns.py` | Spatial patterns: horizontal sweep, depth push, center bloom, diagonal bias |
| `dev/tests/test_spatial_models.py` | Placement/config validation tests |
| `dev/tests/test_spatial_grid.py` | Grid lookup and coordinate bucketing tests |
| `dev/tests/test_spatial_mapper.py` | Spatial scene generation tests |
| `dev/tests/test_spatial_runtime.py` | Adapter/runtime integration tests |

## Files to Modify

| File | Change |
|------|--------|
| `src/dreamsync/output/auto_detect.py` | Extend `DeviceConfig`; parse coordinate-based placement from YAML |
| `src/dreamsync/output/govee_lan.py` | Placement-aware multi-device fanout and spatial scene routing |
| `src/dreamsync/output/null_adapter.py` | Maintain compatibility with placement-aware device tuples |
| `src/dreamsync/live.py` | Reactive path uses `SpatialMapper` when enabled |
| `src/dreamsync/show/runtime.py` | Show playback uses `SpatialMapper` when enabled |
| `src/dreamsync/config_watcher.py` | Preserve coordinate metadata during reload |
| `src/dreamsync/device_health.py` | Tolerate expanded device tuple shape |
| `src/dreamsync/cli.py` | Add spatial mode flags, validation, and mapper construction |
| `devices-template.yaml` | Document 3x3 placement examples |
| `README.md` | Add spatial setup and tuning examples |

## Spatial Model

### Normalized coordinates

Use normalized room coordinates:

- `x = -1.0` far left
- `x =  0.0` center
- `x = +1.0` far right
- `y = -1.0` front
- `y =  0.0` center
- `y = +1.0` back

This makes 3x3 placement straightforward while leaving room for future finer positioning.

### Device placement

```python
@dataclass(frozen=True)
class DevicePlacement:
    x: float                  # -1.0..1.0
    y: float                  # -1.0..1.0
    orientation: Literal["left_to_right", "right_to_left"] = "left_to_right"
    weight: float = 1.0
    enabled: bool = True
```

### Grid cells

For MVP, coordinates are resolved into a 3x3 grid:

```python
class GridCell(str, Enum):
    FRONT_LEFT = "front_left"
    FRONT_CENTER = "front_center"
    FRONT_RIGHT = "front_right"
    CENTER_LEFT = "center_left"
    CENTER = "center"
    CENTER_RIGHT = "center_right"
    BACK_LEFT = "back_left"
    BACK_CENTER = "back_center"
    BACK_RIGHT = "back_right"
```

### Spatial scene

The mapper should emit a complete 3x3 scene:

```python
@dataclass(frozen=True)
class SpatialCellState:
    intent: LightingIntent
    emphasis: float
    phase_offset: float = 0.0
    color_shift: float = 0.0
    params: dict[str, Any] = field(default_factory=dict)


SpatialScene = dict[GridCell, SpatialCellState]
```

## YAML Config Shape

Prefer coordinates as the source of truth, but allow named aliases for usability.

### Friendly YAML form

```yaml
devices:
  - name: "Desk Left"
    address: "10.0.0.100"
    type: lan
    segments: 15
    transport: ptreal
    role: primary
    x_position: left
    y_position: front
    orientation: left_to_right
    weight: 1.0

  - name: "Monitor Bar"
    address: "10.0.0.101"
    type: lan
    segments: 20
    transport: razer
    role: primary
    x_position: center
    y_position: front
    weight: 1.2

  - name: "Rear Strip"
    address: "10.0.0.102"
    type: lan
    segments: 7
    transport: ptreal
    role: accent
    x_position: right
    y_position: back
    orientation: right_to_left
    weight: 0.8
```

### Advanced YAML form

```yaml
devices:
  - name: "Rear Lamp"
    address: "AA:BB:CC:DD:EE:FF"
    type: ble
    protocol: bulb
    role: accent
    x: 0.25
    y: 0.9
```

### Parsing rules

- allow either:
  - `x_position` / `y_position`
  - or numeric `x` / `y`
- aliases:
  - `left=-1.0`, `center=0.0`, `right=1.0`
  - `front=-1.0`, `center=0.0`, `back=1.0`
- `orientation` defaults to `left_to_right`
- `weight` defaults to `1.0`
- spatial mode remains opt-in
- legacy configs without placement still work when spatial mode is off

## Mapping Behavior

The MVP should be deterministic and readable, not magical.

Suggested defaults by effect type:

| Effect / render mode | Spatial behavior |
|----------------------|------------------|
| `solid` | Balanced fill, slight center emphasis |
| `pulse` | Front-center and center cells hit strongest; rear slightly delayed/dimmer |
| `breathe` | Broad room wash with front/back phase separation |
| `scroll` | Horizontal sweep by default, center column acts as bridge rather than dead stop |
| `wave` | Left-right mirrored phase with center stabilization |
| `gradient` | Horizontal or depth gradient depending on cue params |

Optional `ShowCue.params` or reactive params:

```python
{
    "spatial_axis": "horizontal" | "depth" | "diagonal" | "radial",
    "spatial_focus": "left" | "center" | "right" | "front" | "back" | "balanced",
    "center_gain": 0.15,
    "rear_delay_ms": 80,
    "color_spread": 0.10,
}
```

These remain optional. Existing shows and reactive flows should map cleanly without them.

## Detailed Execution Plan

### Step 0: Baseline inventory and regression guardrails

**Prerequisites:** None.

**Deliverables:**

- Working notes capturing:
  - current device tuple shape(s)
  - all code paths that iterate `multi_adapter.devices`
  - all commands that must remain backward compatible
- Regression checklist for:
  - single-device `govee-live`
  - multi-device `session`
  - `play`
  - `compile-and-play`
  - streaming playback

**Implementation details:**

- Inventory tuple-shape assumptions before changing adapter data structures
- Confirm how `mirror` is currently used and where orientation will interact with it
- Freeze MVP invariants:
  - spatial mode is opt-in
  - legacy configs require zero edits
  - existing `.show.json` files remain valid
  - BLE followers remain global/base-intent unless explicitly changed later

**Completion criteria:**

- We know every place that will break if tuple shape changes
- We have a fixed regression checklist to run after each major step

**Tests:**

1. Manual: inspect `govee-live`, `session`, `play`, and `compile-and-play` help output
2. Manual: capture one legacy config behavior baseline
3. Manual: list current tests covering multi-device output and playback

**Verify:**

```bash
python -m dreamsync govee-live --help
python -m dreamsync session --help
python -m dreamsync play --help
python -m dreamsync compile-and-play --help
```

---

### Step 1: Coordinate placement models and config schema

**Prerequisites:**

- Step 0 complete
- Agreement on normalized coordinate conventions

**Deliverables:**

- `src/dreamsync/spatial/models.py`:
  - `DevicePlacement`
  - `GridCell`
  - placement parsing helpers
- `DeviceConfig` extended in `src/dreamsync/output/auto_detect.py`
- YAML parsing for:
  - `x_position` / `y_position`
  - numeric `x` / `y`
  - `orientation`
  - `weight`
  - optional `enabled`
- `devices-template.yaml` updated with 3x3 examples
- `dev/tests/test_spatial_models.py`

**Implementation details:**

- Add optional config fields:
  - `x_position`
  - `y_position`
  - `x`
  - `y`
  - `orientation`
  - `weight`
  - `enabled`
- Validation:
  - numeric `x`, `y` must be in `[-1.0, 1.0]`
  - aliases must be from the approved set
  - `weight > 0`
  - `orientation` valid
- Prefer typed placement objects instead of raw strings beyond the config layer
- Represent unplaced devices as `placement=None`

**Completion criteria:**

- Coordinate-based placement can be parsed and validated
- Legacy configs load unchanged when spatial mode is off
- Friendly aliases and numeric coordinates both work

**Tests (14):**

1. `test_device_config_without_spatial_fields_still_valid`
2. `test_left_front_alias_parses_to_negative_coordinates`
3. `test_center_alias_parses_to_zero`
4. `test_right_back_alias_parses_to_positive_coordinates`
5. `test_numeric_coordinates_parse`
6. `test_invalid_x_alias_rejected`
7. `test_invalid_y_alias_rejected`
8. `test_out_of_range_x_rejected`
9. `test_out_of_range_y_rejected`
10. `test_default_orientation_applied`
11. `test_invalid_orientation_rejected`
12. `test_default_weight_applied`
13. `test_nonpositive_weight_rejected`
14. `test_template_examples_load`

**Verify:**

```bash
python -m pytest dev/tests/test_spatial_models.py -v
```

---

### Step 2: Grid quantization and cell lookup

**Prerequisites:**

- Step 1 complete

**Deliverables:**

- `src/dreamsync/spatial/grid.py`
- Helpers for:
  - coordinate -> `GridCell`
  - optional nearest-cell and center-biased bucketing
  - pretty-printing placements for validation/debugging
- `dev/tests/test_spatial_grid.py`

**Implementation details:**

- Define thresholds for 3-column and 3-row bucketing
- Suggested MVP thresholds:
  - `x < -0.33` -> left
  - `-0.33 <= x <= 0.33` -> center
  - `x > 0.33` -> right
  - same idea for `y`
- Keep this logic isolated so future interpolation can replace it
- Add helper to format a placement table:
  - device name
  - resolved coordinates
  - resolved grid cell
  - orientation
  - role

**Completion criteria:**

- Every placed device resolves deterministically to one 3x3 cell
- The bucketing logic is isolated from adapter/runtime code

**Tests (12):**

1. `test_far_left_maps_left_column`
2. `test_center_x_maps_center_column`
3. `test_far_right_maps_right_column`
4. `test_front_y_maps_front_row`
5. `test_center_y_maps_center_row`
6. `test_back_y_maps_back_row`
7. `test_front_center_maps_front_center`
8. `test_center_right_maps_center_right`
9. `test_back_left_maps_back_left`
10. `test_boundary_values_bucket_stably`
11. `test_unplaced_device_has_no_cell`
12. `test_format_mapping_table_includes_resolved_cell`

**Verify:**

```bash
python -m pytest dev/tests/test_spatial_grid.py -v
```

---

### Step 3: `SpatialMapper` 3x3 scene generation

**Prerequisites:**

- Steps 1 and 2 complete

**Deliverables:**

- `src/dreamsync/spatial/mapper.py`
- `src/dreamsync/spatial/patterns.py`
- 3x3 `SpatialScene` output
- `dev/tests/test_spatial_mapper.py`

**Implementation details:**

- `SpatialMapper` should be pure and deterministic
- Public entry points:
  - `map_reactive()`
  - `map_show_cue()`
- Pattern helpers should cover:
  - balanced room wash
  - horizontal sweep
  - depth push
  - center bloom
  - diagonal bias
- Center row/column must be deliberate, not an afterthought:
  - center should act as blend/bridge for motion-heavy effects
  - center can act as focal anchor for pulse/solid effects
- Keep spatial params optional

**Completion criteria:**

- Mapper returns all 9 cells for every call
- Center row/column behavior is explicit and tested
- No adapter or renderer mutation occurs inside the mapper

**Tests (16):**

1. `test_mapper_returns_all_nine_cells`
2. `test_solid_mode_center_emphasis_is_small_and_stable`
3. `test_pulse_front_center_bias_applied`
4. `test_breathe_front_back_phase_split`
5. `test_scroll_defaults_to_horizontal_axis`
6. `test_scroll_depth_axis_override_respected`
7. `test_wave_left_right_phase_pattern`
8. `test_gradient_depth_bias_respected`
9. `test_center_column_bridges_horizontal_motion`
10. `test_center_row_bridges_depth_motion`
11. `test_disabled_mapper_returns_balanced_scene`
12. `test_show_cue_without_spatial_params_still_maps`
13. `test_show_cue_spatial_focus_center_respected`
14. `test_invalid_spatial_axis_falls_back_cleanly`
15. `test_mapping_is_deterministic`
16. `test_color_spread_applies_small_variation_without_palette_breakage`

**Verify:**

```bash
python -m pytest dev/tests/test_spatial_mapper.py -v
```

---

### Step 4: Placement-aware multi-device adapter

**Prerequisites:**

- Steps 1-3 complete

**Deliverables:**

- `MultiGoveeLanAdapter` updated to store placement metadata
- New spatial-scene send path
- Compatibility preserved for existing tuple forms
- Placement-aware updates in:
  - `config_watcher.py`
  - `device_health.py`
  - `show/runtime.py` loops
  - any helper loops in `cli.py` / `live.py`
- `src/dreamsync/output/null_adapter.py` remains compatible

**Implementation details:**

- Normalize device tuples centrally:

```python
(adapter, renderer, role, brightness_scale, placement)
```

- Preserve support for legacy tuple inputs
- Add internal path:
  - `send_frame()` for legacy mode
  - `send_spatial_scene()` or `_send_spatial_scene()` for spatial mode
- Each device:
  - resolves placement -> `GridCell`
  - receives that cell's `SpatialCellState`
  - still applies role + brightness transforms afterward
- Orientation handling remains local to render time

**Completion criteria:**

- Different devices can receive different cell states in the same tick
- Legacy fanout behavior remains unchanged when spatial mode is off
- Expanded tuple shape does not break watchers/health/playback loops

**Tests (14):**

1. `test_legacy_four_tuple_still_normalizes`
2. `test_five_tuple_with_placement_normalizes`
3. `test_spatial_off_uses_legacy_send_path`
4. `test_spatial_on_routes_by_resolved_grid_cell`
5. `test_two_devices_in_same_cell_receive_same_cell_state`
6. `test_center_device_receives_center_cell_state`
7. `test_unplaced_device_uses_legacy_or_fallback_behavior`
8. `test_orientation_flip_applied_to_renderer`
9. `test_role_transform_still_applies_after_spatial_lookup`
10. `test_brightness_scale_still_applies_after_spatial_lookup`
11. `test_ble_followers_still_receive_base_intent`
12. `test_null_adapter_remains_compatible`
13. `test_any_sent_true_if_any_device_sent`
14. `test_replace_devices_preserves_placement_metadata`

**Verify:**

```bash
python -m pytest dev/tests/test_spatial_runtime.py -v -k multi
python -m pytest tests/ -v
python -m pytest dev/tests/ -v
```

---

### Step 5: CLI surface and mapping validation

**Prerequisites:**

- Steps 1-4 complete

**Deliverables:**

- CLI flags in `src/dreamsync/cli.py`:
  - `--spatial-map`
  - `--front-back-separation`
  - `--left-right-separation`
  - `--center-gain`
  - `--rear-delay-ms`
  - `--color-spread`
  - `--validate-mapping`
- Shared mapper-construction helper
- Mapping validation table output

**Implementation details:**

- Commands:
  - `govee-live`
  - `session`
  - `play`
  - `compile-and-play`
- `--spatial-map` values:
  - `off`
  - `grid_3x3`
- `--validate-mapping` should:
  - parse config
  - resolve coordinates
  - resolve grid cells
  - print table
  - exit before audio/network work

**Completion criteria:**

- Users can enable or inspect spatial mapping from the CLI
- Help text clearly describes the 3x3 model and the fact that it is opt-in

**Tests (8):**

1. `test_cli_reactive_spatial_flag_parses`
2. `test_cli_playback_spatial_flag_parses`
3. `test_spatial_flags_default_to_off`
4. `test_validate_mapping_prints_coordinates_and_cells`
5. `test_validate_mapping_rejects_invalid_coordinate_config`
6. `test_center_gain_passes_to_mapper`
7. `test_spatial_tuning_values_pass_to_mapper`
8. `test_grid_3x3_mode_is_constructed_correctly`

**Verify:**

```bash
python -m dreamsync govee-live --help
python -m dreamsync session --help
python -m dreamsync play --help
python -m dreamsync session --config devices.yaml --spatial-map grid_3x3 --validate-mapping
```

---

### Step 6: Reactive-mode integration

**Prerequisites:**

- Steps 1-5 complete

**Deliverables:**

- `live.py` integration so reactive rendering uses spatial scenes when enabled
- `session` integration for long-running config-based sessions
- Debug output showing resolved placements and active spatial mode

**Implementation details:**

- Do not change BPM detection, beat detection, mood classification, or `Director`
- Spatial mapping is applied after base `LightingIntent` construction
- Reuse one `SpatialMapper` instance per run
- Ensure config reload preserves/replaces placement metadata cleanly
- Keep center-aware behavior visible in reactive patterns:
  - horizontal movement should sweep through center devices
  - front/back emphasis should use center row sensibly

**Completion criteria:**

- `govee-live` and `session` behave spatially when `--spatial-map grid_3x3` is enabled
- Reactive output is unchanged when spatial mode is off
- Config reload and health monitoring keep working

**Tests (8):**

1. `test_live_path_without_mapper_unchanged`
2. `test_live_path_with_mapper_routes_spatial_scene`
3. `test_session_path_with_mapper_routes_spatial_scene`
4. `test_center_device_participates_in_horizontal_motion`
5. `test_config_reload_preserves_placement`
6. `test_device_health_tolerates_placement_tuples`
7. Manual: center device visibly bridges left-right wave/scroll
8. Manual: front/back depth effect is noticeable without overwhelming center row

**Verify:**

```bash
python -m dreamsync session --config devices.yaml --spatial-map grid_3x3 --validate-mapping
python -m dreamsync govee-live --config devices.yaml --spatial-map grid_3x3 --debug-mood
python -m dreamsync session --config devices.yaml --spatial-map grid_3x3 --debug-mood
```

---

### Step 7: Show-playback integration

**Prerequisites:**

- Steps 1-6 complete

**Deliverables:**

- `ShowPlaybackRuntime` integration with `SpatialMapper.map_show_cue()`
- Playback entry points continue to work:
  - `play`
  - `compile-and-play`
  - playlist playback
  - streaming pipeline consumer

**Implementation details:**

- Keep `ShowTimeline` / `ShowCue` backward compatible
- Spatial mapping wraps existing cue intent generation
- Optional cue params can refine:
  - horizontal vs depth movement
  - center emphasis
  - diagonal bias
- Existing shows must still play if they contain no spatial params

**Completion criteria:**

- Existing show JSON files play unchanged
- Spatial playback uses the 3x3 scene when enabled
- Center devices contribute meaningfully instead of being ignored

**Tests (12):**

1. `test_show_runtime_without_mapper_unchanged`
2. `test_show_runtime_with_mapper_sends_spatial_scene`
3. `test_existing_show_json_loads_without_spatial_fields`
4. `test_cue_params_horizontal_axis_used_when_present`
5. `test_cue_params_depth_axis_used_when_present`
6. `test_cue_params_center_focus_used_when_present`
7. `test_invalid_spatial_param_value_falls_back`
8. `test_playlist_playback_uses_spatial_mapper`
9. `test_streaming_consumer_uses_spatial_mapper`
10. `test_color_cycle_still_works_with_spatial_mapping`
11. `test_transition_fades_still_work_with_spatial_mapping`
12. `test_stats_collection_unchanged`

**Verify:**

```bash
python -m dreamsync play path/to/song.mp3 --config devices.yaml --spatial-map grid_3x3 --debug
python -m dreamsync compile-and-play path/to/song.mp3 --config devices.yaml --spatial-map grid_3x3 --debug
```

---

### Step 8: Documentation, examples, and end-to-end validation

**Prerequisites:**

- Steps 1-7 complete
- At least one reactive and one show-playback spatial run completed successfully

**Deliverables:**

- `devices-template.yaml` examples for:
  - 2-device left/right front setup
  - 3-device front-left/front-center/front-right setup
  - 5-device room with front, center, and back placements
- README section documenting:
  - coordinate model
  - alias model
  - 3x3 grid mode
  - orientation
  - tuning knobs
  - validation workflow
- End-to-end validation checklist

**Implementation details:**

- Document the MVP honestly:
  - coordinate-based foundation
  - 3x3 runtime mode
  - not screen-aware DreamView
- Include at least one setup where center placement matters
- Document tuning:
  - `front-back-separation`
  - `left-right-separation`
  - `center-gain`
  - `rear-delay-ms`
  - `color-spread`

**Completion criteria:**

- A user can configure a left-center-right / front-center-back setup using docs alone
- Manual validation passes on:
  - one 2-device setup
  - one 3-device front row setup
  - one mixed front/center/back setup
  - one legacy non-spatial setup

**Tests:**

1. Manual: two-device front-left/front-right pair
2. Manual: three-device front-left/front-center/front-right row
3. Manual: mixed hardware front/center/back room
4. Manual: legacy config with spatial mode off
5. Manual: `session --pipeline --spatial-map grid_3x3`

**Verify:**

```bash
python -m dreamsync session --config devices.yaml --spatial-map grid_3x3 --validate-mapping
python -m dreamsync session --config devices.yaml --pipeline --capture --spatial-map grid_3x3 --debug-mood
```

## Future Work

### 1. Interpolated sampling instead of hard cell bucketing

Once coordinates are established, the next upgrade is to sample scenes smoothly instead of picking a single 3x3 cell. That would make near-center placements behave more naturally.

### 2. Richer placement geometry

Extend `DevicePlacement` with spans/extent:

```python
DevicePlacement(
    x=0.1,
    y=-0.8,
    width=0.3,
    height=0.1,
    orientation="left_to_right",
)
```

This would support devices that occupy a region instead of a single point.

### 3. DreamView-style screen / room semantics

Because the model is coordinate-based, a future DreamView-like system can build on the same placement layer with richer source semantics rather than starting over.

### 4. Compiler-emitted spatial metadata

Later the compiler can become spatially aware and emit cue params such as:

- chorus expands from center outward
- build sweeps front to back
- fill rotates diagonally across the room

## Summary

| Step | Deliverable | Tests | Dependencies |
|------|-------------|-------|-------------|
| 0 | Baseline inventory and regression guardrails | 3 manual | None |
| 1 | Coordinate placement models + config schema | 14 automated | Step 0 |
| 2 | Grid quantization + cell lookup | 12 automated | Step 1 |
| 3 | `SpatialMapper` 3x3 scene generation | 16 automated | Steps 1-2 |
| 4 | Placement-aware multi-device adapter | 14 automated | Steps 1-3 |
| 5 | CLI surface + mapping validation | 8 automated | Steps 1-4 |
| 6 | Reactive-mode integration | 6 automated + 2 manual | Steps 1-5 |
| 7 | Show-playback integration | 12 automated | Steps 1-6 |
| 8 | Docs + end-to-end validation | 5 manual | Steps 1-7 |
| **Total** | | **66 automated + 10 manual** | |
