# GUI CLI Feature-Parity Implementation Plan

## Goal

Promote the user-facing CLI capabilities identified during live-readiness review into the desktop GUI while deliberately leaving engineering, automation, and low-level tuning utilities in the CLI.

This plan covers only the capabilities previously recommended for GUI implementation:

1. Application version information.
2. Safe advanced device testing, reactive mirror direction, and master brightness.
3. Passive device health monitoring and queue repeat.
4. Deterministic show compilation, automatic baked-frame validation, and baked-frame Save As.
5. Storage/cache visibility, cache clearing, and explicit capture archiving.
6. Profile discovery, deterministic generation, validation, export, transition preview, and advanced chaining controls.

Spotify-driven capture splitting was implemented immediately before this plan and is treated as an existing dependency rather than a new phase.

## Product Principles

- Keep the normal Live workflow concise; advanced and diagnostic controls belong behind explicit panels or dialogs.
- Never send packets merely by opening a page.
- Require an explicit user action before device tests, cache deletion, or capture deletion.
- Default all hardware tests to short duration and reduced brightness.
- Preserve the GUI runtime supervisor as the single owner of active light output.
- Prefer automatic validation and clear status over exposing internal pipeline stages.
- Keep machine-readable exports, raw feature tools, low-level probe tuning, and developer replay workflows in the CLI.
- Make settings persistent only when users reasonably expect them to survive an application restart.

## Phase 1: Application Version Information

### Scope

Add a small About dialog available from the GUI that shows:

- Product name.
- Installed DreamSync version.
- Python version.
- Current device-config path, if selected.
- A copyable support-information summary.

### Design

- Add a reusable service/helper that resolves package metadata with a source-tree fallback.
- Add an `About DreamSync` action to the window menu.
- Keep test-only `--close-after-ms` and machine-readable `--version` in the CLI.

### Acceptance Criteria

- About opens without optional hardware or Spotify dependencies.
- Version resolution works both when installed and when run from source.
- No secrets, Spotify tokens, device addresses, or user paths beyond the selected config path appear in copied support information.

## Phase 2: Safe Hardware Controls

### Advanced Device Test

Extend Device Discovery with a deliberate `Advanced Test` dialog for the selected discovered/configured device.

Controls:

- Test color.
- Pattern appropriate to protocol:
  - LAN: solid, alternate, rainbow, walk.
  - BLE: solid, walk.
- Duration, clamped to a safe range.
- Brightness, defaulting low.
- Segment count.
- LAN transport or BLE protocol.

Safety behavior:

- No test starts until the user presses Run.
- Only the selected device is targeted.
- Test execution occurs off the Qt event loop.
- The service always stops/deactivates its adapter in `finally`.
- The dialog provides Stop where the adapter supports cooperative interruption.
- Errors are surfaced without mutating the saved assignment.

### Reactive Mirror Direction

- Add a persisted boolean to `ReactiveSettings`.
- Expose `Center outward` versus `Left to right`.
- Forward it through `RuntimeSupervisor` and `SessionService` into the live renderer.

### Master Brightness

- Add a persisted reactive brightness scale, range 0.05–1.0.
- Treat this as a software intensity ceiling, separate from per-device configuration.
- Forward it through the existing reactive runtime path.
- Default to a conservative value for new settings while preserving current behavior for existing saved settings.

### Acceptance Criteria

- Opening Device Discovery sends no packets.
- Test arguments map correctly for LAN and BLE.
- Mirror and master brightness reach the reactive session factory.
- Invalid device/test combinations are rejected before adapter creation.

## Phase 3: Session Reliability And Queue Behavior

### Passive Device Health

Add a GUI health service that:

- Runs only when configured hardware is selected.
- Periodically probes configured devices without performing discovery.
- Produces online/degraded/offline/unknown summaries.
- Runs off the GUI thread.
- Supports explicit refresh.
- Stops cleanly when the window closes or hardware mode is disabled.

