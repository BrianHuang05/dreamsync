# Group Control Actions Implementation Plan

## Goal

Add first-class logical groups to DreamSync so devices and independently
addressable strip sections can participate in one or more named groups, and so
compiled shows, saved-show playback, baked playback, simulation, and Live
Reactive mode can target or enable/disable those groups consistently.

Examples include:

- `Left Group`
- `Top Group`
- `Strip Group`
- `Group A`
- `Group B`

Groups are an authoring and routing concept. Spatial coordinates continue to
describe where a node is located; groups describe how the node should be used.

## Status

Planned. No implementation has started.

## Desired End State

The completed system should support all of the following:

1. Create, rename, color-code, and delete groups from the Room Layout screen.
2. Assign a whole device to multiple groups.
3. Assign individual sections of the same strip to different groups.
4. Allow a section to belong to multiple groups.
5. Target compiled cue effects and effect layers to one or more groups.
6. Toggle groups on or off during compiled/saved-show playback.
7. Toggle groups on or off during Live Reactive playback.
8. Let Live Reactive choose and combine groups using musical structure,
   spatial placement, device type, and recent group usage.
9. Preserve equivalent behavior in hardware output, GUI simulation, preview
   mirroring, diagnostics, and baked-frame playback.
10. Load all existing device configs, profiles, and saved shows without
    behavior changes.

## Non-Goals

- Do not replace spatial coordinates or spatial presets with groups.
- Do not infer group membership from display names.
- Do not require users to create groups for existing configurations.
- Do not introduce true independent multi-renderer compositing in the first
  phase.
- Do not make the compiler depend on a particular physical room layout unless
  group-aware authoring is explicitly enabled.
- Do not treat device roles such as `primary` and `accent` as group
  memberships. Roles remain device behavior transforms.
- Do not silently black out nodes because one of several memberships is
  disabled.

## Current Architecture and Feasibility

The feature is feasible without replacing the output architecture:

- `DevicePlacement` and `SectionPlacement` already model device and section
  nodes independently.
- The Room Layout editor already expands multi-section strips into individually
  editable scene nodes.
- The continuous spatial output path already samples and colors each section
  independently.
- compiled `ShowCue.params` already carries extensible routing and spatial
  metadata through saved-show serialization and playback.
- Live Reactive and compiled playback both reach the adapter through
  `send_frame`.
- baked artifacts already identify nodes by stable device and section keys,
  including `address#section:index`.
- the runtime control bus already supports thread-safe changes during active
  saved-show and Reactive sessions.

The main work is therefore:

1. group definition and membership persistence;
2. group-aware editor state and UX;
3. a shared node-level routing/composition policy;
4. compiled and Reactive authoring/control surfaces;
5. diagnostics, migration, and tests.

## Terminology

### Group

A stable logical entity with an immutable ID and editable display name.

### Node

The smallest routable output unit:

- a non-segmented device; or
- one addressable section of a segmented strip.

### Direct membership

A group assigned directly to a device or section in the device config.

### Inherited membership

A device-level group inherited by a section belonging to that device.

### Effective membership

The final set of groups used by routing after inheritance and explicit section
overrides are resolved.

### Target group

A group selected by a cue, scene layer, or Live Reactive action.

### Group contribution

The color/intensity/effect contribution produced specifically for one targeted
group. A node may receive several contributions because it can belong to
several groups.

### Group enable state

A runtime master state controlling whether a group's contributions are
eligible to affect output.

## Required Semantic Decisions

These rules should be implemented consistently across compiler, playback,
Reactive, simulation, and baked output.

### Stable identity

- Group IDs are machine-facing slugs and must be unique case-insensitively.
- Display names are editable and do not appear in cue routing references.
- Renaming a group must not invalidate saved cues or memberships.
- Deleting a referenced group requires an explicit confirmation and reports
  all affected memberships and authoring references that can be found.

### Implicit groups

Every routable node participates in the reserved `all` group.

The `all` group:

- is not stored as ordinary membership;
- cannot be renamed or deleted;
- provides backward-compatible global behavior;
- may be exposed as a read-only row in group selectors.

Optional computed selectors such as `all-strips` should not be introduced as
magic group IDs in the first implementation. Users can create an ordinary
`strips` group explicitly.

### Membership inheritance

Recommended schema:

- device `groups` are inherited by all of its sections;
- section `groups` add direct memberships;
- section `exclude_groups` can remove inherited memberships;
- effective membership is:

  `{"all"} | device.groups | section.groups - section.exclude_groups`

This supports fast whole-device assignment while still allowing a strip to be
split into arbitrary logical regions.

### Overlapping groups

Disabling one group suppresses only that group's contribution. It must not mute
a node that remains active through another enabled targeted group.

Example:

- section 4 belongs to `left`, `strips`, and `group-a`;
- `group-a` is disabled;
- an effect targeting `left` still reaches section 4;
- a contribution targeting only `group-a` does not.

An unconditional physical-node mute remains the responsibility of the existing
device/section `enabled` state, not group state.

### No-target behavior

For backward compatibility, a cue or layer without group-routing metadata
targets `all`.

### Target matching

The initial matcher should use `any` semantics:

- `target_groups: [left, strips]` targets nodes in either group.

An optional `target_match: all` field may be supported for intersection
selection:

- `target_groups: [left, strips]`
- `target_match: all`
- targets only nodes that belong to both groups.

Exclusions apply after positive selection:

- `exclude_groups: [accent]`

### Disabled-group behavior

Group state gates group contributions before node composition.

- If a node has at least one eligible contribution, compose and send it.
- If every contribution to a node is ineligible, use the cue's explicit
  untargeted behavior.
- For a group-specific accent over a global base, disabling the accent group
  reveals the global base.
- For a group-exclusive cue with `untargeted_behavior: blackout`, disabling
  its only target blacks out those nodes.

### Untargeted behavior

Support an explicit enum rather than relying on implicit intensity:

- `preserve_base` — default for layers and overlays;
- `blackout` — default for exclusive whole-cue group actions;
- `ignore` — leave the prior node frame unchanged only for specialized manual
  control paths.

`ignore` should not be used by ordinary compiled or Reactive frames because it
can create stale hardware output.

### Composition order

Initial deterministic order:

1. global/base contribution;
2. eligible group contributions sorted by ascending priority;
3. blend by each contribution's declared mode;
4. apply physical node `enabled`;
5. clamp RGB and submit to the adapter.

Supported initial blend modes:

- `replace`
- `max`
- `add`
- `mix`

Tie-break equal priorities by stable source order, never by set/dict iteration.

## Proposed Configuration Schema

Example:

```yaml
groups:
  - id: left
    name: Left Group
    color: "#4f7fda"
    enabled_by_default: true
    tags: [lateral, primary]
  - id: top
    name: Top Group
    color: "#c084fc"
    enabled_by_default: true
    tags: [vertical, accent]
  - id: strips
    name: Strip Group
    color: "#22c55e"
    enabled_by_default: true
    tags: [strip]
  - id: group-a
    name: Group A
    color: "#f59e0b"
    enabled_by_default: true
    tags: [alternating]
  - id: group-b
    name: Group B
    color: "#06b6d4"
    enabled_by_default: true
    tags: [alternating]

devices:
  - name: Counter Strip
    address: 10.0.0.20
    type: lan
    segments: 4
    groups: [strips, group-a]
    sections:
      - index: 0
        x: -0.80
        y: 0.30
        z: 0.00
        groups: [left]
      - index: 1
        x: -0.75
        y: 0.30
        z: 0.00
        groups: [left]
      - index: 2
        x: -0.70
        y: 0.30
        z: 0.00
        groups: [group-b]
        exclude_groups: [group-a]
      - index: 3
        x: -0.65
        y: 0.30
        z: 0.00
        groups: [group-b]
        exclude_groups: [group-a]
```

### Group definition fields

Required:

- `id`
- `name`

Optional:

- `color`
- `enabled_by_default`
- `tags`
- future policy metadata

Unknown fields should be preserved by editor saves where practical.

### Validation

Reject:

- duplicate IDs, ignoring case;
- reserved ID redefinition, including `all`;
- empty or invalid IDs;
- unknown membership references;
- the same group in a section's `groups` and `exclude_groups`;
- malformed group lists;
- non-boolean `enabled_by_default`.

Warn, but permit:

- a group with no members;
- two groups with the same display name;
- a section exclusion for a group not inherited by its device;
- a group whose members are all physically disabled.

## Proposed Runtime Models

Introduce immutable group-domain models in a focused module, for example
`dreamsync/groups/models.py`:

- `GroupDefinition`
- `GroupMembership`
- `GroupCatalog`
- `GroupState`
- `GroupSelector`
- `GroupContribution`

