# Input-Only Loopback Capture and Queue Audio Ownership Plan

Status: proposed implementation plan; no implementation changes in this document

Target branch: `codex/loopback-capture-input-only`

Related plan: [spotify-connected-learned-live-plan.md](spotify-connected-learned-live-plan.md)

## 1. Objective

Implement loopback capture as a read-only tap on a configurable audio input such as `CABLE Output`. Capturing audio must never become part of the audible playback path, must not re-output the captured samples, and must not add audible delay.

At the same time, establish a hard application rule: Queue mode is the only DreamSync mode allowed to produce audio. Reactive Live, Raw Visualizer, and Spotify Live — Learning may read audio and render lighting, but they may not instantiate an audio player, open an output stream, monitor an input to an output, or replay captured audio.

Also add an exposed, persistent sensitivity control to Raw Visualizer mode.

## 2. Product decisions

These decisions are requirements, not implementation suggestions:

1. `CABLE Output` is an input source from DreamSync's perspective.
2. Loopback capture means “copy samples from an input endpoint.” It does not mean “play the captured samples through another endpoint.”
3. Spotify, the browser, Windows, VB-Cable, or another external mixer remains responsible for audible Spotify playback. DreamSync does not insert itself into that route.
4. Starting or stopping capture must not change the audible route, device, volume, or latency.
5. Queue mode is the only mode that may own DreamSync audio output.
6. Queue mode supports two source policies:
   - precompiled-only playback;
   - Spotify-pipeline ingestion with compilation of cache misses, followed by Queue-owned playback.
7. Delayed captured-show replay is a Queue workflow, not a property of loopback capture and not an independent live-output mode.
8. Reactive Live, Raw Visualizer, and Spotify Live — Learning share the same input-only audio-analysis foundation where practical.
9. Runtime settings that affect capture or analysis apply without restarting the application, unless an active stream must be stopped and reopened. Reopening the input stream must still leave audible playback untouched.

## 3. Terminology

- **Audible output**: PCM sent by DreamSync to a speaker, headphone, HDMI, or virtual playback endpoint.
- **Input tap**: an input-only stream that receives a copy of PCM samples from an input endpoint.
- **Capture**: optionally retaining input-tap samples for segmentation, encoding, analysis, or learned-cache creation.
- **Analysis consumer**: Reactive Live or Raw Visualizer processing PCM without retaining or playing it.
- **Recording consumer**: background-learning logic that retains and encodes selected PCM ranges.
- **Output lease**: exclusive permission for a DreamSync component to produce audible audio.
- **Lighting output**: simulated or physical light frames. This is independent of audible output.
- **Spotify pipeline**: metadata observation, capture, compilation, and Queue ingestion. It does not imply direct audio monitoring.

## 4. Required mode behavior

| Mode | Reads input PCM | May retain/encode PCM | Produces lighting | DreamSync audible output |
| --- | --- | --- | --- | --- |
| Queue: precompiled only | No, unless a separate optional analyzer is explicitly enabled later | No | Yes, from precompiled show | Yes |
| Queue: Spotify pipeline/hot compile | Yes, while ingesting a missing track | Yes | Yes, when the item is ready for Queue playback | Yes, during Queue playback only |
| Reactive Live | Yes | No | Yes | Never |
| Raw Visualizer | Yes | No | Yes | Never |
| Spotify Live — Learning | Yes | Yes, for cache misses when enabled | Yes, learned show or reactive fallback | Never |

The output lease must encode this table. Simulation-only affects lighting output; it does not grant audio-output permission to a non-Queue mode.

## 5. Audio flows

### 5.1 Connected live and learning modes

```text
Spotify/browser
    -> Windows/VB-Cable audible routing ---------------------> speakers
    -> CABLE Output input endpoint
         -> DreamSync input tap
              -> reactive/raw analysis -> simulated or physical lights
              -> optional recorder -> learned cache/compiler
```

There is no DreamSync arrow from the input tap to the speakers. Analysis and encoding may lag under load, but that lag cannot delay the audible path because DreamSync is not in that path.

### 5.2 Queue playback

```text
local Queue item (audio + compiled show)
    -> DreamSync AudioPlayer -> configured Queue output device
    -> show runtime ---------> simulated or physical lights
```

Queue mode owns both clocks for local playback: the audio player is the playback clock and the show runtime follows it.

## 6. Existing implementation to retain or adapt