The first implementation may reuse `DeviceHealthMonitor` if its callback and lifecycle interface are suitable; otherwise add a GUI adapter around it.

### Queue Repeat

- Add a persisted or session-local Repeat Queue control beside Shuffle.
- Forward repeat when building `PlaylistManager` and local playlist sessions.
- Allow toggling repeat for an already loaded session if the underlying playlist supports it.
- Display repeat state in queue status.

### Acceptance Criteria

- Health polling never runs in simulation-only mode.
- Health polling does not add newly discovered devices.
- Repeat wraps after the final track and does not alter track order.
- Disabling repeat restores normal queue completion.

## Phase 4: Compilation And Baked Artifacts

### Deterministic Compile Seed

- Add an optional integer seed in Show compile settings.
- Use it for single-track and Compile All paths.
- Persist it only when explicitly enabled.
- Display the active seed in compilation details for reproducibility.

### Automatic Baked Validation

- Validate baked-frame metadata and frame structure whenever an artifact is loaded or selected for playback.
- Reject incompatible artifacts before output starts.
- Surface the validation reason in Show status and diagnostics.
- Keep the CLI `--validate-only` command for batch/automation use.

### Baked Save As

- Add `Save Baked Frames As…` after a valid artifact exists.
- Copy/export through the baked-frame model/service rather than raw GUI file manipulation.
- Confirm before replacing an existing file.
- Preserve the source artifact if export fails.

### Acceptance Criteria

- Identical input/profile/seed produces identical compiled output.
- Invalid baked artifacts cannot become active output.
- Save As produces an artifact that passes the same validator.

## Phase 5: Storage, Cache, And Capture Archives

### Storage View

Add a Storage section showing:

- Cache entry count and total size.
- Capture directory file count and total size.
- Current cache and capture paths.
- Refresh action.

### Cache Actions

- Clear all cache entries with confirmation.
- Clear cache for the selected track when a stable track ID is available.
- Refresh storage statistics after each mutation.
- Do not expose arbitrary per-operation cache directories.

### Capture Archive

Add an explicit Archive Captures action:

- Choose archive destination/name.
- Preview MP3 count and total bytes.
- `Keep originals` defaults on.
- If originals will be deleted, require an additional explicit confirmation.
- Leave JSON sidecars untouched, matching CLI archive semantics unless the dialog clearly offers a separate choice later.
- Never archive automatically during shutdown.

### Acceptance Criteria

- Refresh is read-only.
- Clear actions cannot escape the configured cache root.
- Archive failure leaves originals intact.
- Delete-after-archive occurs only after the ZIP is successfully finalized.

## Phase 6: Profile Library And Advanced Generation

### Profile Library

- Search built-in and configured profile directories.
- Filter by name and tags.
- Show palette count, moods, and validation state.
- Load the selected profile into the existing editor.

### Deterministic Generation And Export

- Add optional generation seed.
- Add bounded auto-palette pool size.
- Preview generated profiles before applying them.
- Support `Save As Profile…` for generated profiles without exposing CLI pool indices.

### Validation And Harmony

- Run schema validation before save.
- Present color-harmony warnings as non-blocking unless the profile is structurally invalid.
- Associate messages with the affected palette or field where possible.

### Chain Preview And Timing

- Preview cross-fades in simulation without starting hardware output.
- Add optional minimum/maximum dwell duration.
- Preserve the existing fixed rotation interval when range mode is disabled.
- Forward auto-palette seed, pool size, and dwell range through the reactive session configuration.

### Acceptance Criteria

- Profile search never mutates files.
- Generated results are reproducible for a fixed seed.
- Invalid profiles cannot overwrite valid files.
- Harmony warnings do not prevent intentional artistic choices.
- Chain preview remains simulation-only.

## Test Strategy

Run tests after every phase rather than waiting for the final integration:

1. Version helper and About GUI smoke test.
2. Device-test argument/service tests plus reactive-setting propagation tests.
3. Health-service lifecycle tests and playlist-repeat tests.
4. Compile-seed determinism and baked validation/export tests.
5. Cache boundary, storage-statistics, and archive transaction tests.
6. Profile search, generation determinism, validation, export, and chain-setting propagation tests.

After all phases:

- Run all GUI service/controller/model tests.
- Run capture, playback, cache, profile, and output tests touched by the changes.
- Run the full suite and classify any remaining failures as new regressions or pre-existing failures.
- Perform simulation-only manual checks before connecting lights.

## Manual Safety Gate Before Physical Lights

Physical connection should wait until:

- GUI opens and closes cleanly.
- Simulation playback, reactive mode, queue repeat, and capture splitting all pass.
- Device tests show the exact selected target and safe brightness/duration before Run.
- Health monitoring remains off in simulation mode.
- Master brightness visibly scales simulation output.
- Invalid baked frames are rejected.
- Cache/archive actions operate only on disposable test directories.
- Profile preview and chain preview remain simulation-only.

## Implementation Record — 2026-07-25

All six planned sections are implemented.

### Delivered

1. **Application information**
   - Added installed/source-tree version resolution and an About dialog with copyable, privacy-bounded support information.
2. **Safe hardware controls**
   - Added bounded per-device advanced tests with protocol-specific patterns, duration and brightness limits, background execution, and unconditional output deactivation.
   - Added reactive mirror direction and software master brightness through settings, supervisor, session construction, simulation, and live rendering.
3. **Session reliability**
   - Added a hardware-mode-only passive health service. It probes configured LAN endpoints, never discovers new devices, does not scan BLE, and stops when hardware mode/window lifecycle ends.
   - Added queue repeat for unloaded, loaded, and active local sessions.
4. **Compilation and baked artifacts**
   - Added an optional persisted compile seed for Compile Track and Compile All. Seeded timelines record `compile_seed` metadata.
   - Added automatic companion baked-artifact validation on Show load, explicit validation, and model-based atomic Save As.
   - Existing playback selection continues to reject stale/incompatible baked artifacts before output starts.
5. **Storage and archives**
   - Added cache/capture counts, byte totals, paths, refresh, bounded clear-all, and selected-track cache clearing.
   - Added previewed capture archiving with `Keep originals` on by default, a second confirmation before deletion, verified temporary ZIP finalization, and untouched JSON sidecars.
6. **Profile library and generation**
   - Added built-in/configured-directory search, tag filtering, validation state, palette/mood summaries, and library loading.
   - Added deterministic generated pools, bounded pool size, preview, validated atomic export, structural save validation, and non-blocking harmony warnings.
   - Added simulation-only cross-fade preview and forwarded auto-palette seed, pool size, optional dwell range, and blend duration into `ProfileChain`.

Spotify-driven capture splitting remains integrated through the watcher callback and periodic queue timing source implemented immediately before these phases.

### Automated Verification

Per-section results:

- Application information: 2 passed.
- Advanced hardware/reactive controls: 163 passed.
- Passive health and repeat: 96 passed.
- Compile seed and baked artifacts: 41 passed.
- Storage/cache/archive: 29 passed.
- Profile library/generation/chaining: 163 passed.
- Integrated GUI-tagged run: 116 passed, 1 warning, with two known spatial startup expectation tests deliberately deselected.

Full repository result:

- 2,096 passed.
- 1 subtest passed.
- 10 failed.
- 1 warning.

The 10 failures reproduce in isolation and are outside this feature set:

- One test expects the literal command `ffmpeg`, while the environment correctly resolves an installed absolute FFmpeg path.
- One analyzer test expects the legacy feature-key set and rejects newer stereo/downbeat fields.
- Two spatial GUI tests expect the first tab to be selected even though the product launches into Live, which also prevents their hidden canvas from receiving the asserted key event.
- Five audio-player tests expect legacy callback/clock behavior.
- One spatial-mapper test expects a static layer where current scroll mapping produces a slice layer.