`DevicePlacement` and `SectionPlacement` should carry direct membership data or
reference a separately resolved node-routing table. Prefer the design that
keeps physical placement immutable and avoids repeated per-frame string-set
construction.

Recommended runtime representation:

```text
GroupCatalog
  definitions_by_id
  bit_index_by_id

NodeRoute
  node_key
  effective_group_mask
  physically_enabled

GroupRuntimeState
  enabled_group_mask
  revision
```

Use bit masks or precomputed frozen sets in the render loop. Group membership
and catalog validation occur during config load, not once per frame.

## Proposed Cue and Layer Schema

The group routing surface should be available on both a base cue and individual
scene layers.

Example whole-cue target:

```json
{
  "params": {
    "target_groups": ["group-a"],
    "target_match": "any",
    "exclude_groups": [],
    "untargeted_behavior": "blackout"
  }
}
```

Example layered target:

```json
{
  "params": {
    "scene_layers": [
      {
        "instrument": "bass",
        "target_groups": ["left", "strips"],
        "target_match": "all",
        "spatial_preset": "flash_floor_only",
        "color_bias": "#ff8800",
        "layer_weight": 0.8,
        "layer_priority": 20,
        "layer_blend": "max"
      }
    ]
  }
}
```

Group metadata must survive:

- profile parsing;
- treatment generation;
- compiler assembly;
- show patches;
- `ShowTimeline.to_dict/from_dict`;
- prepared spatial cue caching;
- runtime control application;
- baked-frame export metadata.

Because cue params are already serialized generically, saved-show schema
versioning may not be required for the first addition. Validation and
normalization should still be centralized.

## Proposed Runtime Control Surface

Extend `RuntimeControlState` with group controls:

- `disabled_groups: tuple[str, ...]`
- optionally `enabled_groups: tuple[str, ...]` for explicit snapshots
- `solo_groups: tuple[str, ...]`

Recommended precedence:

1. physical node `enabled` is final;
2. `solo_groups`, when non-empty, makes non-solo group contributions
   ineligible;
3. `disabled_groups` suppresses matching contributions;
4. all other catalog groups use `enabled_by_default`;
5. unknown runtime group IDs are rejected with a user-visible error.

Add focused bus operations:

- `set_group_enabled(group_id, enabled)`
- `set_disabled_groups(group_ids)`
- `set_solo_groups(group_ids)`
- `clear_group_overrides()`

Do not overload general route mutes. Group state should remain independently
observable and independently clearable.

## Phase 1 — Domain Model, Config Parsing, and Validation

### Work

1. Add group definition, selector, and resolved node-route models.
2. Extend device config loading to parse top-level group definitions.
3. Parse device and section `groups` and section `exclude_groups`.
4. Resolve effective membership once per config load.
5. Preserve memberships and definitions during config serialization.
6. Add reserved `all` membership to every routable node.
7. Keep all existing configs valid with a synthesized empty catalog plus
   `all`.
8. Add validation with actionable path-based error messages.
9. Ensure discovery/upsert operations preserve existing top-level groups and
   membership fields.

### Likely files

- `src/dreamsync/output/auto_detect.py`
- `src/dreamsync/spatial/models.py`
- `src/dreamsync/gui/services/device_service.py`
- new `src/dreamsync/groups/` package
- `src/dreamsync/gui/services/device_discovery_service.py`

### Tests

- parse and serialize group definitions;
- device membership round-trip;
- section direct membership round-trip;
- inherited membership;
- inherited membership exclusion;
- multiple group membership;
- reserved `all`;
- legacy config compatibility;
- validation for duplicates, unknown references, and malformed lists;
- discovery/upsert preserves unrelated group data;
- Room Layout position save preserves group data byte-semantically where
  possible.

### Acceptance criteria

- Existing device config tests pass unchanged.
- Loading and saving an old config does not add required group boilerplate.
- A section can resolve to different effective groups than adjacent sections
  on the same strip.
- Config save operations do not erase group definitions or memberships.

## Phase 2 — Shared Group Routing and Output Gating

### Work