The existing Spotify learned-live plan and commit `c776237` contain useful pieces that should be retained selectively:

- Spotify metadata polling and track identity;
- learned-track store and cache-hit lookup;
- background learning state and retention settings;
- reactive fallback and compiled-show selection;
- pipeline segmentation, encoder, metadata sidecars, and compiler worker;
- existing Reactive Live and Raw Visualizer renderers;
- existing Queue `AudioPlayer` and timeline playback session;
- current native input-rate resolution work in `audio_device_service.py`, `runtime_supervisor.py`, and its focused tests.

The current `PipelineCoordinator` already describes capture and compilation as independent from active output. Preserve that separation, but remove the assumption that capture should automatically arm or switch to captured-show playback.

The current FFmpeg `CaptureProcessManager` is already input-only at the process boundary: it opens DirectShow input and writes PCM to stdout. Its output pipe is data, not audible output. This can be reused for recording if it satisfies device identity, native-rate, stop-latency, and contention requirements.

## 7. Selective reuse from later branches

Later branches may and should be mined for proven fixes, especially changes developed during Live — Learning diagnosis. Do not merge or cherry-pick an entire later branch. The implementation begins with a source inventory:

1. Record the baseline commit and dirty working-tree state.
2. Enumerate local and remote branches, reflog entries, and relevant commits.
3. Diff candidates by subsystem:
   - audio device enumeration and native sample-rate negotiation;
   - Reactive and Raw start/stop control restoration;
   - Live — Learning mode switching and hot-applied settings;
   - capture-device matching across WASAPI, DirectSound, MME, WDM-KS, and DirectShow;
   - shutdown fixes that eliminate stale callbacks and ghost beat detection;
   - simulation-frame routing;
   - cache controls and Spotify queue UI.
4. Classify each candidate as reusable, reusable after adaptation, superseded, or rejected.
5. Reapply small changes on this branch with focused tests after each subsystem.

A later-branch change is rejected if it does any of the following:

- opens an audio output stream outside Queue mode;
- forwards input samples to an output device;
- couples capture start/stop to audible playback;
- restores captured-show replay as a live-mode behavior;
- hard-codes `44100` when the selected device's native rate differs;
- identifies a device only by a transient numeric index;
- blocks the audio callback on encoding, compilation, disk I/O, UI work, or lighting output;
- removes working Reactive or Raw controls as a side effect of Live — Learning UI reuse.

Each reused portion should keep its original commit hash in the implementation notes or commit message for traceability.

## 8. Proposed architecture

### 8.1 Separate input capture from output playback

Introduce an explicit input-only service boundary, tentatively `AudioInputTapService`. Its public contract should expose:

- resolve and validate an input device;
- report the selected backend, stable identity, native sample rate, and input channel count;
- open one input stream;
- publish timestamped PCM blocks to registered consumers;
- add and remove consumers at runtime;
- stop promptly and idempotently;
- report overruns, dropped consumer blocks, callback timing, and last-sample time.

It must not accept an output device, construct `AudioPlayer`, create `sounddevice.OutputStream`, or contain a monitor/listen/replay option.

### 8.2 Use a non-blocking fan-out

Prefer one input owner with multiple consumers over opening the same VB-Cable endpoint independently for Reactive analysis and recording. This reduces backend contention and makes the input lifecycle observable.

The input callback performs only bounded work:

1. copy or reference the incoming PCM block;
2. attach monotonic timing and stream-frame position;
3. enqueue it to each active consumer using a bounded, non-blocking queue;
4. update lightweight counters.

It must never wait for a consumer. A slow consumer drops or coalesces its own blocks according to policy:

- analysis consumer: keep the newest data and drop stale blocks;
- recorder consumer: use a larger bounded queue and surface an explicit capture-gap error if it falls behind;
- UI metering: coalesce to the newest meter value.

Encoding, MP3 writing, segmentation, metadata calls, Spotify polling, compilation, lighting rendering, and GUI signals all run off the callback thread.

### 8.3 Adapt the current capture pipeline

Refactor `CaptureOrchestrator` so its PCM source is injectable. Support two adapters during migration:

- existing FFmpeg/DirectShow source for standalone recording;
- shared input-tap consumer for simultaneous analysis and learning.

The split processor, boundary queue, metadata writer, retention buffer, and encoder worker should remain downstream of the PCM-source interface. They must not know about speakers or playback devices.

