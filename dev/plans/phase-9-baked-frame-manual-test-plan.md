# Phase 9 Baked Frame Manual Test Plan

## Purpose

This guide covers manual validation for the Phase 9 baked-frame implementation after the code test suite is green.

The key product decision is that pipeline baking is not currently expected to be useful. Saved shows can benefit from baked playback because the artifact is reusable. The live capture pipeline is ephemeral, and a baker that keeps up with playback still consumes one second of bake time per one second of song unless there is queue slack. Pipeline playback should therefore remain live cue playback unless future pipeline/show generation efficiency changes the economics.

Use this guide in two modes:

- With lights connected: validate saved-show bake/export/playback against physical devices.
- Without lights connected: validate pipeline and baked playback behavior in simulation.

## Prerequisites

- Working Python environment for this repo.
- `PYTHONPATH` includes `src` and the local test dependency path when needed:

```powershell
$env:PYTHONPATH='src;.codex-python'
```

- A known-good device config YAML for connected-light testing, for example:

```powershell
$config = 'C:\Users\brian\dreamsync\devices.yaml'
```

- At least one saved show file, for example:

```powershell
$show = 'C:\path\to\song.show.json'
$audio = 'C:\path\to\song.mp3'
```

- A capture directory for pipeline testing:

```powershell
$captures = 'C:\path\to\captures'
```

## Baseline Automated Checks

Run these before manual testing so manual failures are easier to interpret.

```powershell
$env:PYTHONPATH='src;.codex-python'
python -m pytest dev\tests\test_playback_selector.py dev\tests\test_baked_frames.py dev\tests\test_baked_frame_export.py dev\tests\test_baked_playback_runtime.py dev\tests\test_cli_baked_frames.py dev\tests\test_null_adapter.py dev\tests\test_spatial_runtime.py dev\tests\test_preview_simulation.py dev\tests\test_show_runtime.py dev\tests\test_show_cli.py dev\tests\test_gui_services.py dev\tests\test_gui_runtime_supervisor.py dev\tests\test_gui_runtime_telemetry_service.py dev\tests\test_gui_mode_switching.py
```

Expected result:

- Phase 9 suite passes.
- Current known full-suite baseline failures, if the full suite is run, are unrelated to Phase 9:
  - `dev/tests/test_capture_process.py::TestBuildCommand::test_command_structure`
  - `dev/tests/test_features.py::FeatureRowFromFrameTests::test_returns_all_expected_keys`

## Saved-Show Baked Playback With Lights Connected

### 1. Confirm Device Discovery And Routing

Start the GUI:

```powershell
$env:PYTHONPATH='src;.codex-python'
python -m dreamsync gui
```

In the GUI:

- Load or select the connected device config.
- Confirm the runtime strip reports hardware routing rather than simulation-only routing.
- Start a short reactive or saved-show playback to confirm lights respond normally before testing baked playback.

Pass criteria:

- Lights respond.
- No routing error is shown.
- Device status does not report fallback-only simulation unless that is explicitly selected.

### 2. Bake A Saved Show From The CLI

Run:

```powershell
$env:PYTHONPATH='src;.codex-python'
python -m dreamsync bake-frames $show --config $config --fps 30 --force --summary
```

Expected result:

- A sibling artifact is created:

```text
song.show.frames.json
```

- CLI summary includes frame count, node count, and artifact path.
- Artifact JSON summary includes:
  - `frame_count`
  - `node_count`
  - `cue_count`
  - `bake_duration_seconds`
  - `bake_frames_per_second`

Manual inspection:

```powershell
Get-Content ($show -replace '\.show\.json$', '.show.frames.json') -TotalCount 80
```

Pass criteria:

- `kind` is `dreamsync_baked_frames`.
- `settings.fps` matches the requested FPS.
- `nodes` contain device or section keys matching the connected layout.
- `frames[*].colors` arrays are the same length as `nodes`.

### 3. Validate Artifact Freshness

Run:

```powershell
python -m dreamsync bake-frames $show --config $config --fps 30 --validate-only
```

Pass criteria:

- Command exits successfully.
- Output indicates the baked artifact is valid.

Negative check:

```powershell
python -m dreamsync bake-frames $show --config $config --fps 15 --validate-only
```

Pass criteria:

- Command exits nonzero or reports stale validation.
- Reason should identify FPS/settings mismatch.

### 4. Play Saved Show In `Auto`

In the GUI:

- Select the saved show.
- Set baked playback combo to `Auto`.
- Start saved-show playback.

Pass criteria:

- Lights play the show.
- `Baked:` status becomes active, or shows a clear skip reason if the artifact is stale.
- `out/playback_diagnostics.log` contains a line similar to:

```text
selected playback runtime mode=baked reason=valid
```