1. Build a shared selector matcher for node routes.
2. Normalize cue- and layer-level group metadata.
3. Carry resolved node routes into LAN, BLE, simulation, and mirror adapters.
4. Apply group eligibility per rendered section.
5. Implement deterministic composition and untargeted behavior.
6. Enforce group state in one shared final-output path where possible.
7. Ensure non-segmented bulbs and single-segment strips behave as one node.
8. Include group information in final-frame diagnostics.
9. Preserve the existing output path when there are no group targets or
   runtime overrides.

### Important constraint

Do not use only device-level filtering. A physical strip can contain sections
with different effective memberships, so eligibility must be evaluated at the
node/section level before the final segment color list is sent.

### Likely files

- `src/dreamsync/output/govee_lan.py`
- `src/dreamsync/output/govee_ble.py`
- `src/dreamsync/output/null_adapter.py`
- `src/dreamsync/spatial/mapper.py`
- new `src/dreamsync/groups/routing.py`

### Diagnostics additions

Per node:

- `effective_groups`
- `matched_target_groups`
- `eligible_contributions`
- `suppressed_contributions`
- `final_group_state`

Per frame:

- group runtime revision;
- enabled, disabled, and solo groups;
- count of nodes suppressed by group routing;
- count of overlapping nodes receiving multiple contributions.

Keep verbose per-node diagnostics behind existing trace/diagnostic controls.

### Tests

- ungrouped output is unchanged;
- whole-device target;
- section-only target;
- any/intersection matching;
- exclusions;
- disabled contribution reveals global base;
- overlapping group contribution behavior;
- physically disabled node remains black;
- deterministic priority and blend ordering;
- LAN/BLE/simulation parity;
- mirror parity for grouped output;
- final-frame snapshot contains correct section colors.

### Acceptance criteria

- One strip can send different group-filtered colors to adjacent sections in
  one physical frame.
- Disabling one membership does not incorrectly mute a node served by another
  eligible contribution.
- Legacy ungrouped performance remains within an agreed small tolerance.

## Phase 3 — Room Layout Group Editor

### UX layout

Add a Group Manager panel to the Room Layout screen:

- group list with name, color, member count, and default enabled state;
- Add, Rename, Delete, and Duplicate actions;
- selected-node membership checklist;
- inherited memberships shown separately from direct memberships;
- exclusions available for individual strip sections;
- filter to show or dim members of selected groups;
- group enable/solo controls for active preview/runtime sessions.

### Editing workflows

Support:

1. select a whole device/strip and assign inherited groups;
2. enter individual-node mode and change one section;
3. multi-select sections and apply membership in bulk;
4. paint membership across contiguous strip sections;
5. inspect all effective memberships for the selected node;
6. highlight one or more groups on the room canvas;
7. preview enabled/disabled group state without starting hardware output.

If multi-selection or paint assignment would make the first GUI phase too
large, ship whole-device plus individual-section checklists first, then add
bulk editing immediately afterward. The persistence and controller APIs should
still be batch-oriented from the start.

### Canvas presentation

- Use group colors as small badges or outline rings, not as replacements for
  live output color.
- Overlapping membership should remain legible.
- Dim non-members when filtering rather than hiding them completely.
- Show inherited membership with a distinct visual treatment.
- Never overload selected-state coloring with group membership coloring.

### Controller/service changes

Add operations such as:

- `create_group`
- `rename_group`
- `delete_group`
- `set_device_groups`
- `set_section_groups`
- `set_section_exclusions`
- `set_group_default_enabled`
- `members_for_group`
- `effective_groups_for_node`

Controller saves should submit placement and group changes together through a
structured scene update rather than parallel ad hoc dictionaries.

### Likely files

- `src/dreamsync/gui/models/spatial_scene.py`
- `src/dreamsync/gui/controllers/spatial_controller.py`
- `src/dreamsync/gui/services/device_service.py`
- `src/dreamsync/gui/widgets/spatial_canvas.py`
- `src/dreamsync/gui/main_window.py`

### Tests

- group CRUD;
- rename preserves ID and references;
- deletion confirmation/reference summary;
- assign whole device;
- assign individual sections;
- multiple memberships;
- inherited membership display;
- exclusion display and persistence;
- selection/filter canvas behavior;
- placement editing does not alter memberships;
- group editing does not alter placement;
- keyboard focus and existing Room Layout shortcuts remain correct.

### Acceptance criteria

- A user can split one strip into Group A and Group B entirely through the GUI.
- A user can subscribe one device or section to several groups.
- Reloading the config reproduces the exact effective memberships.
- Existing strip layout, orientation, and fine-tune workflows remain usable.