If the shared input tap cannot provide a lossless recording stream on a target backend, retain the FFmpeg recorder as a separate input reader for that backend, but still enforce input-only behavior. This fallback must be validated for shared-mode device contention and must not be the default merely because it already exists.

### 8.4 Make audio ownership explicit

Split the existing broad output state into two concepts:

- `lighting_output_lease`: who sends simulated or physical light frames;
- `audio_output_lease`: who may instantiate or control audible playback.

`audio_output_lease.owner` may be empty or `queue`. Any request from Reactive, Raw, Live — Learning, capture, preview, or compiler code must fail fast with a clear developer-facing invariant error.

Centralize Queue audio-player creation behind one service method. Avoid direct `AudioPlayer` construction in mode coordinators. Tests can then prove that no non-Queue path reaches the output boundary.

Simulation previews that currently play captured MP3s require a product-level distinction:

- a lighting-only preview may remain outside Queue and must be silent;
- an audible captured-show preview must be launched as a temporary Queue item and acquire the Queue audio lease.

### 8.5 Reframe captured-show replay

Deprecate `switch_to_pipeline_playback()` as a live-mode transition. Replace it with a Queue ingestion operation such as `enqueue_ready_pipeline_items()`.

The capture pipeline produces ready artifacts. Queue policy decides whether and when those artifacts play. Capture completion must never start audio automatically unless Queue mode is active and its configured autoplay policy permits it.

### 8.6 Queue source policies

Add a persisted Queue source policy with at least:

- `precompiled_only`: accept only items with valid audio and compiled-show artifacts; reject or visibly mark misses without starting background capture;
- `spotify_hot_compile`: observe Spotify metadata, capture cache misses, compile them, and enqueue ready items. Playback still occurs through the Queue audio service.

Define autoplay, purge-after-playback, shuffle, and repeat only in Queue terms. They should not appear in or affect Spotify Live — Learning.

## 9. Device selection and format negotiation

### 9.1 Stable device identity

Do not persist only PortAudio's numeric device index. Persist a descriptor containing, at minimum:

- device name;
- host API/backend;
- input direction;
- optional backend-specific stable identifier when available.

Resolve the descriptor to the current runtime index whenever devices are refreshed or a stream starts. Detect ambiguity instead of silently selecting the first `CABLE Output` across MME, DirectSound, WASAPI, and WDM-KS.

The UI may display all variants but should recommend WASAPI shared mode on Windows when supported. The selected value must identify the exact backend variant.

### 9.2 Native sample rate

At stream start:

1. query the selected input device;
2. validate that it has input channels;
3. probe or use its native/default sample rate;
4. open at a supported rate;
5. report the effective rate in diagnostics.

Do not assume `44100`. A 48 kHz VB-Cable endpoint must open at 48 kHz. If an artifact pipeline requires another rate, resample in a downstream worker, never in the input callback and never by changing the audible route.

Frame counts, Spotify boundary conversion, drift detection, segment minimums, and encoder configuration must use the effective capture rate rather than constants derived from 44.1 kHz.

### 9.3 Runtime setting changes

Changes to the input device, channel count, or stream format require a controlled input-stream reopen:

1. stop accepting new consumers;
2. stop and join the old input stream within a bounded timeout;
3. clear stale analysis buffers and detector state;
4. resolve and validate the new descriptor;
5. start the new input stream;
6. reattach requested consumers;
7. publish one coherent state transition to the GUI.

No application restart and no audio-output restart should be required.

## 10. Reactive, Raw, and Live — Learning integration

### 10.1 Reactive Live

Move Reactive's PCM acquisition behind the input-tap consumer interface. Preserve its existing controls, start/stop behavior, profiles, palettes, simulation output, and hardware output.

Stopping Reactive must unregister its consumer, flush analyzer state, stop beat/effect timers, and update the GUI promptly. No “input hot” or beat events may continue after stop.

### 10.2 Raw Visualizer

Raw Visualizer uses the same analysis consumer but keeps its frequency-gradient renderer and palette behavior. It must retain its own visible Start/Stop controls and must not depend on Live — Learning state.

Add a `Sensitivity` dial/slider and adjacent numeric value. Use a user-facing range of `0–100`, persisted as sensitivity rather than exposing a tiny floating-point noise threshold.

Map sensitivity monotonically and logarithmically to the internal noise floor so the useful range has adequate control. A proposed mapping is:

```text
threshold = 10 ** lerp(log10(0.05), log10(0.0001), sensitivity / 100)
```

Thus higher sensitivity means a lower activation threshold. Before implementation, validate the endpoints against recorded quiet-room, ordinary music, and loud-source samples; adjust constants without changing the user-facing direction.

The UI should show `Sensitivity: N` and may show the effective threshold in diagnostics. Changes apply live to the active renderer without reopening the audio stream. Existing `raw_visualizer_noise_threshold` settings should migrate by inverse mapping and be preserved until one successful save writes the new sensitivity field.

### 10.3 Spotify Live — Learning

Reuse the complete Reactive control panel and renderer behavior where applicable, but give the mode its own top-level Start/Stop controls and status text. Hide Reactive's duplicate Start button while this mode is selected.

Starting the mode registers:

- one analysis consumer for immediate reactive lighting or learned-show timing;
- one recording consumer only when learning is enabled and the current track is a cache miss;
- the Spotify metadata observer.

A learned cache hit uses Spotify progress as the compiled-show clock while the input tap remains available for diagnostics/fallback. A cache miss immediately runs reactive lighting and may record in the background. Neither path produces audio.

Stopping the mode unregisters both consumers, cancels metadata polling, stops capture/encoding safely, clears detector state, and stops lighting output. It must not stop Spotify, change the Windows default device, or touch speaker playback.

## 11. GUI and configuration changes

### Config tab

- Rename ambiguous “Output audio” labels so they explicitly say `Queue playback output`.
- Rename “System-loopback device” to `Capture input`.
- Store the exact input device/backend identity, not only a text pattern.
- Display effective sample rate, channels, backend, and validation state.
- Remove or relabel “Captured-show playback device” as `Queue playback output`; it must not live in capture settings.
- Keep learning retention settings with Live — Learning settings.
- Apply changes live through the controlled reopen path.

### Live tab

- Preserve Queue, Reactive, Raw Visualizer, and Spotify Live — Learning mode buttons.
- Preserve per-mode Start/Stop controls and keyboard behavior.
- Show input status separately from Queue playback status.
- In Live — Learning, show Spotify queue and learning/cache status; do not show local Queue playback, shuffle, repeat, or cue-file actions.
- In Queue, show playback, shuffle, repeat, and source policy; do not imply that input capture is audible monitoring.
- Add Raw Visualizer sensitivity beside its other raw controls.

### Diagnostics

Expose:

- resolved input descriptor and runtime index;
- host API/backend;
- effective rate and channels;
- input-tap state and active consumers;
- callback/queue overruns and dropped blocks per consumer;
- last input timestamp and input level;
- lighting output owner;
- audio output owner, which must be `none` or `queue`;
- Queue playback device when Queue owns audio;
- shutdown duration and stale-callback count.

Avoid text such as “monitoring to speakers,” because DreamSync does not provide that function.

## 12. State and lifecycle requirements

Model input, lighting, compilation, and audio playback as orthogonal state machines. A single `active_output_mode` string is insufficient to describe them safely.

Required transitions:

- switching Reactive -> Raw may reuse the open input tap but must replace consumers and reset analysis state;
- switching Reactive/Raw -> Live — Learning may reuse the tap and add/remove consumers without touching audio output;
- switching any input-only mode -> Queue stops input consumers unless Queue's hot-compile policy needs recording;
- leaving Queue releases the audio output lease and stops its player before another mode starts;
- stopping capture does not stop lighting analysis unless that consumer explicitly depends on recorded PCM;
- capture/encoder failure downgrades learning status but does not interrupt reactive lighting or audible Spotify playback;
- GUI close stops all consumers and workers in a bounded order.

Every asynchronous callback must carry a session/generation identifier so a callback from a stopped session cannot update current state or render a late frame.

## 13. Implementation phases

### Phase 0: Baseline and branch mining

- Preserve the current working native-rate fix and its tests.
- Inventory later branches and create the candidate-reuse table described above.
- Add regression tests for working Reactive and Raw start/stop controls before structural edits.
- Capture baseline manual behavior with and without VB-Cable selected.

### Phase 1: Enforce audio ownership

- Add separate lighting and audio leases.
- Centralize `AudioPlayer` construction behind Queue playback.
- Add fail-fast guards for non-Queue output attempts.
- Convert audible previews and captured-show replay to Queue operations.
- Remove automatic capture-to-playback transitions.

### Phase 2: Stabilize device identity and format negotiation

