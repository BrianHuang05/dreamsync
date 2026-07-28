# Phase 9 Baked Frame Export And Playback Cache Plan

## Status

Draft implementation plan for Phase 9 of the spatial effecting layer engine.

Current implementation decision:

- Saved-show baked export/playback is in scope for Phase 9.
- Live pipeline auto-bake is deferred until show generation efficiency improves.
- Pipeline playback and preview should use live cue playback for now, even when saved-show baked playback is set to `auto` or `require`.

This plan extends:

- [dev/plans/spatial-effecting-layer-engine-plan.md](dev/plans/spatial-effecting-layer-engine-plan.md)
- [dev/plans/continuous-spatial-runtime-followup.md](dev/plans/continuous-spatial-runtime-followup.md)
- [dev/plans/gui-precompiled-show-player-plan.md](dev/plans/gui-precompiled-show-player-plan.md)

## Goal

Add an optional baked playback artifact for saved shows. Live-pipeline generated shows may use the same artifact format later, but automatic pipeline baking is deferred for now.

The editable `.show.json` remains the canonical authored document. A baked artifact is derived from:

- the authored show timeline
- the spatial/device layout
- the spatial layer engine version
- render/output settings that affect final per-node colors

The baked artifact lets playback avoid most runtime spatial computation by reading precomputed frame samples.

## Non-Goals

- Do not replace `.show.json` with baked-only data.
- Do not make baked playback mandatory.
- Do not block normal editable cue playback when a baked artifact is missing or stale.
- Do not attempt photoreal room rendering.
- Do not bake hardware transport packets in the first implementation; bake logical node/segment colors.

## Desired End State

Normal editable playback remains:

```text
.show.json
-> cue lookup
-> spatial layer preparation
-> per-frame spatial sampling
-> renderer/output adapter
```

Baked playback becomes:

```text
.show.json + device config + bake settings
-> .show.frames.json or binary cache
-> frame lookup/interpolation
-> output adapter / GUI preview
```

The runtime can choose:

- use baked frames when a valid artifact exists and baked mode is enabled
- fall back to live-simulated cue playback otherwise

## Key Design Principles

- Authored shows stay editable.
- Baked artifacts are disposable cache files.
- Cache invalidation must be explicit and deterministic.
- Baked playback should serve both GUI simulation and hardware output.
- The first version should favor correctness and debuggability over compactness.
- The schema should leave room for a future binary format.

## Artifact Naming

Start with JSON for inspectability:

```text
song.show.json
song.show.frames.json
```

For pipeline captures:

```text
capture-name.show.json
capture-name.show.frames.json
```

Future binary cache option:

```text
song.show.frames.bin
song.show.frames.index.json
```

## Proposed JSON Schema

Top-level shape:

```json
{
  "schema_version": 1,
  "kind": "dreamsync_baked_frames",
  "source_show_path": "C:/path/song.show.json",
  "source_show_hash": "sha256...",
  "device_config_path": "C:/path/devices.yaml",
  "device_config_hash": "sha256...",
  "engine": {
    "dreamsync_version": "local",
    "spatial_engine_version": 1,
    "renderer_version": 1
  },
  "settings": {
    "fps": 30,
    "sample_mode": "nearest",
    "include_sections": true,
    "color_space": "srgb8"
  },
  "duration": 123.456,
  "nodes": [
    {
      "key": "10.0.0.12#section:0",
      "device_address": "10.0.0.12",
      "section_index": 0,
      "kind": "section"
    }
  ],
  "frames": [
    {
      "t": 0.0,
      "colors": ["#000000", "#224466"]
    }
  ],
  "summary": {
    "frame_count": 3704,
    "node_count": 12,
    "cue_count": 42,
    "generated_at": "2026-05-27T18:00:00"
  }
}
```

Notes:

- `nodes` defines stable color order for every frame.
- `frames[*].colors.length` must equal `nodes.length`.
- Store colors as hex in v1 for readability.
- If size becomes a problem, add a binary format later without changing the authored show format.

## Invalidation Inputs

A baked artifact is valid only if all relevant inputs still match:

- source `.show.json` hash
- device config/layout hash
- spatial engine version
- renderer version
- target FPS
- section expansion mode
- render mode adaptation rules
- output role/brightness scale rules when they affect baked colors

The validity check should return a structured reason:

```python
@dataclass(frozen=True)
class BakeValidation:
    valid: bool
    reason: str = ""
    stale_fields: tuple[str, ...] = ()
```

Examples:

- `missing_artifact`
- `source_show_hash_changed`
- `device_config_hash_changed`
- `fps_changed`
- `engine_version_changed`
- `schema_version_unsupported`

## Implementation Phases

### Phase 1: Data Model And Hashing

Objective: define the baked artifact contract and validation helpers.

Files likely to change:

- `src/dreamsync/show/baked_frames.py`
- `src/dreamsync/show/models.py`
- `dev/tests/test_baked_frames.py`

Work:

- Add `BakedFrameNode`, `BakedFrame`, `BakedFrameArtifact`, and `BakeSettings`.
- Add `to_dict` / `from_dict` / `to_json` / `from_json`.
- Add deterministic file hash helper.
- Add validation function comparing artifact metadata with current inputs.
- Add schema version checks.

Completion criteria:

- Baked artifact round-trips through JSON.
- Validation detects changed show hash, device hash, FPS, and schema.

### Phase 2: Frame Baking Engine

Objective: evaluate a show timeline into node/section color frames at a fixed FPS.

Files likely to change:

- `src/dreamsync/show/bake.py`
- `src/dreamsync/output/null_adapter.py`
- `src/dreamsync/output/govee_lan.py`
- `src/dreamsync/show/runtime.py`
- `dev/tests/test_baked_frame_export.py`

Work:

- Build a bake adapter that captures logical node/section colors instead of sending output.
- Reuse `ShowPlaybackRuntime` and `MultiGoveeLanAdapter` where possible.
- Use the same spatial preparation path as playback.
- Sample at fixed frame times: `0, 1/fps, 2/fps, ... duration`.
- Capture section-level colors using the same node keys used by simulation preview.
- Include a small progress callback for GUI/pipeline integration.

Important behavior:

- Baking must not open audio streams.
- Baking must not send hardware packets.
- Baking must not mutate the canonical timeline.

Completion criteria:

- A simple show bakes deterministic frames.
- Spatial slice/expand/static layers produce expected different frame colors over time.
- Section placement is reflected in baked output.

### Phase 3: Baked Playback Runtime

Objective: play a baked artifact cheaply at runtime.

Files likely to change:

- `src/dreamsync/show/baked_runtime.py`
- `src/dreamsync/gui/services/session_service.py`
- `src/dreamsync/output/null_adapter.py`
- `src/dreamsync/output/govee_lan.py`
- `dev/tests/test_baked_playback_runtime.py`

Work:

- Add `BakedFramePlaybackRuntime`.
- Given current audio position, find the nearest frame or interpolate between frames.
- Send colors to:
  - GUI simulation preview
  - hardware adapters
  - BLE followers if a sensible fallback color is available
- Keep audio playback flow the same as normal timeline playback.

Initial frame lookup:

- Use nearest-frame lookup for v1.
- Add optional interpolation later if needed.

Completion criteria:

- Runtime can play frames without calling spatial mapper per frame.
- Playback position controls frame selection.
- Missing/stale baked artifact falls back to normal timeline playback.

### Phase 4: Session Service Integration

Objective: let saved-show playback choose baked playback when available. Pipeline playback remains live-only until pipeline/show generation efficiency is revisited.

Files likely to change:

- `src/dreamsync/gui/services/session_service.py`
- `src/dreamsync/gui/services/runtime_supervisor.py`
- `src/dreamsync/gui/settings.py`
- `dev/tests/test_gui_services.py`
- `dev/tests/test_gui_runtime_supervisor.py`

Work:

- Add playback setting:
  - `baked_playback_mode`: `"off" | "auto" | "require"`
- In `auto`, use baked artifact only when valid.
- In `require`, fail with a clear error if the artifact is missing/stale.
- In `off`, always use normal timeline playback.
- Record selected mode and validation reason in runtime diagnostics.

Completion criteria:

- Saved-show playback can use baked frames.
- Pipeline playback intentionally skips baked frames until pipeline/show generation efficiency is revisited.
- Invalid bake artifacts produce clear UI/runtime messages.

### Phase 5: CLI Export Command

Objective: provide a reliable non-GUI way to bake frames.

Files likely to change:

- `src/dreamsync/cli.py`
- `dev/tests/test_cli_baked_frames.py`

Proposed command:

```powershell
python -m dreamsync bake-frames path\to\song.show.json --config path\to\devices.yaml --fps 30
```

Options:

- `--output path`
- `--fps 15|24|30|60`
- `--force`
- `--validate-only`
- `--summary`

Completion criteria:

- CLI can create `.show.frames.json`.
- CLI can validate an existing artifact.
- CLI exits nonzero for stale/missing required inputs.

### Phase 6: GUI Export And Playback Controls

Objective: expose baked-frame workflows without making the GUI noisy.

Files likely to change:

- `src/dreamsync/gui/widgets/queue_panel.py`
- `src/dreamsync/gui/main_window.py`
- `src/dreamsync/gui/services/runtime_telemetry_service.py`
- `dev/tests/test_gui_mode_switching.py`

Suggested controls:

- `Bake Frames` button near saved-show controls.
- `Use baked playback` combo:
  - `Auto`
  - `Off`
  - `Require`
