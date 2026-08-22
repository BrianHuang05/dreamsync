# Queue Mode Capture and Replay Controls Plan

## Objective

Make the GUI's Spotify Queue workflow operable from one runtime surface.

Today the workflow is split across tabs:

| User action | Current location |
| --- | --- |
| Start and stop audio-loopback capture | Config |
| Inspect Spotify playback queue | Queue Mode |
| Inspect compiled captures and choose a ready item | Shows |
| Start the next compiled capture | Config or a selected item on Shows |

This is difficult to discover and makes the normal delayed-playback workflow
look incomplete. Queue Mode should expose the actions and state required to
capture Spotify audio, build a compiled backlog, and start Queue-owned replay.

Configuration remains in Config. This plan does not change capture, compiler,
audio-routing, or playback semantics.

## User-facing workflow

1. In **Config**, select the capture source, Queue playback device, storage
   roots, retention policy, and Spotify loopback setting.
2. In **Live > Queue Mode**, click **Start Capture**.
3. Start Spotify playback. Queue Mode shows capture, compile, ready, and
   buffered-duration state while the browser feeds the Queue capture sink.
4. When suitable compiled material is ready, click **Play Next Captured Show**
   or select a ready item and click **Play Now**.
5. Use **Stop Capture** and **Stop Output** from the same Queue Mode surface.

The Queue Mode surface must make it clear that Spotify/browser audio is capture
input and Queue replay is the only DreamSync audio-output owner.

## Scope

### Move runtime controls into Queue Mode

Add a Queue-runtime control strip/group within the Queue Mode Live surface:

- **Start Capture** — existing `start_capture_pipeline` behavior.
- **Stop Capture** — existing `stop_capture_pipeline` behavior.
- **Play Next Captured Show** — existing next-ready-item behavior.
- **Stop Output** — existing Queue playback stop behavior.
- Capture state, pipeline state, ready count, and audio/lighting owner labels.

The controls must stay visible when Queue Mode is selected and should be
disabled only when their action is invalid. Examples:

| State | Start Capture | Stop Capture | Play Next | Stop Output |
| --- | --- | --- | --- | --- |
| Idle, no ready item | enabled | disabled | disabled | disabled |
| Capturing, no ready item | disabled | enabled | disabled | disabled |
| Capturing, ready item exists | disabled | enabled | enabled | disabled |
| Queue playback active | disabled or policy-defined | enabled | disabled | enabled |

Preserve the existing output-lease checks; Queue replay remains the sole
DreamSync audio-output owner.

### Move compiled-capture operations into Queue Mode

Render the existing **Compiled Capture Replay Queue** in Queue Mode, adjacent
to the Spotify Queue and Queue-runtime controls. Reuse the current list and
actions rather than creating a second source of truth:

- item label, state, and duration;
- Lighting Preview (Silent);
- Play Now;
- Send To Top;
- Discard.

The list must distinguish `capturing`, `compiling`, `ready`, and `playing`.
Display a total duration for Ready items when metadata is available. This is
observability only in the first implementation; it must not claim that a
specific duration guarantees no underrun.

### Retain Config for setup only

Keep these fields in Config:

- PipeWire/Pulse capture device pattern;
- Queue playback device;
- output target and simulation fallback;
- capture, audio, analysis, and compiled-show roots;
- retention, purge, debug, and capture-format settings;
- Spotify loopback enablement and credentials guidance.

Remove the runtime action row from Config after its controls are reparented.
Do not leave duplicate buttons connected to the same action: duplicate runtime
controls make active ownership and current state ambiguous.

## Implementation outline

1. Inventory the Queue Mode Live layout in `queue_panel.py`, including
   `live_queue_group`, the Spotify queue group, and the existing compiled
   replay queue widgets.
2. Reparent the existing capture and replay-action widgets into a new Queue
   Runtime group within `live_queue_group`. Reparent the existing ready-list
   widgets into the same Queue Mode composition.
3. Remove their Config layout placements while preserving widget object names,
   signal bindings, and settings persistence.
4. Centralize action enabled/visible state in the runtime render path. Derive
   it from `RuntimeModeState.capture_state`, `pipeline_state`, `ready_items`,
   and output leases; do not infer it from button text or tab visibility.
5. Add Ready-duration aggregation to the Queue runtime status display. Treat
   unknown duration as unknown rather than zero.
6. Keep the Shows-tab saved-show/library controls separate. If the replay
   queue is removed from Shows, leave a non-interactive navigation hint only
   if needed for migration; do not duplicate item management.
7. Update Help text so Queue Mode explicitly describes the sequence: configure
   route in Config, start capture in Queue Mode, wait for ready material, then
   start compiled replay.

## Tests

Add or update GUI tests to verify:

- Queue Mode shows Start/Stop Capture, Play Next Captured Show, and Stop
  Output without navigating to Config.
- Config continues to expose setup fields but no longer exposes runtime
  capture/playback buttons.
- Start Capture invokes the existing pipeline coordinator and updates Queue
  Mode status.
- A ready captured item appears in Queue Mode with state and duration.
- Play Next is disabled with no ready item and starts Queue playback when an
  item becomes ready.
- Selected-item Play Now, priority, preview, and discard still target the same
  pipeline item after widget reparenting.
- Switching Queue, Reactive, Raw Visualizer, and Spotify Live — Learning
  keeps controls mode-correct and does not expose Queue audio output controls
  in live-input-only modes.
- Existing settings migration and object-name based tests continue to pass.

## Manual acceptance

1. Launch the GUI using the Linux `spotify-queue` route.
2. Configure `dreamsync_queue_capture.monitor` and the physical playback
   device in Config.
3. Return to Queue Mode and start capture without visiting Config again.
4. Confirm browser audio is capture-only, and Queue Mode reports capture and
   compile progress.
5. Confirm a compiled item appears in the Queue Mode replay queue as Ready.
6. Start it from Queue Mode and verify only the compiled replay is audible.
7. Stop capture and playback from Queue Mode; confirm the audio-output lease,
   capture process, and PipeWire route clean up correctly.
8. Check a narrow/short desktop window and a high-DPI target display. Queue
   runtime controls must remain reachable through the intended scrolling or
   layout behavior.

## Non-goals

- Automatic duration-based buffering or underrun recovery.
- Streaming/incremental compilation.
- Changing Spotify's remote queue or treating local precompiled tracks as
  Spotify entries.
- Changing Linux PipeWire route semantics or playback ownership rules.
- Broad fullscreen/window-manager changes beyond ensuring the Queue controls
  remain reachable in the existing supported layout.