- Complete native-rate probing and propagation.
- Introduce stable input descriptors and runtime resolution.
- Validate direction, channels, backend, and supported format before start.
- Add controlled runtime reopen.

### Phase 3: Introduce the input tap

- Define PCM block, source, consumer, and diagnostics interfaces.
- Implement the non-blocking shared input owner and bounded fan-out.
- Adapt Reactive and Raw to analysis consumers.
- Verify prompt stop and analyzer reset.

### Phase 4: Adapt recording and learning

- Make `CaptureOrchestrator` accept an injected PCM source.
- Attach the recorder only for configured cache misses.
- Keep segmentation/encoding/compilation off the callback thread.
- Validate retention policies and partial-track rejection.
- Integrate Live — Learning cache-hit and fallback behavior.

### Phase 5: Queue pipeline policy

- Add `precompiled_only` and `spotify_hot_compile` policies.
- Enqueue ready compiled artifacts rather than switching live output modes.
- Scope autoplay, repeat, shuffle, purge, and playback device to Queue.

### Phase 6: Raw Visualizer sensitivity

- Add setting, migration, mapping helpers, GUI dial/value, and live update.
- Calibrate mapping against sample recordings.
- Preserve existing raw palette and strip-preview controls.

### Phase 7: UI cleanup and end-to-end validation

- Separate capture-input and Queue-output labels/settings.
- Make mode-specific panels and buttons deterministic.
- Add diagnostics and test guide updates.
- Run automated and VB-Cable manual acceptance suites.

Each phase should land as a focused commit. Do not combine the shared-input refactor, Queue policy, and UI restructuring into one rollback-sized change.

## 14. Automated test plan

### Unit tests

- input tap opens `InputStream` only and has no output-device argument;
- starting Reactive, Raw, or Live — Learning never constructs `AudioPlayer` or an output stream;
- only Queue can acquire the audio output lease;
- an audio lease violation fails immediately and records diagnostics;
- capture completion enqueues an artifact but does not start playback outside Queue;
- native 48 kHz devices are opened at 48 kHz;
- effective rate propagates to frame timing, split boundaries, drift calculation, and encoder settings;
- stable device descriptors resolve correctly after numeric indices change;
- ambiguous `CABLE Output` names require backend disambiguation;
- slow analysis consumers drop stale blocks without blocking the producer;
- recorder overflow reports a capture gap and does not block the producer;
- stop is idempotent and removes all consumers;
- stale callbacks are ignored after generation change;
- Raw sensitivity mapping is monotonic, bounded, invertible within rounding tolerance, persistent, and hot-applied;
- legacy raw threshold settings migrate correctly;
- Queue source policies accept and reject the intended item types.

### Integration tests

- feed synthetic PCM to analysis and recorder consumers simultaneously and verify identical source timing;
- verify recorded PCM is never passed to an output API;
- run Reactive and Raw sequentially over one tap and verify analyzer state does not leak;
- run Live — Learning cache miss with simulated lights and verify frames begin before compilation finishes;
- simulate a slow encoder and verify reactive frame cadence remains within tolerance;
- stop Live — Learning during capture and verify no beat, input-hot, or frame callbacks after the stop deadline;
- change input descriptor/rate at runtime and verify controlled reopen without touching Queue output;
- enqueue a compiled capture and verify it remains silent until Queue starts it;
- start Queue playback and verify it is the sole audio owner.

### Regression tests

- Reactive Start/Stop buttons remain present and functional;
- Raw Visualizer Start/Stop and preview remain functional;
- Queue saved-show and compiled-track playback still emit audio;
- simulation lighting frames render in every lighting-producing mode;
- Live — Learning top controls, cache settings, and keyboard shortcuts remain mode-correct;
- application close leaves no capture, encoder, analyzer, or player thread running.

## 15. Manual VB-Cable acceptance test