`git diff --check` reports no patch whitespace errors; it reports only existing LF-to-CRLF conversion warnings in the dirty Windows worktree.

## Manual Checks Before Connecting Lights

Keep the physical lights disconnected and leave **Config → Output target** set to **Simulation Only** for every check below.

1. **Environment and launch**
   - Activate `.venv`.
   - Run `python -c "import pandas, PySide6; print('environment ok')"` and confirm it exits successfully.
   - Launch the GUI and close it once. Confirm there is no traceback or lingering DreamSync process.
2. **About and persistence**
   - Open **Help → About DreamSync**.
   - Confirm version/Python information appears and Copy Support Info contains no tokens or device addresses.
   - Save Configuration, restart, and confirm the selected startup screen and new seeded/chain settings persist.
3. **Hardware safety UI**
   - Open Device Discovery with lights still disconnected.
   - Select an entry and open Advanced Test, but do not press Run.
   - Confirm the dialog names exactly one target, starts at reduced brightness, and constrains duration/pattern choices.
4. **Passive health**
   - In Simulation Only, confirm Config reports device health as off and Refresh is disabled.
   - Do not select hardware mode until the connection phase.
5. **Reactive simulation**
   - Start Reactive using the intended audio input.
   - Compare Center outward versus Left to right.
   - Compare master brightness at 20% and 100%; the simulation must visibly scale without clipping or freezing.
   - Stop Reactive and confirm the output owner returns to idle.
6. **Local queue and repeat**
   - Load at least two short local tracks.
   - Enable Repeat Queue, play through the final boundary, and confirm the first track becomes current without queue reordering.
   - Disable Repeat Queue and confirm the same queue stops normally after its final track.
7. **Loopback capture splitting**
   - Use a disposable capture directory.
   - Start Audio Loopback Capture and play two clearly separated tracks.
   - With Spotify loopback enabled, confirm the watcher boundary closes the first capture and the periodic timing fallback remains active.
   - Confirm two MP3s/sidecars appear and captured-show preparation proceeds without any light output.
8. **Deterministic compilation**
   - Enable Deterministic compile seed and enter a known value.
   - Compile a Track or Compile All and confirm the status includes the seed.
   - Recompile equivalent input/profile with the same seed and confirm cue/effect choices are unchanged.
9. **Baked artifact validation**
   - Load a Show with a valid companion `.show.frames.json`; confirm Validate Baked succeeds.
   - Test Save Baked As to a new file and validate the exported copy.
   - On a disposable copy, alter the source/config hash or schema and confirm validation rejects it and Require Baked playback does not start.
10. **Storage and archive**
    - Point Capture Directory at a disposable folder containing test MP3s and JSON sidecars.
    - Refresh Storage and verify counts/bytes/paths.
    - Archive with Keep originals checked; inspect the ZIP and confirm originals and JSON sidecars remain.
    - Repeat on disposable MP3s with Keep originals unchecked; confirm the second warning appears and deletion occurs only after the ZIP exists.
    - Use selected-track cache clearing only on a disposable test track. Do not use Clear All unless the displayed cache path contains nothing you need.
11. **Profile library and validation**
    - Search built-in and configured profiles by name and tag; load one and confirm palette/mood counts and validation status.
    - Preview a generated pool twice with the same seed and confirm the same names/palettes/order.
    - Save one generated profile, reload it, and confirm it is structurally valid.
    - Confirm harmony warnings are visible but do not block an intentional valid save.
12. **Profile-chain simulation**
    - Set a seed, pool size, blend duration, and dwell range.
    - Open Preview Chain Cross-fade and move the slider end to end.
    - Confirm the preview explicitly says Simulation Only and no output session/device health polling starts.
13. **Final shutdown**
    - Stop capture, playback, and Reactive.
    - Confirm runtime/capture owners show idle.
    - Close the GUI and verify capture files, archive, exported baked artifact, and generated profile are readable.

Only after all checks pass should hardware mode be selected and physical-light testing begin at low master brightness with one device.
