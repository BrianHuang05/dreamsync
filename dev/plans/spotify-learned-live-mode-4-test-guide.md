# Mode 4 — Spotify Live Learning Test Guide

## Readiness verdict

**Ready for controlled manual testing in simulation, followed by hardware.**

Mode 4 is not yet production-validated. The implementation and focused automated
tests are complete, but the real Windows audio topology must still pass the
Phase 7 validation gate from
[`spotify-connected-learned-live-plan.md`](spotify-connected-learned-live-plan.md).
In particular, the target PC must prove that Reactive Live and FFmpeg can read
`CABLE Output` concurrently without device-busy errors, capture gaps, Reactive
drops, audible duplication, or unstable lighting.

This verdict was checked on 2026-08-03 against the current working tree.

### Evidence reviewed

- `SpotifyLearnedLiveSession` selects a compatible cached show or starts
  Reactive immediately on a cache miss.
- `RuntimeSupervisor` keeps a single light-output owner and starts the learning
  capture/compile pipeline independently.
- Spotify track ID and URI flow through queue timing, capture metadata, cache
  identity, analysis, show, and manifest persistence.
- Eligibility rejects mid-track starts, pause, seek, skip, duration mismatch,
  capture gaps/restarts, unreadable audio, and trivial audio.
- Learned publication is transactional: analysis and reloadable show must exist
  before a complete manifest is published and source retention is applied.
- The GUI exposes **Spotify Live — Learning (experimental)**, Reactive/compiled
  badges, background learning states, retention controls, and learned-library
  counts.
- Focused automated readiness gate: **104 passed in 10.49 seconds**.
- The full `dev/tests` run exceeded a five-minute command timeout and produced no
  final result. It did not emit a test failure, but it is **not** recorded as a
  passing full-suite run.

### Current test boundary

The supported manual path uses the GUI and the default learned cache at:

```text
%USERPROFILE%\.dreamsync\cache\spotify_{spotify_track_id}\
```

The GUI currently uses that default cache. Do not use a custom cache root for
this validation: the learned store can accept a custom root, while the GUI
pipeline worker currently constructs the default cache internally.

## What a passing test proves

A complete pass proves that:

1. An uncached Spotify track gets Reactive lighting immediately.
2. The same play is captured without taking lighting ownership from Reactive.
3. A naturally completed, eligible play creates durable analysis, compiled
   show, and manifest artifacts keyed by Spotify track ID.
4. The current play stays Reactive even if compilation finishes before the song
   ends.
5. A later play of that Spotify ID starts the compiled show at Spotify's current
   position.
6. Pause, resume, seek, and skip behave safely.
7. The independent sounddevice and FFmpeg readers are stable on the target PC.
8. Hardware output behaves the same as simulation without overlapping output
   authorities.

## Required equipment and software

- Windows test PC with the current DreamSync checkout.
- Project virtual environment with GUI, YAML, and Spotify dependencies.
- FFmpeg available on `PATH`.
- VB-Audio Virtual Cable installed.
- Spotify desktop app and a Spotify account authorized for DreamSync.
- Three full tracks that have not already been learned with the selected
  DreamSync profile. Prefer tracks of 2–4 minutes with clean natural
  transitions.
- Speakers/headphones configured to monitor Spotify routed through VB-Cable.
- For the hardware phase, the target Govee devices, LAN access, and a valid
  device/room-layout YAML file.

Use tracks you are willing to play from beginning to end. Crossfade, autoplay
overlap, manual scrubbing, ads, podcasts, and local Spotify files can complicate
the first baseline and should be disabled or avoided until the happy path passes.

## Test record

Create a copy of this table in the test report before starting.

| Field | Value |
|---|---|
| Date/time | |
| Tester | |
| Commit | |
| Windows version | |
| Python version | |
| FFmpeg version | |
| VB-Cable version | |
| Spotify app version | |
| DreamSync profile | |
| Device config | |
| Output phase | Simulation / Hardware |
| Capture device pattern | `CABLE Output` |
| Reactive input native rate | |
| Learned capture output rate | 44100 Hz |
| Retention policy | Keep recent, limit 10 |

Record the three baseline tracks separately:

| Track | Artist/title | Spotify track ID | Duration | Initially uncached? |
|---|---|---|---:|---|
| A | | | | |
| B | | | | |
| C | | | | |

The Spotify track ID is the value after `/track/` in a Spotify track URL, or the
value after `spotify:track:` in its URI.

## 1. Automated preflight

Open PowerShell in the repository root:

