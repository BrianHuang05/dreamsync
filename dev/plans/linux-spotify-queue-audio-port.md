# Linux Spotify Queue Audio Port Plan

Status: proposed implementation plan; no production changes in this document

Related plans:

- [linux-pipewire-capture-port.md](linux-pipewire-capture-port.md)
- [input-only-loopback-capture-plan.md](input-only-loopback-capture-plan.md)
- [streaming-show-pipeline.md](streaming-show-pipeline.md)

## Objective

Port the already-working Windows **Spotify Queue** audio behavior to Linux
PipeWire without changing Live Learning / Reactive audio behavior.

Spotify Queue must capture browser audio without making that browser stream
audible.  Once a complete track has been captured and compiled, DreamSync must
play the captured audio through the selected physical output while driving its
compiled lighting timeline.  The resulting delay is intentional: it is the
time required to finish capture and compile the track.

## Problem confirmed in the Linux port

The current Linux helper creates `dreamsync_capture` as a PipeWire
`module-combine-sink` with the physical sink as its slave.  Every application
routed to it is therefore immediately audible.

That is correct for Live Learning but wrong for Spotify Queue.  It risks
hearing the browser immediately and the queued replay later.

Separately, the CLI `session --pipeline` path creates a
`ShowPipelineWorker` and a ready queue but deliberately leaves ready captures
silent.  It does not start `ShowPlaybackConsumer`.  The GUI has an explicit
Queue-playback path, but it currently requires a user action after an item
becomes ready.

## Required mode contract

| Mode | Browser route | DreamSync audio output | When audio is heard |
| --- | --- | --- | --- |
| Reactive / Live Learning | `dreamsync_live_capture` -> physical sink | Never | Immediately, from PipeWire |
| Spotify Queue capture | `dreamsync_queue_capture` -> no physical sink | Never while capturing | Not during capture |
| Spotify Queue playback | Browser remains on queue-capture sink | `AudioPlayer` -> selected physical device | Once the completed item is compiled |

The player output device must never be either DreamSync capture sink or its
monitor source.  The active queue player is the sole DreamSync audio-output
owner.

## Non-goals

- Do not change Windows VB-Cable routing or the Windows queue workflow.
- Do not replay input PCM in Reactive, Visualizer, or Live Learning modes.
- Do not change device LAN/BLE transport logic.
- Do not require an internet connection beyond the existing Spotify metadata
  integration.

## Implementation phases

### 1. Establish the Windows parity baseline

Before editing Linux code, run the working Windows Spotify Queue workflow and
record:

- the input endpoint used by FFmpeg;
- the browser/default output endpoint while capture is active;
- the explicit playback endpoint used by `AudioPlayer`;
- whether playback begins automatically or requires the existing Queue
  control;
- queue ordering, failure handling, and shutdown behavior.

Turn this into an acceptance fixture in the implementation notes.  The Linux
port must match these externally observable behaviors rather than infer a
second, incompatible queue design from the Live Learning route.

### 2. Make Linux routing explicitly mode-aware

Replace the single-purpose behavior of
`dev/scripts/setup_linux_pipewire_capture.sh` with an idempotent route helper
that supports two named modes:

1. `live-learning`: retain the current combine-sink route to the selected
   physical sink.  Its monitor is `dreamsync_live_capture.monitor`.
2. `spotify-queue`: create `dreamsync_queue_capture` as a capture-only
   PipeWire/Pulse sink with a monitor but **no physical-sink slave**.

The helper must:

- save the prior default sink and restore it on teardown;
- create only uniquely named DreamSync modules and remove only those modules;
- accept an explicit physical sink and validate it with `pactl list short
  sinks`;
- report the exact browser target, capture monitor, and physical playback
  sink;
- fail clearly if `pactl` or the Pulse compatibility server is unavailable.

Do not leave the queue capture sink as the desktop-wide default after browser
launch.  The launcher should route only the intended browser stream to the
queue sink (or temporarily set it as default while launching the browser, then
restore the physical default).  This prevents unrelated desktop audio from
being silently captured.

Add a matching teardown command and ensure SIGINT/SIGTERM invokes it on a
clean session exit.

### 3. Plumb the selected route into capture configuration

Add a small platform-neutral route descriptor, for example
`AudioRoute(mode, capture_source, physical_sink, browser_sink)`.  The queue
route must pass `dreamsync_queue_capture.monitor` to
`CaptureOrchestrator`/FFmpeg; Live Learning continues to use its audible
monitor source.