## Phase 4 — Compiled Show Authoring and Playback Controls

### Compiler/backend work

1. Accept group selectors in profile effects, EQ routes, instrument routes,
   treatments, and manual scene layers.
2. Preserve group selectors during treatment and micro-cue generation.
3. Add compiler validation against the selected device/group catalog when a
   device config is available.
4. When no device config is available, preserve selectors and report
   unresolved group IDs as warnings rather than discarding them.
5. Support compiler policies such as alternating a tagged A/B group pair by
   section or phrase.

### Saved-show editor work

For the selected cue or layer, expose:

- target groups;
- any/all membership matching;
- excluded groups;
- untargeted behavior;
- priority and blend mode where applicable.

The editor should clearly distinguish:

- persistent cue targeting;
- temporary runtime group enable/disable state.

### Playback controls

Add a group control bank to saved-show playback:

- per-group on/off;
- solo;
- clear overrides;
- current default/runtime state;
- member count and optional color badge.

Runtime controls must not mutate the saved show unless the user explicitly
applies them to a cue or saves a patch.

### Likely files

- `src/dreamsync/compiler/treatments.py`
- `src/dreamsync/compiler/assemble.py`
- `src/dreamsync/profile.py`
- `src/dreamsync/show/models.py`
- `src/dreamsync/show/control_patch.py`
- `src/dreamsync/show/runtime.py`
- `src/dreamsync/show/runtime_control.py`
- `src/dreamsync/gui/controllers/show_patch_controller.py`
- `src/dreamsync/gui/main_window.py`
- relevant GUI state/service modules

### Tests

- compiler preserves targets on section and micro-cues;
- group-aware route defaults;
- show JSON round-trip;
- show patch round-trip;
- runtime group toggle during active playback;
- runtime solo;
- clear restores defaults;
- cue target and runtime disabled state compose correctly;
- prepared spatial cue caching includes normalized group metadata;
- unknown group warning/error behavior;
- legacy shows remain global.

### Acceptance criteria

- A compiled cue can target Group A while adjacent cues target Group B.
- An operator can disable either group during playback without recompiling.
- Clearing overrides returns to the authored group state.

## Phase 5 — Live Reactive Group Intelligence

### Principles

Live intelligence must not depend on names such as `left` or `top`. It should
use:

- explicit group tags;
- resolved spatial centroid and extent;
- device/section type composition;
- member count and coverage;
- musical event type and confidence;
- recent group-selection history;
- current enabled/solo state.

Names remain for human display only.

### Derived group descriptors

Precompute:

- centroid `(x, y, z)`;
- spatial bounds and spread;
- left/right, top/bottom, front/back bias;
- bulb/strip/section proportions;
- node and physical-device counts;
- overlap relationships with other groups;
- tags;
- whether the group is empty or entirely disabled.

Recompute only when the config or membership changes.

### Initial Reactive policies

Implement conservative policies first:

1. **Alternation**
   - alternate groups tagged `alternating` on phrase or section changes;
   - avoid switching on every beat.

2. **Call and response**
   - choose spatially separated group pairs;
   - send primary events to one and response accents to the other.

3. **Frequency zoning**
   - bass/sub routes prefer low or floor-biased groups;
   - presence/air routes prefer high groups;
   - vocals prefer broad/front/primary groups;
   - percussion prefers strip/accent groups.

4. **Coverage shaping**
   - quiet sections use a smaller subset;
   - lifts expand from one group to several;
   - drops may use all eligible groups plus targeted accents.

5. **Repetition avoidance**
   - track recent group emphasis;
   - penalize immediate repeats unless musical continuity warrants it.

6. **Graceful fallback**
   - zero usable groups: target `all`;
   - one usable group: treat it as global;
   - ambiguous metadata: prefer stable output over rapid random switching.

### State model

Add a small group-selection policy state:

- active emphasis groups;
- prior group groups/pair;
- last change bar/phrase/section;
- selection reason;
- confidence;
- cooldown;
- recent usage counts.

Policy decisions should occur at musical boundaries or route activation
changes, not independently on every render frame.

### Manual control precedence

1. runtime solo/disable state;
2. explicit authored/manual target;
3. profile group policy;
4. automatic Reactive policy;
5. `all` fallback.

### Telemetry

Expose:

- selected groups;
- selection reason;
- policy confidence;
- rejected candidates and reason;
- group descriptor summary;
- current cooldown;
- active manual overrides.