- Runtime diagnostics include:
  - `playback_mode_used=baked`
  - `baked_validation_valid=True`
  - `frame_lookup_count`
  - `frame_lookup_avg_ms`
  - `frame_lookup_max_ms`

### 5. Play Saved Show In `Require`

In the GUI:

- Set baked playback combo to `Require`.
- Start saved-show playback.

Pass criteria with a valid artifact:

- Playback starts.
- `Baked:` status indicates active baked playback.
- Lights match the expected show cues.

Negative check:

- Rename or remove the `.show.frames.json` artifact.
- Keep baked playback set to `Require`.
- Start saved-show playback.

Pass criteria:

- Playback fails clearly instead of silently falling back.
- The UI or diagnostics report missing/stale baked artifact.

Restore the artifact before continuing.

### 6. Play Saved Show In `Off`

In the GUI:

- Set baked playback combo to `Off`.
- Start saved-show playback.

Pass criteria:

- Lights play using live cue playback.
- Diagnostics show:

```text
playback_mode_used=live
baked_validation_reason=baked_playback_off
```

This confirms the baked setting is respected and the live path remains available.

### 7. Compare Visual Output

Run the same 30 to 60 second segment twice:

1. Baked mode `Off`.
2. Baked mode `Auto` with a valid artifact.

Observe:

- Color timing at cue boundaries.
- Section placement on segmented strips.
- Brightness behavior.
- BLE follower fallback behavior, if configured.
- Whether any device lags, flickers, or shows stale colors.

Pass criteria:

- Baked and live playback are visually equivalent for the same saved show.
- Baked playback does not introduce visible timing jumps at cue boundaries.
- Segment-specific effects land on the expected physical sections.

## Saved-Show Baked Playback Without Lights

Use this track when no lights are connected.

### 1. Bake A Show Against A Simulation Config

Create or use a device config with at least one segmented LAN strip:

```yaml
devices:
  - name: Test Strip
    address: 10.0.0.2
    type: lan
    segments: 6
```

Run:

```powershell
python -m dreamsync bake-frames $show --config $config --fps 30 --force --summary
```

Pass criteria:

- Artifact is generated.
- `nodes` include `10.0.0.2#section:0` through the expected final section.

### 2. Validate Simulation Preview

Start GUI without hardware routing.

In the GUI:

- Load the saved show.
- Bake frames with `Bake Frames`, or use the CLI-generated artifact.
- Set baked playback to `Auto`.
- Start saved-show playback.

Pass criteria:

- Preview updates.
- `Baked:` status reports active baked playback.
- Diagnostics show `playback_mode_used=baked`.
- Preview node colors are populated.

### 3. Confirm Live Fallback

Remove or rename the artifact and keep baked playback in `Auto`.

Pass criteria:

- Playback still starts in simulation.
- Diagnostics show `playback_mode_used=live`.
- Diagnostics include a skip reason such as `missing_artifact`.

## Pipeline Testing Without Lights

These tests verify that pipeline remains live cue playback and does not require baked artifacts.

### 1. CLI Directory Pipeline Compile-Only

Run:

```powershell
$env:PYTHONPATH='src;.codex-python'
python -m dreamsync pipeline $captures --config $config --mode compile --debug
```

Pass criteria:

- Capture directory scans.
- Tracks analyze and compile.
- No baked artifact is required.
- The command does not attempt to bake `.show.frames.json` files.

Record:

- Number of tracks scanned.
- Number analyzed.
- Number compiled.
- Any failed files and error messages.

### 2. CLI Directory Pipeline Play In Simulation

Run with a test/simulation config:

```powershell
python -m dreamsync pipeline $captures --config $config --mode play --debug --fps 30
```

Pass criteria:

- Tracks play through the pipeline.
- Pipeline uses analyze -> compile -> play.
- No `.show.frames.json` artifact is required for playback.
- Playback continues across multiple tracks.
- Debug output does not show baked playback as a requirement.

Observe:

- Whether playback starts before all tracks have compiled.
- Whether queue gaps occur between tracks.
- Whether CPU spikes during analysis/compile affect playback.
- Whether failures are isolated to the failing track.

### 3. GUI Capture Pipeline Simulation

In the GUI:

- Set output target to simulation or enable fallback to simulation.
- Set capture directory.
- Enable `Verbose pipeline debug`.
- Click `Start Capture`.
- Feed known audio through the capture source.
- Click `Switch To Pipeline` once ready items appear.

Pass criteria:

- `Capture:` becomes running.
- `Pipeline:` moves through running/processing/ready.
- Ready count increases when compiled items are available.
- `Switch To Pipeline` plays ready items.
- Baked status must not imply pipeline baked playback; pipeline playback is expected to pass `baked_playback_mode="off"`.

Record:

- Time from segment completion to ready state.
- Time from ready state to playback start.
- Whether ready queue grows, stays flat, or drains immediately.
- Any error shown in the ready list.