1. Configure Windows/Spotify/VB-Cable so Spotify is already audible at the speakers before DreamSync starts.
2. Confirm `CABLE Output` is listed as an input endpoint and note its backend and native rate.
3. Start DreamSync with no mode active. Confirm it does not change the audible route.
4. Select the exact WASAPI `CABLE Output` capture input and verify the effective rate, expected to be 48 kHz on the currently observed setup.
5. Start Reactive in simulation. Confirm lights react and Spotify audio remains continuous with no new echo, monitoring path, or perceptible latency.
6. Stop Reactive. Confirm input-hot, beats, and frames stop within the defined deadline.
7. Repeat for Raw Visualizer. Sweep sensitivity from low to high and verify the response changes immediately without reopening the input or affecting audio.
8. Start Live — Learning on a cache miss. Confirm reactive frames appear immediately, background capture advances, and Spotify remains the only audible source.
9. Stop Live — Learning mid-track. Confirm DreamSync stops analysis/capture promptly while Spotify audio continues unchanged.
10. Start a learned cache hit. Confirm compiled lighting follows Spotify progress without DreamSync audio output.
11. Switch to Queue precompiled-only and play a local item. Confirm DreamSync now owns audible output and reports `audio owner: queue`.
12. Stop Queue and confirm the audio lease returns to none.
13. Exercise Queue Spotify hot-compile. Confirm capture and compile create Queue-ready artifacts, but no artifact plays until Queue policy starts it.
14. Compare CPU load, input queue depth, dropped blocks, frame cadence, and stop latency with capture disabled and enabled.

Acceptance thresholds should be recorded before implementation testing. Initial targets:

- no additional audible buffering because DreamSync is absent from the audible path;
- analysis frames visible within 250 ms of received PCM under normal load;
- no input-hot, beat, or rendered-frame callback more than 1 second after stop;
- no callback blocking event above one input block duration;
- no recorder gap during a 30-minute normal-load run;
- no non-Queue audio output API calls in automated traces.

## 16. Likely files affected during implementation

Exact names may change after branch mining, but the expected areas are:

- `src/dreamsync/gui/services/audio_device_service.py`
- `src/dreamsync/gui/services/runtime_supervisor.py`
- `src/dreamsync/gui/services/session_service.py`
- `src/dreamsync/gui/models/runtime_mode_state.py`
- `src/dreamsync/gui/models/runtime_routing_state.py`
- `src/dreamsync/gui/models/capture_settings.py`
- `src/dreamsync/gui/settings.py`
- `src/dreamsync/gui/main_window.py`
- `src/dreamsync/gui/widgets/queue_panel.py`
- `src/dreamsync/capture/capture_process.py`
- `src/dreamsync/capture/orchestrator.py`
- `src/dreamsync/spotify/learned_live_session.py`
- `src/dreamsync/reactive/` and/or the existing Reactive session implementation
- focused tests under `dev/tests/`

A new input abstraction should live in a small audio/capture service module rather than making `main_window.py` the stream owner.

## 17. Migration and compatibility

- Preserve legacy `device_pattern` as a fallback only when no stable descriptor exists; prompt the user to save the resolved exact device.
- Migrate `pipeline_playback_device_id` to Queue playback configuration.
- Migrate `raw_visualizer_noise_threshold` to the new sensitivity value with a reversible mapping.
- Preserve learned-cache artifacts, captured MP3s, metadata sidecars, and retention settings.
- Do not delete or rewrite user capture files during migration.
- Keep CLI capture commands input-only and document that they never monitor to speakers.
- If a later branch changed settings schema, provide versioned migration rather than silently accepting mismatched fields.

## 18. Out of scope

- Installing or configuring VB-Cable automatically.
- Changing Windows default devices or enabling “Listen to this device.”
- Providing a DreamSync software-monitoring path from capture input to speakers.
- Compensating for latency by delaying audible audio.
- Replacing Spotify's player or browser.
- Redesigning the light renderer or learned-show compiler beyond changes needed to consume the shared input clock.
- Removing the existing working Reactive or Raw mode controls.

## 19. Definition of done

The implementation is complete when all of the following are true:

- selecting `CABLE Output` opens a supported native-rate input stream reliably;
- loopback capture records/analyzes a copy of input audio and never re-outputs it;
- Reactive, Raw, and Live — Learning create no DreamSync audible output;
- Queue is the only component capable of acquiring the audio output lease;
- learned cache misses capture and compile without delaying audible Spotify playback or reactive lighting;
- compiled captures enter Queue rather than auto-playing as a live-mode transition;
- simulation and physical lighting frames render normally while capture is active;
- stopping a mode promptly removes its consumers and produces no ghost input/beat/frame state;
- Raw Visualizer exposes a useful persistent sensitivity dial/value that updates live;
- settings can be changed without restarting the application;
- selected fixes from later branches have been reapplied individually with traceability and regression coverage;
- automated tests and the full VB-Cable manual acceptance test pass.