```powershell
Set-Location C:\Users\brian\dreamsync
& .\.venv\Scripts\python.exe --version
& .\.venv\Scripts\python.exe -m dreamsync --help
ffmpeg -version
```

Run the focused gate used for this readiness review:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q `
  dev/tests/test_spotify_learned_live_session.py `
  dev/tests/test_capture_eligibility.py `
  dev/tests/test_learned_track.py `
  dev/tests/test_show_pipeline_worker_learned.py `
  dev/tests/test_gui_runtime_supervisor.py `
  dev/tests/test_gui_settings_recovery.py `
  dev/tests/test_gui_storage_service.py `
  dev/tests/test_spotify_models.py `
  dev/tests/test_queue_watcher.py `
  dev/tests/test_timing_integrator.py `
  dev/tests/test_metadata_writer.py `
  dev/tests/test_capture_scanner.py `
  dev/tests/test_v3_position.py `
  dev/tests/test_v3_session.py
```

Expected: all tests pass. Stop and investigate any failure before using real
devices.

Optionally run the full regression suite with a generous timeout:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q dev/tests
```

Record the pass/fail/skip totals and elapsed time. A full-suite failure must be
triaged to determine whether it affects Mode 4 or another subsystem.

## 2. Spotify and VB-Cable preflight

### 2.1 Verify existing Spotify authorization

Spotify authorization is a deployment prerequisite, not a Mode 4 test step.
The configured Spotify credentials and persisted OAuth token must already be
available before testing. Start the GUI and enable the Spotify live loopback;
the Spotify Queue should populate without `Spotify: no valid token found` or a
token-refresh error.

Do not enter a Spotify client ID or re-run the OAuth bootstrap flow as part of
this test guide. Never include access tokens, refresh tokens, client secrets, or
authorization headers in the test report.

### 2.2 Confirm FFmpeg sees VB-Cable

```powershell
ffmpeg -hide_banner -list_devices true -f dshow -i dummy 2>&1 |
  Select-String "CABLE Output"
```

Expected: a DirectShow capture device matching `CABLE Output` is listed.

### 2.3 Confirm Windows routing

1. Route Spotify playback to **CABLE Input**.
2. Keep CABLE Input and CABLE Output on the same Windows format. DreamSync opens
   the Reactive reader at the selected input device's native rate (commonly
   `48000` Hz for WASAPI); the FFmpeg learning capture may independently
   resample its output to `44100` Hz.
3. Enable Windows monitoring for **CABLE Output** to the chosen
   speakers/headphones, or use the already-established external monitoring
   route.
4. Play 15–30 seconds in Spotify before starting DreamSync.
5. Confirm audible playback is clean, single, and continuous.

Mode 4 treats Spotify as the audio authority. DreamSync must not replay the
captured MP3. If audio is doubled or echoed before DreamSync starts, fix the
Windows route first.

### 2.4 Confirm a clean cache-miss sample

For each planned track, inspect the default learned cache without deleting it:

```powershell
$trackId = "PASTE_SPOTIFY_TRACK_ID"
$learnedPath = Join-Path $env:USERPROFILE ".dreamsync\cache\spotify_$trackId"
Get-ChildItem -Force -LiteralPath $learnedPath -ErrorAction SilentlyContinue
```

Expected for the first pass: the directory does not exist, or it has no complete
`manifest.json` for the selected profile. If it is already learned, choose a
different track. Do not delete an existing learned entry merely to manufacture a
cache miss unless that deletion is separately approved and backed up.

## 3. Launch and configure the GUI

Start with simulation output:

```powershell
Set-Location C:\Users\brian\dreamsync
& .\.venv\Scripts\python.exe -m dreamsync gui --config dev/devices-dummy.yaml
```

In the GUI:

1. Set **Output Target** to **Simulation Only**.
2. Select **Spotify Live — Learning** from the Live tab's mode buttons, or select
   **Spotify Live — Learning (experimental)** in **Config** > **Runtime /
   Routing**. Both controls switch the active Live mode immediately and keep the
   startup selection in sync.
   The **Reactive Live** control surface must remain visible in this mode. Mode 4
   uses those same profile, palette, effect, beat, diagnostic, and runtime-
   override controls; Spotify coordination and background capture are layered
   around the Reactive session rather than replacing it.
3. Set **Reactive audio input** to the `CABLE Output` input device, rather than
   **System default**.
4. Confirm the capture directory has sufficient free space for at least three
   MP3s plus analysis and show artifacts. The default is `captured_songs`.
5. Set the capture device pattern to `CABLE Output`.
6. Set learned-capture output sample rate to `44100`. This does not force the
   Reactive WASAPI input stream to 44.1 kHz; DreamSync negotiates that stream at
   the selected device's native rate.
