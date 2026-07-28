# Directional Strip Layout Builder Plan

## Goal

Extend the GUI room layout builder so individually addressable strip sections can be placed as a directional, physically valid chain of linked dots. Users should be able to position an entire strip by centerpoint, orient the strip directionally, and then fine-tune wrapping by moving individual section dots. Invalid layouts should be visible while editing but blocked from saving.

## Current State

The existing spatial editor already has most of the persistence foundation:

- `src/dreamsync/gui/services/device_service.py` expands strip `sections` into individual scene entries.
- `src/dreamsync/gui/controllers/spatial_controller.py` stores editable `SceneNode`s and saves coordinates back through `DeviceService`.
- `src/dreamsync/gui/widgets/spatial_canvas.py` draws draggable spatial nodes.
- `src/dreamsync/gui/main_window.py` wires the Devices / Spatial tab, coordinate spinboxes, drag axis controls, reload, and save.
- `src/dreamsync/spatial/models.py` already parses `SectionPlacement` and stores per-section coordinates.

The missing layer is chain-aware editing: grouping section nodes by physical strip, drawing adjacency, manipulating the strip as a rigid chain, validating physical spacing, and preventing invalid saves.

## Data Model

Add first-class chain metadata to editor-side section nodes, likely in `SceneNode`:

- `chain_key` or `strip_address`
- `chain_index`
- `chain_count`
- optional `chain_valid`
- optional `chain_error`

Keep the YAML format compatible with the current config shape:

- continue saving section coordinates under each device's `sections`
- continue updating root device `x`, `y`, and `z` to the average section position
- avoid a new schema unless later editor-only hints are worth persisting

Support whatever segment count the config declares. The UI can include quick presets for common 12, 15, and 20-section strips, but the underlying logic should also handle existing 25-section strips and future devices.

## Coordinate Rules

Use the existing editor nudge step as the physical adjacency unit:

- default step: `0.05`
- valid adjacent deltas:
  - `(+step, 0, 0)`
  - `(-step, 0, 0)`
  - `(0, +step, 0)`
  - `(0, -step, 0)`
  - `(0, 0, +step)`
  - `(0, 0, -step)`

A section chain is valid only when each section after the first is exactly one cardinal step from the section before it, within a small floating point tolerance.

## Validation

Add pure validation helpers in `spatial_scene.py` or a small new GUI model module:

- `group_section_chains(nodes) -> dict[str, list[SceneNode]]`
- `validate_chain(nodes, step=0.05, tolerance=...) -> ChainValidation`
- `validate_layout(nodes) -> list[ChainValidation]`
- `is_cardinal_step(a, b, step=0.05, tolerance=...) -> bool`

Validation should check:

- all expected section indices are present
- sections are ordered by `section_index`
- every adjacent pair is exactly one cardinal step apart
- all coordinates remain within `[-1.0, 1.0]`

The editor may allow temporarily invalid states, but `_save_spatial_scene()` in `main_window.py` should refuse to persist invalid chains and show a clear status message naming the strip and failing segment pair.

## Controller Changes

Extend `SpatialController` with chain-aware operations:

- `chain_for_node(key) -> list[SceneNode]`
- `move_chain(address, dx, dy, dz)`
- `generate_chain(address, center, direction, count)`
- `orient_chain(address, direction)`
- `move_section_cardinal(key, direction)`
- `validate_layout() -> list[ChainValidation]`

This supports three editing modes:

- **Move Strip:** dragging or spinbox edits move the whole selected chain by centerpoint.
- **Orient Strip:** choose a centerpoint and cardinal direction, then regenerate a straight linked chain.
- **Fine Tune Dots:** move individual section dots for wrapping while preserving or validating one-step adjacency.

## UI Changes

Update the Devices / Spatial tab in `main_window.py`.

Replace or augment the flat node list with a grouped strip-aware layout:

- physical strip rows as parents/groups
- section rows beneath each strip
- bulbs and non-section devices remain individual nodes

Add controls:

- edit mode selector: `Move Strip`, `Orient Strip`, `Fine Tune Dots`
- selected strip display
- center `x`, `y`, `z` controls for chain mode
- direction selector: `X+`, `X-`, `Y+`, `Y-`, `Z+`, `Z-`
- `Apply Line` action
- optional `Reverse Order` action for installation direction
- validation status label

Coordinate spinboxes should become mode-aware:

- in strip mode, they edit the selected chain center
- in dot mode, they edit the selected section

## Canvas Changes

Update `spatial_canvas.py` so strip sections render as linked chains:

- draw lines between consecutive section nodes before drawing dots
- highlight the whole chain when one section is selected
- draw the selected dot distinctly
- draw invalid links in a warning color
- preserve hit testing for individual section dots

Interaction behavior:

- dragging in **Move Strip** mode moves the whole chain
- dragging in **Fine Tune Dots** mode moves the selected section
- individual movement should snap to the configured step or be validated immediately
- existing Tab, Shift+Tab, axis cycling, and view cycling behavior should continue to work

The canvas currently emits `nodeDragged(key, delta_x, delta_y)`. Either keep that signal and let `main_window.py` route it by mode, or add a mode-aware drag signal if the current handler becomes too tangled.

## Directional Placement

When the user applies an initial direction:

1. Resolve the selected strip section count from loaded nodes/config.
2. Resolve the selected centerpoint.
3. Resolve the cardinal direction vector.
4. Generate positions spaced by one step.
5. Center the generated chain around the requested center.
6. Clamp or reject generated positions that exceed room bounds.
7. Select the first section or preserve the previously selected section.

For even section counts, the mathematical center falls between two sections. That is fine: compute offsets around the center so the average of all section coordinates equals the requested centerpoint.

## Persistence

Keep `DeviceService.save_scene()` focused on writing YAML. Add validation before it is called:

1. gather nodes from `spatial_controller`
2. validate all section chains
3. if invalid, show status and abort
4. if valid, call `device_service.save_scene(...)`
5. reload from disk and refresh details/canvas

This keeps save behavior compatible while ensuring impossible layouts are never written.

## Runtime Compatibility

The runtime already understands section coordinates through `SectionPlacement` and `DevicePlacement.sections`. This feature should not require mapper changes unless downstream code is found to ignore section placements in a specific path.

Add one focused test to confirm saved section coordinates still load through `parse_device_placement()` and remain available to spatial sampling.

## Tests

Add or extend tests in:

- `dev/tests/test_gui_spatial_projection.py`
- `dev/tests/test_spatial_models.py`
- possibly new `dev/tests/test_gui_spatial_chains.py`

Coverage should include:

- directional generation creates expected section coordinates
- even-count chains center correctly
- whole-chain movement preserves adjacency
- single-section movement can create an invalid chain
- invalid chain cannot be saved
- valid wrapped chain can be saved
- canvas helper orders and links sections by `section_index`
- bulbs and non-section devices still edit/save normally

## Implementation Order

1. Add pure chain grouping and validation helpers with tests.
2. Add controller chain operations and tests.
3. Add save-blocking validation in the GUI.
4. Update canvas rendering to show linked dots and invalid links.
5. Add UI controls for strip center, direction, and edit mode.
6. Add directional line generation.
7. Add wrapping/fine-tune affordances.
8. Run GUI spatial tests plus spatial model and spatial mapper tests.