### 4. Pipeline Queue Slack Observation

This is the important test for deciding whether pipeline baking should ever return.

During a realistic capture session, record:

| Segment | Capture end | Ready time | Playback start | Queue wait seconds | Notes |
| --- | --- | --- | --- | --- | --- |
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |

Interpretation:

- If queue wait is usually near zero, pipeline baking remains unattractive.
- If queue wait is regularly longer than the song duration or a large fraction of it, opportunistic baking may become viable.
- If compile is the bottleneck, improve show generation before revisiting baking.
- If playback is the bottleneck and ready queue grows naturally, background baking could be reconsidered.

### 5. Pipeline Failure And Recovery

Test with a mix of valid and intentionally bad files.

Pass criteria:

- A failed analysis or compile marks only that item failed.
- Later items still become ready.
- Playback continues for valid ready items.
- Stopping output does not stop capture unless explicitly requested.
- Stopping capture does not corrupt already-ready items.

## Pipeline Testing With Lights Connected

Use this only after simulation pipeline tests pass.

### 1. Hardware Smoke Test

Before starting capture:

- Confirm lights respond to a saved show or reactive live session.
- Confirm the desired output target is hardware.
- Confirm fallback-to-simulation behavior is set intentionally.

Pass criteria:

- Lights respond before pipeline testing begins.
- Any hardware routing issue is solved before involving the pipeline.

### 2. Pipeline Playback Hardware Test

In the GUI:

- Start capture.
- Wait for one or more ready items.
- Click `Switch To Pipeline`.

Pass criteria:

- Lights respond during pipeline playback.
- Playback uses live cue playback, not baked playback.
- Pipeline ready item state changes to playing and then returns/discards according to the configured purge behavior.
- No baked artifact is required.

### 3. Long-Run Stability Test

Run at least 30 minutes of capture + pipeline playback.

Record:

- Number of captured segments.
- Number of ready items.
- Number played.
- Number failed.
- Any device dropouts.
- Any increasing delay between capture completion and playback.
- CPU and memory observations.

Pass criteria:

- Capture continues while playback runs.
- Device output remains responsive.
- Failures are isolated.
- No accumulating unbounded queue unless input rate exceeds processing by design.

## Diagnostics To Collect

Collect these files after each manual run:

- `out/playback_diagnostics.log`
- `logs/pipeline.jsonl`
- Any generated `.show.json`
- Any generated `.show.frames.json`
- GUI screenshots of:
  - runtime strip
  - ready queue
  - baked status
  - routing status

For baked saved-show tests, capture these fields from session summary or diagnostics:

- `playback_mode_used`
- `baked_validation_valid`
- `baked_validation_reason`
- `baked_stale_fields`
- `baked_artifact_path`
- `frame_count`
- `node_count`
- `frames_sent`
- `frame_lookup_count`
- `frame_lookup_avg_ms`
- `frame_lookup_max_ms`

For pipeline tests, capture:

- Ready queue depth over time.
- Time from capture saved -> analyzing -> compiling -> ready.
- Time from ready -> playback start.
- Whether any item waits long enough that baking would have fit into idle queue time.

## Decision Criteria

### Saved-Show Baked Playback Is Accepted When

- CLI bake creates valid artifacts.
- GUI `Bake Frames` creates valid artifacts for saved shows.
- `Auto` uses baked playback when valid and live fallback when missing/stale.
- `Require` fails clearly when missing/stale.
- `Off` always uses live cue playback.
- Connected lights look equivalent between live and baked saved-show playback.
- Diagnostics clearly show mode, validation reason, and lookup timings.

### Pipeline Baking Stays Deferred When

- Pipeline ready items usually have little or no queue wait.
- Baking would compete with capture/compile/playback for CPU.
- Pipeline playback works correctly without baked artifacts.
- Most pipeline value comes from reducing compile latency rather than caching final frames.

### Pipeline Baking Can Be Reconsidered Only If

- Ready queue naturally accumulates enough wait time to bake without blocking playback.
- Show generation becomes fast enough that baking is no longer competing with the critical path.
- Pipeline items have stable persisted `.show.json` paths and ready-item metadata can safely track baked artifact status.
- Playback can use baked artifacts opportunistically without waiting for them.

## Known Current Constraints

- Pipeline playback intentionally uses `baked_playback_mode="off"`.
- Pipeline auto-bake is not implemented.
- The GUI `Bake Frames` action is for saved shows on disk, not unsaved editor state.
- Baked artifacts are cache-like outputs. They should be regenerated after show, device config, engine, renderer, or FPS changes.
- Full test suite currently has two known unrelated baseline failures:
  - `dev/tests/test_capture_process.py::TestBuildCommand::test_command_structure`
  - `dev/tests/test_features.py::FeatureRowFromFrameTests::test_returns_all_expected_keys`