7. Check **Learn cache misses**.
8. Select **Keep recent MP3s** and set the limit to `10` for the initial rollout.
9. Keep the simulation preview visible.
10. Open the **Diagnostics** tab and note any pre-existing warnings.

Do not start **Compiled Capture Replay**. It is Mode 5 and intentionally replays
captured audio after analysis; it is not the live pipeline under test.

## 4. Three-track simulation baseline

Queue tracks A, B, and C in Spotify. Begin with Spotify stopped or paused at the
start of track A.

### 4.1 Start track A

1. Start track A from 0:00 in Spotify.
2. Within two seconds, click **Start Live — Learning** at the top of the Live
   tab, or press **Space**. Starting later than two seconds intentionally makes
   the capture ineligible. While Mode 4 is running, **Space** or **Esc** stops it.
3. Observe the active badge, simulation preview, Spotify Queue, DreamSync
   Background Learning list, and Diagnostics tab.

Expected within a few seconds:

- Badge: **REACTIVE · LEARNING**.
- Lighting simulation responds immediately to live audio; it does not wait for
  the track to finish.
- The preview footer advances beyond **Frame: waiting for output** and reports a
  monotonically increasing **Frame #N**. A manually selected palette is not required: the active
  Reactive profile supplies its initial palette.
- Capture state is running.
- Spotify remains the only audible playback source.
- No compiled show takes over during this first play.
- No second light-output owner appears.

Record time-to-first-light from clicking Start to visible Reactive activity.
Treat more than two seconds, a blank preview, a frozen preview, or an output
ownership warning as a failure.

After this observation, press **Space** or **Esc** once. The top-level mode must
release output ownership and change the Reactive status to **Input not
listening** on the next normal status refresh (normally under one second).
Background FFmpeg/finalizer cleanup may continue without blocking the GUI.
Treat multi-second input/beat ghosting or an unresponsive window as a failure.

### 4.2 Let tracks transition naturally

Do not pause, seek, skip, change the output target, or edit the queue during the
baseline. Let A transition naturally to B, then B to C.

At each transition, expected behavior is:

- The new uncached track becomes **REACTIVE · LEARNING** immediately.
- The completed previous track appears separately as `queued`, `analyzing`,
  `compiling`, then `learned`.
- Background compilation never replaces the active track's Reactive output.
- The GUI remains responsive while analysis and compilation run.
- Audio remains single and uninterrupted.
- Capture reports no process restart, gap, or device-busy error.

After track C ends naturally, allow background work to finish. Do not close the
GUI while a track is `queued`, `analyzing`, or `compiling` for this baseline.

### 4.3 Baseline pass criteria

All three tracks must reach `learned`. The baseline fails if any track is marked
`not learned` or `failed`, or if any of these occurs:

- Reactive output begins late or stops during compilation.
- FFmpeg or sounddevice cannot open `CABLE Output`.
- Audible echo, duplicate playback, new dropout, or glitching occurs.
- The capture process restarts or records a gap.
- The GUI freezes or controls become materially sluggish.
- A compiled show hot-swaps into the same first play.
- Lighting continues to be driven by two runtimes at once.

## 5. Verify durable learned artifacts

For each track ID:

```powershell
$trackId = "PASTE_SPOTIFY_TRACK_ID"
$learnedPath = Join-Path $env:USERPROFILE ".dreamsync\cache\spotify_$trackId"
Get-ChildItem -Force -LiteralPath $learnedPath
Get-Content -Raw -LiteralPath (Join-Path $learnedPath "manifest.json")
```

Expected files in each `spotify_{track_id}` directory:

```text
manifest.json
analysis.json
{profile_fingerprint}.show.json
```

Validate the manifest:

- `schemaVersion` is `1`.
- `provider` is `spotify`.
- `providerTrackId` exactly matches the Spotify track ID.
- `providerUri` matches the tested track.
- `captureComplete` is `true`.
- `durationMs` is plausible.
- `captureValidation.startOffsetSeconds` is at most `2.0`.
- `captureValidation.seekDetected` is `false`.
- `captureValidation.skipped` is `false`.
- `captureValidation.captureGaps` is `0`.
- `captureValidation.captureRestarts` is `0`.
- `profileFingerprints` contains the active profile's show fingerprint.

Open `analysis.json` and the `.show.json` as JSON to confirm they parse. Do not
edit the files during the test.

Verify MP3 retention in the configured capture directory. With **Keep recent =
10**, the three source MP3s and their sidecars should normally remain. Durable
artifacts must remain even if an older retained MP3 is later removed by policy.