- Status label:
  - `Baked: valid, 30 FPS, 3704 frames`
  - `Baked: stale, device config changed`
  - `Baked: missing`
- Optional `Validate Bake` action.

Pipeline-specific controls:

- Bake ready show
- Auto-bake ready shows after compile
- Prefer baked playback for pipeline queue

Completion criteria:

- User can bake the selected saved show from the GUI.
- User can see whether baked playback will be used.
- Pipeline ready queue can optionally auto-bake compiled shows.

### Phase 7: Pipeline Integration

Status: deferred.

Objective: after show generation efficiency improves, make captured/generated shows optionally bake after compile.

Files likely to change:

- `src/dreamsync/show_pipeline_worker.py`
- `src/dreamsync/gui/services/runtime_supervisor.py`
- `src/dreamsync/gui/models/runtime.py`
- `dev/tests/test_gui_runtime_supervisor.py`
- `dev/tests/test_pipeline_integration.py`

Work:

- Add pipeline setting:
  - `auto_bake_frames_after_compile: bool`
  - `pipeline_bake_fps: int`
- When a show enters ready state, optionally bake frames.
- Store baked artifact path on the ready item.
- Surface bake status in ready queue metadata.
- Do not block capture while baking if avoidable.

Completion criteria:

- Pipeline can produce `.show.json` and `.show.frames.json`.
- Ready queue shows bake status.
- Pipeline playback can prefer baked artifacts.

### Phase 8: Diagnostics And Performance Metrics

Objective: prove that baked playback reduces live CPU/render load.

Files likely to change:

- `src/dreamsync/gui/services/session_service.py`
- `src/dreamsync/gui/services/runtime_telemetry_service.py`
- `src/dreamsync/show/baked_runtime.py`

Work:

- Record:
  - bake duration
  - frame count
  - node count
  - playback mode used
  - frame lookup time
  - fallback reason
- Add `out/playback_diagnostics.log` entries for baked playback selection.
- Add a small bake summary in artifact metadata.

Completion criteria:

- Diagnostics clearly show whether baked or live-simulated playback was used.
- Users can tell why baked playback was skipped.

## Runtime Selection Rules

Default mode should be `auto`.

Pseudo-flow:

```python
if baked_mode == "off":
    use_live_simulated()
elif baked_mode == "require":
    artifact = load_and_validate_or_raise()
    use_baked(artifact)
else:
    artifact = load_and_validate()
    if artifact.valid:
        use_baked(artifact)
    else:
        log_skip_reason(artifact.reason)
        use_live_simulated()
```

## Open Design Questions

- Should v1 bake per section or per physical device segment?
  - Recommendation: bake per preview node/section key, because this aligns with GUI simulation and section-level spatial placement.
- Should baked playback interpolate frames?
  - Recommendation: nearest-frame lookup in v1; interpolation can follow.
- Should render-mode local animation be baked too?
  - Recommendation: yes. The artifact should represent final logical colors, not just spatial activation values.
- Should hardware brightness scale be baked?
  - Recommendation: no for v1 unless it affects preview colors. Keep hardware brightness as output-time behavior.
- Should baked files be committed or treated as cache?
  - Recommendation: cache by default, user-exportable when desired.

## Testing Strategy

Unit tests:

- artifact JSON round-trip
- validation stale/missing scenarios
- deterministic frame baking
- baked runtime frame lookup
- fallback to live simulation

Integration tests:

- saved show bakes and plays
- pipeline ready item auto-bakes
- stale device config disables baked playback in `auto`
- stale device config errors in `require`
- spatial slice produces changing frames over time
- static region produces stable bounded frames
- expand layer produces radial activation over time

Performance tests:

- compare normal spatial playback vs baked playback frame loop CPU/time
- assert baked playback does not call spatial layer resolution per frame

GUI tests:

- bake controls exist
- baked status label updates
- pipeline ready item shows bake status
- baked mode setting persists

## Rollout Order

Recommended implementation order:

1. Data model and validation.
2. Bake adapter and frame export engine.
3. CLI `bake-frames` command.
4. Baked playback runtime.
5. Session service `auto/off/require` integration.
6. GUI controls for saved shows.
7. Defer pipeline auto-bake integration until pipeline/show generation efficiency improves.
8. Diagnostics and performance polish.

This order gives a usable CLI/debug path before adding GUI complexity.

## Definition Of Done

Phase 9 is complete when:

- `.show.json` remains the editable source of truth.
- A `.show.frames.json` artifact can be generated from a show and device config.
- The artifact can be validated against current inputs.
- Saved-show playback can use baked frames in `auto` mode.
- Live pipeline ready-show playback intentionally uses live cue playback for now.
- Missing/stale baked frames fall back cleanly unless `require` is selected.
- GUI clearly shows baked artifact status and selected playback mode.
- Tests prove baked playback avoids runtime spatial layer resolution per frame.