### Likely files

- `src/dreamsync/live.py`
- `src/dreamsync/show/runtime_control.py`
- `src/dreamsync/gui/models/reactive_settings.py`
- `src/dreamsync/gui/services/runtime_supervisor.py`
- new `src/dreamsync/groups/reactive_policy.py`
- Reactive diagnostics widgets/state

### Tests

- deterministic selection with a fixed seed/state;
- A/B alternation at phrase boundaries;
- no per-frame thrashing;
- bass prefers lower group;
- presence prefers upper group;
- disabled groups are never selected;
- solo groups constrain selection;
- overlap-aware selection avoids visually identical pairs;
- one/zero-group fallback;
- manual target precedence;
- telemetry explains each selection.

### Acceptance criteria

- Reactive mode produces stable, musically timed group changes.
- Arbitrarily named groups work through descriptors and tags.
- Manual group controls take effect on the next output frame.
- Automatic selection never re-enables a manually disabled group.

## Phase 6 — Baked Frames and Artifact Compatibility

### Work

1. Bake group-authored cue output into ordinary per-node colors.
2. Record the device config/group catalog hash already covered by the device
   config hash.
3. Optionally include a normalized group catalog summary in artifact metadata
   for diagnostics.
4. Apply runtime group gating to baked node colors at playback time.
5. Keep group-target authoring changes as a rebake-required operation.
6. Define behavior when current group membership differs from the baked device
   config:
   - validation reports staleness;
   - playback may fall back or require explicit confirmation according to the
     existing baked artifact policy.

### Important distinction

- Runtime on/off or solo changes can filter existing baked node colors.
- Changing a cue's group target, membership layout, or group composition
  requires rebaking because it changes authored frame content.

### Likely files

- `src/dreamsync/show/bake.py`
- `src/dreamsync/show/baked_frames.py`
- `src/dreamsync/show/baked_runtime.py`
- `src/dreamsync/output/govee_lan.py`

### Tests

- baked export includes group-targeted section colors;
- baked playback toggle suppresses affected nodes;
- overlapping membership follows runtime gating semantics;
- re-enable restores the current baked color without rebaking;
- changed group config marks the artifact stale;
- old baked artifacts remain readable.

### Acceptance criteria

- Baked playback visually matches ordinary grouped playback at sampled times.
- On/off and solo controls work without regenerating frames.
- Authoring/membership changes are detected as stale.

## Phase 7 — Optional True Independent Group Effects

The initial phases can target and blend the existing base frame and spatial
layers. They do not need to run a completely independent renderer instance for
every group.

Add this phase only if users need simultaneous effects such as:

- Group A scrolls one palette left-to-right;
- Group B breathes another palette at a different speed;
- their shared sections blend both animations.

### Required work

1. Represent independent group effect instances.
2. Maintain renderer state per effect instance, not just per device.
3. Render each instance into a logical node buffer.
4. Compose node buffers by priority/blend.
5. Map the final logical node buffer back into physical device frames.
6. Define phase continuity when groups are toggled off and on.
7. Add resource limits and diagnostics for active effect instances.

This is a materially larger feature and should not block basic group
membership, targeting, toggling, or Reactive selection.

## Migration and Backward Compatibility

### Device configs

- Missing `groups` means no explicit groups.
- Every node still receives implicit `all`.
- Missing device/section membership fields resolve to empty direct membership.
- Existing `enabled`, placement, role, brightness, and orientation semantics
  remain unchanged.

### Profiles and shows

- Missing target metadata means `target_groups: ["all"]`.
- Unknown group targets remain serialized.
- Playback with an unavailable group reports a warning and follows a documented
  fallback; it must not silently redirect to a different named group.

Recommended fallback:

- if every explicit target is unknown, treat the contribution as ineligible;
- surface the warning in GUI status and telemetry;
- offer an editor repair action to map missing IDs.

### Runtime state

- Group runtime overrides begin empty.
- Starting a new session initializes catalog defaults.
- Reloading a changed device config clears or reconciles overrides
  transactionally and reports removed IDs.

## Performance Requirements

Group routing runs inside the render path and must avoid avoidable allocation.

Requirements:

- resolve group IDs to masks/indexes when config or cue metadata changes;
- cache normalized cue selectors;
- cache node effective memberships;
- avoid constructing Python sets per node per frame;
- perform section gating in the same loop that already spatializes or submits
  segment colors;