## 6. Cache-hit replay test

Replay track A without stopping Mode 4. It may be selected manually, but start it
from 0:00 for the cleanest observation.

Expected immediately:

- Badge: **PRECOMPILED**.
- The compiled timeline drives the simulation.
- Reactive capture does not start for that track.
- No new analysis or compilation job is queued.
- The session cache-hit count increases.
- The learned-library count does not decrease.

Seek to a recognizable point after the initial start only after completing the
basic cache-hit observation. The compiled timeline should snap to Spotify's
position rather than restart from zero.

Replay A once more by starting it at a non-zero position. Expected: it is still
**PRECOMPILED** and begins at the current Spotify position.

## 7. Transport and eligibility tests

Use uncached test tracks for invalidation cases. Each case must preserve Reactive
output but must not publish a canonical complete manifest.

### 7.1 Pause and resume on an uncached track

1. Start an uncached track at 0:00 and confirm **REACTIVE · LEARNING**.
2. Pause after 15–30 seconds.
3. Wait five seconds, then resume.

Expected:

- Badge becomes **PAUSED** while paused.
- The candidate becomes `not learned` with reason `paused`.
- Reactive operation can continue after resume, but that play is not eligible
  for canonical publication.
- No complete manifest appears for that track.

### 7.2 Seek on an uncached track

1. Start another uncached track at 0:00.
2. Seek forward by at least 10 seconds.

Expected:

- Reactive lighting remains the active strategy.
- The capture is invalidated with `seek_detected`.
- No complete learned manifest is published.

### 7.3 Skip an uncached track

1. Start another uncached track at 0:00.
2. Skip after 15–30 seconds.

Expected:

- The old track is `not learned`, normally with `track_changed`/skip semantics.
- The next track selects cache hit or Reactive fallback immediately.
- The partial old capture never becomes a complete learned entry.

### 7.4 Pause, resume, seek, and skip on a cached track

Repeat controls using learned track A.

Expected:

- Pause freezes compiled lighting progression and displays **PAUSED**.
- Resume continues against Spotify's current clock.
- Seek snaps compiled output coherently to the new Spotify position.
- Skip immediately selects the next track's strategy.
- No learning capture is created for the cached track.

## 8. Retention transaction tests

Run these only after the baseline artifacts have been backed up or with new
uncached tracks.

### 8.1 Delete after verified compile

1. Select **Delete after verified compile**.
2. Learn one new track through a natural full play.
3. Wait until its state is `learned`.

Expected:

- `analysis.json`, the show, and `manifest.json` exist and reload.
- The source MP3 and temporary sidecar are deleted only after publication.
- Replaying the Spotify track still produces **PRECOMPILED**.

### 8.2 Compile failure safety

This case is best exercised through the automated worker test rather than by
damaging a live environment:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q `
  dev/tests/test_show_pipeline_worker_learned.py `
  -k "compile_failure_retains_recoverable_source_and_analysis"
```

Expected: pass. A compile failure retains the MP3 and analysis and does not
publish a complete manifest.

### 8.3 Keep-recent isolation

With **Keep recent**, confirm that learned-live cleanup does not delete unrelated
Compiled Capture Replay audio. The automated isolation check is:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q `
  dev/tests/test_show_pipeline_worker_learned.py `
  -k "keep_recent_never_deletes_unrelated_replay_audio"
```

## 9. Hardware validation

Proceed only after the entire simulation baseline and cache-hit replay pass.

1. Stop Mode 4.
2. Load the real device/room-layout configuration.
3. Set **Output Target** to **Hardware**.
4. Keep **Fallback to simulation if hardware is unavailable** enabled for the
   first hardware attempt, but record any fallback as a hardware-test failure.
5. Start Mode 4 with an uncached track from 0:00.
6. Repeat the three-track natural-transition baseline.
7. Replay a learned track and verify **PRECOMPILED** against Spotify time.
8. Repeat cached pause, resume, seek, and skip.

Hardware-specific pass criteria:

- Only the intended devices respond.
- No device is controlled by two output runtimes.
- Track changes do not flash, stall, or leave stale output.
- Compiled lighting is visually aligned with Spotify throughout the track.
- Reactive output remains stable while the previous song compiles.
- Simulation fallback never activates unexpectedly.
- Stopping Mode 4 releases output and capture within five seconds.

## 10. Performance and dual-reader decision gate

For each of the three baseline tracks, record:

| Metric | A | B | C |
|---|---:|---:|---:|
| Time to Reactive output (s) | | | |
| Analysis duration (s) | | | |
| Compile duration (s) | | | |
| Reactive dropped/input-overflow blocks | | | |
| Analysis dropped blocks | | | |
| Capture gaps | | | |
| Capture restarts | | | |
| Approx. clock drift/correction (ms) | | | |
| Peak CPU (%) | | | |
| Audible glitches | | | |

Use the GUI Diagnostics tab, capture logs under the configured capture
directory's `logs` folder, manifest validation, Windows Task Manager, and audible
observation. Preserve logs for any failure.

The independent-reader MVP passes only when:

- both readers open the loopback device for all three tracks;
- capture gaps and restarts are zero;
- Reactive dropped blocks do not materially increase during capture/compile;
- UI and lighting remain responsive;
- boundary error stays within the eligibility tolerance of
  `max(2 seconds, 1% of track duration)`; and
- monitoring introduces no echo or duplicated audio.

Escalate to the shared `LoopbackInputService` follow-up described in the plan if
any of these occurs:

- the second reader intermittently or consistently cannot open the device;
- capture gaps or restarts occur during Reactive operation;
- Reactive dropped-block rate increases materially;
- boundary or clock error is unacceptable; or
- capture/compilation makes live output visibly unstable.

## 11. Shutdown and recovery checks

### Normal shutdown

1. Click **Stop Live — Learning**.
2. Confirm the output stops and the capture process exits within five seconds.
3. Confirm no new learning state changes occur after shutdown.
4. Close the GUI.
5. Confirm Spotify audio continues according to the Windows monitoring route;
   DreamSync must not own Spotify playback.

### Interrupted-work evidence

After a deliberate GUI close during a separate disposable test track's analysis
or compile stage, restart the GUI and inspect learned storage/recovery status.

Expected:

- an interrupted transaction never appears as a complete learned cache hit;
- recoverable MP3 or analysis evidence is retained;
- a corrupt or incomplete manifest fails closed;
- previously complete learned tracks remain usable.

Do not use Task Manager to kill DreamSync during the first baseline. Reserve
forced termination for an isolated recovery test after normal behavior passes.

## 12. Final acceptance checklist

Mark Mode 4 validated only when every required item is checked.

- [ ] Focused automated gate passes.
- [ ] Full-suite result is recorded and any failures are triaged.
- [ ] Spotify authorization works without exposing credentials.
- [ ] FFmpeg finds `CABLE Output`.
- [ ] Spotify monitoring is audible once, with no echo.
- [ ] Three uncached simulation tracks become Reactive immediately.
- [ ] All three capture concurrently and transition naturally.
- [ ] All three publish valid analysis, show, and manifest artifacts.
- [ ] No capture gaps or restarts occur.
- [ ] Reactive drops do not materially increase during compilation.
- [ ] No same-play hot-swap occurs.
- [ ] Later playback of a learned track is **PRECOMPILED** immediately.
- [ ] Compiled playback follows Spotify position, pause, resume, and seek.
- [ ] Pause, seek, skip, and mid-track starts never publish canonical captures.
- [ ] Retention happens only after verified publication.
- [ ] GUI distinguishes active playback from background learning and replay.
- [ ] Hardware three-track run passes without simulation fallback.
- [ ] Stop/shutdown releases watcher, capture, worker, and output cleanly.
- [ ] Dual-reader decision gate passes, or measurements justify the shared-input
      follow-up.

## Failure report template

For every failure, capture:

```text
Test step:
Time observed:
Track ID and progress:
Active badge:
Background learning state/reason:
Expected:
Actual:
Reproducible (yes/no):
Output target (simulation/hardware):
Capture device pattern:
Relevant Diagnostics events/warnings:
Capture gaps/restarts:
Reactive dropped blocks:
Artifact directory listing:
Manifest contents with credentials removed:
Log file paths:
Screenshot/video path:
```

Stop the run immediately for device-busy loops, repeated capture restarts,
uncontrolled hardware output, sustained audible corruption, or overlapping
lighting authorities. Preserve the capture directory and learned-track directory
for diagnosis; do not clean them up before the failure is understood.

## Final decision labels

- **PASS — simulation:** automated gate and all simulation sections pass.
- **PASS — hardware MVP:** simulation and hardware sections pass, including the
  three-track dual-reader decision gate.
- **CONDITIONAL:** core lifecycle passes, but a non-safety metric needs an agreed
  threshold or longer soak test.
- **FAIL — shared input required:** dual-reader contention meets a fan-out trigger.
- **FAIL — implementation defect:** identity, eligibility, artifact transaction,
  output ownership, clock following, or cleanup violates the plan.

Only **PASS — hardware MVP** satisfies Phase 7 of the implementation plan.