Expose the route mode in the CLI and GUI settings rather than overloading
`--capture-source` alone.  `--capture-source` remains an advanced override,
but a Queue command must reject a capture source that is also the selected
playback target.

### 4. Complete Queue playback orchestration

Use the existing worker/queue contracts instead of creating another compiler
path:

```text
CaptureOrchestrator
  -> ShowPipelineWorker
  -> ready queue
  -> Queue playback coordinator / ShowPlaybackConsumer
  -> AudioPlayer (physical device) + ShowPlaybackRuntime (lights)
```

Implement the same start policy proven by the Windows baseline:

- If Windows starts playback automatically, start one consumer/coordinator
  when Spotify Queue starts and let it block on the ready queue.
- If Windows requires explicit Queue play, preserve that policy in Linux and
  make the UI state unambiguous: `capturing`, `compiling`, `ready`, and
  `playing`.

For the CLI, remove the misleading compile-only behavior when queue playback
is requested.  Construct `ShowPlaybackConsumer` with the selected **physical**
`--playback-device`, run it on its managed thread, and shut it down in this
order:

1. stop new capture and Spotify timing updates;
2. finalize the active segment;
3. stop/drain or cancel compilation according to the selected policy;
4. stop the player and turn down/deactivate lights cleanly;
5. restore the prior PipeWire route.

For the GUI, reuse `RuntimeSupervisor` and `PipelineCoordinator`; do not
introduce a competing queue state machine.  Route selection and playback
device must flow through the existing output-lease mechanism so no live mode
can steal Queue audio output.

### 5. Guardrails and observability

Add structured status events for:

- route creation, selected browser sink, capture monitor, and physical sink;
- capture segment saved, compile started/completed/failed, ready-queue depth;
- playback start/end, selected output device, and audio-output owner;
- route teardown/restoration and any failed cleanup.

At startup, reject configurations where the selected output device resolves to
either capture sink/monitor.  Surface a recovery command that restores the
previous PipeWire default after a crash.

### 6. Tests

Add or extend tests for:

- PipeWire helper command generation for both routes; queue mode must not
  include a physical sink as a combine-sink slave.
- idempotent setup/teardown and preservation of a non-DreamSync default sink.
- Linux capture resolution selects the monitor belonging to the requested
  route mode.
- Queue mode starts the approved playback mechanism and passes the selected
  physical output device to `AudioPlayer`.
- Queue mode never opens player output while only capturing or compiling.
- Live Learning continues to have no `AudioPlayer` / playback-consumer path.
- rejecting a capture-sink or monitor as Queue playback output.
- FIFO ordering, compile failure recovery, Ctrl+C shutdown, and no lingering
  FFmpeg/player/PipeWire modules.

Update the current GUI test asserting that ready items are never automatically
armed only if the Windows-baseline behavior requires automatic Queue playback.

## Manual acceptance test on the Surface

1. Enable Bluetooth, connect the intended physical audio device, and select
   the current `dev/devices.yaml` configuration.
2. Start **Spotify Queue** routing and confirm:
   - the browser is on `dreamsync_queue_capture`;
   - `dreamsync_queue_capture.monitor` has signal;
   - the browser is not audible directly through the physical sink.
3. Start the queue pipeline; play two complete browser tracks.  Confirm the
   first item transitions `capturing -> compiling -> ready -> playing`.
4. Confirm queued playback begins only under the Windows-parity policy and is
   audible exactly once through the selected physical output.
5. Confirm the show timeline starts with that replay, controls every
   configured LAN and BLE device, and no audio feeds back into capture.
6. Confirm the second item plays after the first in capture order.
7. Stop during capture and during playback.  Confirm no stale audio, lights,
   FFmpeg process, playback stream, or virtual PipeWire module remains, and
   the normal desktop output is restored.

## Definition of done

- Live Learning remains immediate and PipeWire-audible, with no DreamSync
  replay.
- Spotify Queue browser audio is capture-only until its compiled replay.
- Every queued track is heard once, on the configured physical output, with
  synchronized lights.
- CLI and GUI expose the same route semantics and report the active owner of
  audio output.
- Windows behavior is unchanged and Linux manual acceptance passes on the
  Surface with LAN and BLE hardware connected.