- keep the no-groups legacy path fast;
- bound the number of simultaneous independent contributions.

Add timing telemetry around:

- selector resolution;
- group composition;
- total per-frame output;
- node count and contribution count.

Suggested performance acceptance target:

- no measurable functional FPS regression in ordinary ungrouped rooms;
- grouped routing remains comfortably below the existing LAN/BLE render
  interval for representative 15-, 30-, and 100-node layouts.

## Error Handling and Recovery

- Invalid config must identify the exact group/device/section path.
- GUI group mutations should be applied to in-memory state first and saved
  atomically through the existing config persistence approach.
- A failed save must leave the on-disk config intact.
- Runtime references to removed groups should be reconciled without crashing
  active audio analysis.
- Adapter failures remain isolated per physical device.
- Empty groups are valid but never automatically selected by Reactive policy.

## Test Strategy

### Unit

- domain validation;
- membership inheritance;
- selector matching;
- state precedence;
- contribution composition;
- Reactive candidate scoring;
- schema round-trips.

### Integration

- config to physical adapter tuple/node-route resolution;
- compiler to show JSON to playback;
- Live Reactive runtime control updates;
- GUI editor persistence;
- simulation/hardware mirror parity;
- bake/export/playback.

### Regression

Run at minimum:

- spatial models, mapper, and runtime suites;
- output LAN/BLE suites;
- preview simulation;
- baked frames/export/runtime;
- show compiler, models, patches, and runtime;
- GUI spatial chains/layout tests;
- GUI mode switching and runtime control tests;
- Live output contracts and Reactive structure/action tests.

### Manual validation scenarios

1. One 15-section strip split into left/right halves.
2. Alternating even/odd sections assigned to A/B.
3. A section belonging to left, strips, and A simultaneously.
4. Two bulbs plus one strip sharing an accent group.
5. Disable A during a compiled cue, then re-enable it.
6. Solo Top during Live Reactive mode.
7. Change group selection while hardware simulation mirror is visible.
8. Bake a grouped show, toggle a group during baked playback, and compare
   output with non-baked playback.
9. Rename a display name and verify authored references still work.
10. Remove a referenced group and verify repair/warning behavior.

## Recommended Delivery Sequence

1. Phase 1: schema and resolved membership.
2. Phase 2: shared routing and output enforcement.
3. Phase 3: Room Layout editor.
4. Phase 4: compiled authoring and runtime controls.
5. Phase 6: baked compatibility.
6. Phase 5: intelligent Reactive policy.
7. Phase 7 only if independent multi-effect demand justifies it.

Phase 5 follows reliable manual targeting and toggling so Reactive intelligence
is built on observable, user-controllable primitives.

## Definition of Done

The feature is complete when:

- groups are fully editable from Room Layout;
- device and section membership is many-to-many;
- adjacent sections of one strip can have different memberships;
- group data survives every supported config edit and reload path;
- cues and layers can target groups;
- group toggles and solo work in compiled and Live Reactive sessions;
- overlapping groups follow documented deterministic semantics;
- simulation, hardware, diagnostics, and baked playback agree;
- legacy configs and shows behave as before;
- Live Reactive selects groups at musically meaningful boundaries and exposes
  its reasoning;
- automated and manual acceptance scenarios pass.

## Risks and Mitigations

### Risk: ambiguous overlapping memberships

Mitigation: separate contribution gating from physical-node mute and define
priority/blend behavior before implementation.

### Risk: Room Layout saves erase new metadata

Mitigation: replace placement-only save payloads with structured scene updates
and add round-trip preservation tests before exposing group editing.

### Risk: group logic diverges between compiled and Reactive paths

Mitigation: enforce selectors and runtime state in a shared node-routing layer
used by both paths.

### Risk: baked playback cannot honor runtime controls

Mitigation: retain node identity in artifacts and apply group gating immediately
before baked node colors are mapped to physical frames.

### Risk: Reactive mode becomes visually random

Mitigation: make decisions at musical boundaries, include cooldown and recent
usage state, prefer stable fallback, and expose telemetry explaining choices.

### Risk: per-frame routing overhead

Mitigation: pre-resolve groups and selectors to masks, preserve a fast legacy
path, and benchmark representative room sizes.

### Risk: display-name changes break shows

Mitigation: use stable group IDs everywhere outside the editor label.

