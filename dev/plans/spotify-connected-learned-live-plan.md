# Spotify-Connected Learned-Live Implementation Plan

## Status

Planned. This document defines the implementation for the fourth runtime mode:
Spotify-connected live playback that uses a saved compiled show when one exists,
falls back to Reactive Live immediately when one does not, and learns missing
tracks by capturing and compiling them in the background.

The one-time Spotify authorization and Windows/VB-Cable routing configuration
are treated as existing deployment prerequisites and are out of scope here.

## Goal

Add a GUI-driven **Spotify Live — Learning** mode with this behavior:

```text
Spotify starts a track
        |
        +-- saved compatible show exists
        |       |
        |       `-- run the saved ShowTimeline against Spotify's live clock
        |
        `-- no saved compatible show
                |
                +-- run Reactive Live immediately
                +-- capture the complete track in the background
                +-- analyze and compile after the track finishes
                +-- save the reusable analysis and compiled show
                `-- retire the temporary MP3 according to retention policy
```

The mode must never delay the audio the user is currently hearing. A cache miss
must produce Reactive lighting rather than silence, and background learning must
not interrupt the next song.

## Runtime Modes After This Work

The application should expose five distinct concepts:

1. **Precompiled Playback**
   - Plays a local audio file and its saved show together.
   - Existing implementation remains unchanged.
2. **Reactive Live**
   - Produces immediate interpreted lighting from an arbitrary live input.
   - Existing implementation remains unchanged.
3. **Visualizer**
   - Produces immediate low-interpretation visualization from live input.
   - Existing implementation remains unchanged.
4. **Spotify Live — Learning**
   - New mode described in this plan.
   - Uses a compiled show live when cached and Reactive Live while learning a
     cache miss.
5. **Compiled Capture Replay**
   - Existing capture -> analyze -> compile -> replay pipeline.
   - Retains the full-song delay and captured MP3 playback deliberately.
   - This should be labeled as delayed replay rather than live Spotify output.

Mode 4 is a coordinator over existing engines, not a new renderer.

## Product Decisions

### Spotify is the audio authority

- Spotify continues playing the audible track.
- DreamSync does not replay captured audio in Spotify Live — Learning mode.
- The saved show is ticked using Spotify playback position.
- Pause, resume, seek, and track changes update the show clock.
- Windows/VB-Cable monitoring remains responsible for getting Spotify audio to
  the speakers during the first implementation.

### Cache misses are immediately Reactive

- A missing show must not leave the lights idle.
- Reactive Live starts at the current track position and remains active for the
  rest of that play.
- A show compiled during the current play is not hot-swapped in near the end of
  the song. It becomes eligible on the next play, avoiding a visually abrupt
  mid-track change of lighting authority.

### The full-song recording is a temporary build input

- Capture streams to an encoded temporary MP3; it is not an in-memory five-minute
  PCM buffer.
- The durable products are a track manifest, a reusable analysis artifact, and
  one or more compiled show files.
- MP3 deletion happens only after analysis and show persistence are verified.

### Spotify playback control is independent

- Search, playlist editing, Add, Skip, Shuffle, and other Spotify mutation APIs
  are not prerequisites for this mode.
- The existing read-only watcher supplies identity, position, duration, state,
  and boundaries.
- Control actions can be repaired or expanded separately without blocking this
  implementation.

## Current Reusable Foundations

### Spotify clock and cached-show playback

`src/dreamsync/v3_session.py` already provides:

- `PositionInterpolator`, which turns periodic Spotify progress polls into a
  smooth monotonic playback position;
- pause handling;
- seek detection and snapping;
- `SpotifyShowSession`, which ticks `ShowPlaybackRuntime` without an `AudioPlayer`;
- cache lookup by Spotify `track_id`; and
- background inspection of upcoming tracks.

Mode 4 should evolve this path rather than introducing another Spotify clock.

### Reactive runtime

`RuntimeSupervisor.start_reactive_output()` and the live runtime already support
the feature analysis and lighting needed for cache-miss fallback. The new
coordinator must reuse the same settings and output semantics.

### Capture and compile pipeline

The following pieces already exist:

- `CaptureOrchestrator` and Spotify boundary integration;
- `on_segment_saved(mp3_path, metadata)`;
- background analysis/compilation through `ShowPipelineWorker`;
- `SongStructure.to_dict()` / `SongStructure.from_dict()`;
- `ShowCache` with profile-fingerprinted `.show.json` entries; and
- capture gap/restart telemetry.

### GUI runtime ownership

`RuntimeSupervisor` already keeps capture preparation separate from the active
output lease. The new mode must continue to use it as the single authority for
light output so Reactive and a compiled show never drive devices concurrently.

## Gaps To Close

1. Spotify `track_id` and `uri` are dropped from capture timing and segment
   sidecars.
2. `ShowPipelineWorker` uses `path_based_track_id(mp3_path)`, so repeated captures
   of the same Spotify track do not share a stable cache entry.
3. `SpotifyShowSession` leaves the lights idle on a cache miss instead of starting
   Reactive Live.
4. The GUI capture worker does not persist the neutral `.analysis.json` produced
   during background compilation.
5. There is no capture-completeness decision preventing skipped, sought, partial,
   or gap-damaged tracks from becoming canonical cache entries.
6. MP3 retention is based on a directory file count, not verified build-artifact
   completion.
7. The GUI does not expose a distinct Spotify live state such as cache hit,
   Reactive fallback, learning, compiling, or learned.
8. The current Reactive and FFmpeg capture paths open the loopback source
   independently. This is acceptable for an MVP only after a real-device
   contention test.

## Identity And Cache Design

### Primary identity

Use the Spotify track ID as the canonical provider identity:

```text
provider = spotify
provider_track_id = SpotifyTrack.track_id
provider_uri = SpotifyTrack.uri
canonical_key = spotify_{track_id}
```

Track title must not be the primary key. It cannot distinguish remasters, edits,
live recordings, clean/explicit editions, or unrelated tracks with the same
title.

### Fallback identity

For legacy captures or non-Spotify sources, retain the existing metadata fallback:

```text
normalized artist + normalized title + duration bucket
```

The current `sidecar_track_id(title, artist)` remains supported for legacy data.
Duration should be included in a new fallback version rather than silently
changing the existing function and invalidating old cache paths.

### Cache compatibility dimensions

A show lookup is compatible only when all relevant dimensions match:

- provider and provider track ID;
- active profile fingerprint;
- show schema/compiler compatibility version; and
- any future analysis version that materially changes cue timing.

`ShowCache` already partitions by track ID and profile fingerprint. Add explicit
schema/compiler metadata to the manifest and timeline metadata. Do not require a
directory migration for existing entries; legacy entries remain readable but are
not assumed to match a Spotify ID unless indexed explicitly.

### Proposed durable layout

Continue using the configured cache root, with a learned-track library below it:

```text
~/.dreamsync/cache/
  spotify_{track_id}/
    manifest.json
    analysis.json
    {profile_fingerprint}.show.json
```

If preserving the exact existing `ShowCache` path is preferable, `manifest.json`
and `analysis.json` can be added beside the current show files without changing
`ShowCache._entry_path()`.

### Track manifest

Add a versioned manifest model rather than writing ad hoc dictionaries in the
GUI layer.

```json
{
  "schemaVersion": 1,
  "provider": "spotify",
  "providerTrackId": "...",
  "providerUri": "spotify:track:...",
  "title": "...",
  "artist": "...",
  "album": "...",
  "durationMs": 243120,
  "capturedAt": "...",
  "captureComplete": true,
  "captureValidation": {
    "startOffsetSeconds": 0.35,
    "durationDeltaSeconds": -0.42,
    "seekDetected": false,
    "captureGaps": 0,
    "captureRestarts": 0
  },
  "analysisVersion": 1,
  "compilerVersion": 1
}
```

Writes must be atomic. A manifest is published as complete only after the
analysis and at least one show have been written and reloaded successfully.

## Capture Eligibility

Not every observed Spotify segment is safe to learn. Add an explicit immutable
capture candidate record and validator.

### Proposed model

```python
@dataclass(frozen=True)
class SpotifyCaptureCandidate:
    track_id: str
    uri: str
    title: str
    artist: str
    album: str
    expected_duration_seconds: float
    observed_start_progress_seconds: float
    observed_end_progress_seconds: float
    captured_duration_seconds: float
    seek_detected: bool
    skipped: bool
    capture_gaps: int
    capture_restarts: int


@dataclass(frozen=True)
class CaptureEligibility:
    eligible: bool
    reasons: tuple[str, ...]
```

### Initial acceptance rules

A segment is eligible only when:

- it has a non-empty Spotify track ID;
- observation/capture began within 2.0 seconds of the track start;
- no seek larger than the existing interpolator seek threshold occurred;
- the track was not replaced substantially before its expected end;
- there are no capture gaps or capture-process restarts;
- captured duration differs from Spotify duration by no more than
  `max(2.0 seconds, 1% of track duration)`; and
- the finalized MP3 is readable and has nontrivial audio content.

Keep thresholds centralized in a configuration dataclass. Do not scatter magic
numbers through watcher callbacks.

### Ineligible outcomes

- Do not publish an analysis or show under the canonical Spotify track ID.
- Delete the partial temporary MP3 unless diagnostic retention is enabled.
- Record a concise reason such as `started_mid_track`, `seek_detected`,
  `duration_mismatch`, `capture_gap`, or `skipped`.
- Continue Reactive Live without surfacing the condition as a fatal runtime
  error.

## MP3 And Artifact Retention

Add a retention enum to capture/learned-live settings:

```text
keep_all
keep_recent
delete_after_verified_compile
```

Add `retained_mp3_limit`, defaulting to 10 when `keep_recent` is selected.

### Default during initial rollout

Use `keep_recent` with a limit of 10. This preserves enough evidence to debug
boundary or analysis failures while preventing unbounded storage growth.

### Safe deletion transaction

Delete a source MP3 only after all of these succeed:

1. MP3 finalization completes.
2. Capture eligibility passes.
3. Analysis completes.
4. `analysis.json` is written atomically.
5. A compiled show is written through `ShowCache`.
6. The show can be reloaded and validated.
7. The manifest is written with `captureComplete=true`.

If any step fails, retain the MP3 and sidecar for recovery. Deletion includes the
temporary capture metadata sidecar only when its useful fields have been copied
into the durable manifest. Never delete an existing durable analysis or show as
part of MP3 cleanup.

## Runtime State Machine

Add a dedicated coordinator, tentatively `SpotifyLearnedLiveSession`, rather than
continuing to enlarge GUI callbacks.

### Track states

```text
waiting
  |
  +-- track begins and cache hit ------> compiled_live
  |
  `-- track begins and cache miss -----> reactive_learning
                                           |
                                           +-- track ends eligible --> queued_for_compile
                                           |                              |
                                           |                              +--> analyzing
                                           |                              +--> compiling
                                           |                              `--> learned
                                           |
                                           `-- track ends ineligible --> not_learned
```

Background compilation state belongs to the completed track and must not replace
the active state of the newly playing track.

### Output ownership

At most one of these owns lighting output:

- cached `ShowPlaybackRuntime`;
- Reactive Live runtime; or
- Visualizer/another explicitly selected mode.

Capture and compilation do not own output. Track transitions perform an atomic
output-engine swap through `RuntimeSupervisor`.

### Cache-hit behavior

On a cache hit:

1. Load the timeline before replacing the active runtime.
2. Create a new `ShowPlaybackRuntime`.
3. Tick immediately at interpolated Spotify progress, not at zero.
4. Do not capture the track by default.
5. Reset on track change.
6. Freeze while Spotify is paused.
7. Snap or rebuild runtime state after a seek so cue transitions are coherent.

### Cache-miss behavior

On a cache miss:

1. Start Reactive Live immediately using the configured live input and settings.
2. Start a capture candidate only when playback begins near zero.
3. Stream capture concurrently for the duration of the track.
4. Finalize at the immediate Spotify boundary.
5. Submit eligible results to one background learning worker.
6. Keep Reactive as lighting authority for the entire current play.

### Pause, resume, seek, and skip

- Pause freezes both compiled timeline advancement and learning eligibility
  timing. The first implementation may keep recording silence while paused only
  if duration validation removes it correctly; preferably pause candidate capture
  or mark the candidate ineligible.
- Resume restarts interpolation from the latest Spotify progress.
- Seek marks an active learning candidate ineligible. A cached timeline may snap
  to the new time and continue.
- Skip finalizes the old temporary segment as ineligible and selects the next
  track's cache-hit/fallback path immediately.

## Concurrency And Audio Topology

### MVP topology

Use the existing independent readers:

```text
CABLE Output
  +-- sounddevice/live input -> Reactive runtime
  `-- FFmpeg DirectShow input -> CaptureOrchestrator -> temporary MP3
```

This is the smallest implementation and should be tested on the target Windows
machine before introducing a shared audio service.

### MVP exit criteria

During a three-song cache-miss session:

- both readers can open the loopback device;
- no exclusive-mode/device-busy failure occurs;
- Reactive audio dropped-block count remains within the existing acceptable
  threshold;
- capture reports no restarts or gaps;
- GUI and light output remain responsive during background compilation; and
- audible monitoring has no new echo or duplicated output.

### Follow-up topology if MVP contention is observed

Create one shared loopback capture authority:

```text
LoopbackInputService
  +-- AudioBlockRing for Reactive analysis
  +-- streaming encoder sink for temporary capture
  `-- telemetry sink
```

This follow-up is not part of the first implementation unless the MVP validation
fails. Do not add application-level speaker monitoring as part of the refactor;
Windows routing remains the audio-output authority for this mode.

### Compilation resource budget

- Use one learning worker initially.
- Bound the pending compile queue.
- Never compile a cache hit.
- Expose pending/analyzing/compiling counters.
- Monitor Reactive dropped blocks and tick latency while compiling.
- If contention is measurable, add cooperative throttling or defer compilation
  until CPU load/live telemetry recovers.

## Implementation Phases

### Phase 1: Propagate stable Spotify identity

#### Files to modify

- `src/dreamsync/gui/services/queue_service.py`
- `src/dreamsync/session.py`
- `src/dreamsync/cli.py` where equivalent Spotify timing dictionaries are built
- `src/dreamsync/capture/metadata_writer.py`
- `src/dreamsync/capture/orchestrator.py`
- `src/dreamsync/capture/scanner.py`
- `src/dreamsync/cache.py`

#### Work

- Add `spotify_track_id`, `spotify_uri`, and expected duration to every current,
  previous, and queued song timing dictionary.
- Add corresponding optional fields to `SegmentMetadata` and JSON sidecars.
- Copy those fields through `_build_segment_metadata()`.
- Extend `CaptureTrack` scanning without breaking legacy sidecars.
- Add `spotify_track_cache_id(track_id)` and update `track_id_for_capture()` to
  prefer Spotify identity, then legacy sidecar identity, then path identity.
- Keep JSON loading tolerant of unknown and missing fields.

#### Tests

- Track-change timing includes current and previous Spotify IDs/URIs.
- Periodic timing snapshots retain IDs for current and queued tracks.
- Segment sidecar round-trips Spotify identity.
- Legacy sidecars still scan successfully.
- Spotify ID takes precedence over identical title/artist fallback data.
- Two editions with identical title/artist but different Spotify IDs get distinct
  cache keys.

#### Completion criteria

- A finalized GUI Spotify capture can be traced unambiguously from sidecar to
  `SpotifyTrack.track_id`.

### Phase 2: Add learned-track artifacts and eligibility validation

#### Files to create

- `src/dreamsync/spotify/learned_track.py`
- `src/dreamsync/capture/eligibility.py`
- `dev/tests/test_learned_track.py`
- `dev/tests/test_capture_eligibility.py`

#### Files to modify

- `src/dreamsync/cache.py`
- `src/dreamsync/analyzer/models.py` only if version metadata cannot be added
  externally

#### Work

- Implement the candidate and eligibility models.
- Track starting progress, seeks, pauses/skips, gaps, restarts, expected duration,
  and captured duration.
- Add versioned, atomic manifest read/write helpers.
- Add atomic analysis persistence using `SongStructure.to_dict()`.
- Add helpers for listing learned tracks and checking artifact completeness.
- Ensure corrupt manifests/analysis files fail closed and retain the source MP3.

#### Tests

- Complete uninterrupted tracks pass.
- Mid-track starts fail.
- Skips and seeks fail.
- Duration mismatch fails at both sides of the tolerance.
- Capture gaps/restarts fail.
- Atomic writes leave no published partial manifest.
- Corrupt analysis/show artifacts do not mark a track learned.

#### Completion criteria

- Eligibility produces deterministic reasons and cannot publish an incomplete
  learned entry.

### Phase 3: Make the pipeline compile by Spotify ID and persist analysis

#### Files to modify

- `src/dreamsync/show_pipeline_worker.py`
- `src/dreamsync/gui/services/runtime_supervisor.py`
- `src/dreamsync/cache.py`

#### Work

- Replace unconditional `path_based_track_id(mp3_path)` with metadata-aware
  identity resolution.
- Write the neutral analysis artifact before compiling.
- Pass capture validation/manifest context into the worker.
- Publish the manifest only after the show cache write has been verified.
- Add retention-policy handling after successful publication.
- Keep the existing delayed replay worker behavior compatible; learned-live
  publication should be enabled through explicit configuration rather than
  silently changing every capture workflow.
- Reduce the learned-live worker pool to one worker initially.

#### Tests

- Repeated captures with one Spotify ID target the same cache entry.
- A cache hit skips analysis and compilation.
- Analysis is written before the source MP3 is deleted.
- Compile failure retains MP3 and sidecar.
- Reload-validation failure retains MP3.
- Successful verified compile applies each retention policy correctly.
- Existing streaming/delayed pipeline tests continue passing.

#### Completion criteria

- A valid cache-miss capture produces a reloadable analysis, show, and manifest
  under the Spotify ID.

### Phase 4: Implement the learned-live coordinator

#### Files to create

- `src/dreamsync/spotify/learned_live_session.py`
- `dev/tests/test_spotify_learned_live_session.py`

#### Files to modify

- `src/dreamsync/v3_session.py`
- `src/dreamsync/gui/services/session_service.py`
- `src/dreamsync/gui/services/runtime_supervisor.py`

#### Work

- Extract/reuse `PositionInterpolator`; do not duplicate its logic.
- Prefer public watcher callback registration or an event subscription abstraction
  over mutating private `_on_*` callback attributes as `SpotifyShowSession`
  currently does.
- Implement track-level cache-hit selection.
- Add a Reactive fallback factory controlled through `RuntimeSupervisor`.
- Start capture only for eligible cache misses.
- Forward completed candidates to the learned-track worker.
- Prevent a newly compiled show from replacing Reactive mid-track.
- Atomically switch output authority on every track change.
- Preserve simulation and hardware adapter selection rules.
- Record session events and statistics without exposing Spotify credentials.

#### Tests

- Cache hit creates a show runtime and never starts Reactive/capture.
- Cache miss starts Reactive and capture immediately.
- Cache miss completion queues background learning.
- A show learned during a play is used only on a later play.
- Next play of the same Spotify ID is a cache hit.
- Pause freezes compiled playback time.
- Seek snaps cached playback and invalidates a learning capture.
- Skip invalidates capture and selects the next track immediately.
- Output ownership never overlaps between Reactive and compiled runtime.
- Session shutdown stops watcher subscriptions, capture, worker, and output.

#### Completion criteria

- With mocked Spotify/audio dependencies, one cache-miss play is Reactive and the
  next play of the same ID uses the saved show against Spotify time.

### Phase 5: Add GUI mode and telemetry

#### Files to modify

- `src/dreamsync/gui/models/runtime_mode_state.py`
- `src/dreamsync/gui/models/runtime_telemetry_state.py`
- `src/dreamsync/gui/models/capture_settings.py`
- `src/dreamsync/gui/settings.py`
- `src/dreamsync/gui/services/runtime_supervisor.py`
- `src/dreamsync/gui/widgets/queue_panel.py`
- `src/dreamsync/gui/main_window.py`

#### Work

- Add **Spotify Live — Learning** as a distinct selectable mode.
- Keep **Compiled Capture Replay** as a separately labeled action.
- Add persisted learned-live settings:
  - enabled/profile selection;
  - capture device pattern;
  - learning enabled;
  - MP3 retention policy;
  - recent MP3 limit;
  - optional diagnostic logging.
- Reuse Reactive settings rather than duplicating them.
- Show active-track badges:
  - `PRECOMPILED`;
  - `REACTIVE · LEARNING`;
  - `REACTIVE · NOT LEARNING`;
  - `PAUSED`.
- Show completed-track background states separately:
  - queued;
  - analyzing;
  - compiling;
  - learned;
  - not learned with reason;
  - failed.
- Separate the **Spotify Playback Queue** from **DreamSync Background Learning**
  and **Captured Replay Queue**. Do not present them as one queue.
- Add learned-library count and cache-hit/miss session statistics.
- Keep Spotify mutation controls outside this mode's acceptance criteria.

#### Tests

- Settings round-trip with backward-compatible defaults.
- Selecting learned-live does not select delayed replay.
- GUI state distinguishes Spotify current/upcoming from background learning.
- Badge changes correctly for cache hit, miss, pause, and ineligible capture.
- Stopping learned-live leaves no active output or capture process.
- Simulation mode exercises the same coordinator without hardware.

#### Completion criteria

- A user can start and understand the complete learned-live lifecycle from the
  GUI without consulting logs.

### Phase 6: Retention, recovery, and storage management

#### Files to modify

- `src/dreamsync/gui/services/storage_service.py`
- `src/dreamsync/gui/widgets/queue_panel.py`
- `src/dreamsync/gui/main_window.py`
- capture cleanup helpers as appropriate

#### Work

- Include learned manifests, analyses, shows, and retained temporary MP3s in
  storage statistics.
- Add a recovery scan for:
  - finalized MP3 awaiting analysis;
  - analysis awaiting compilation;
  - show awaiting manifest publication;
  - stale partial/temp files.
- Recovery must resume safe work or retain evidence; it must not guess a Spotify
  identity from a filename when a manifest/sidecar lacks it.
- Add explicit actions to retry a failed learned track and delete retained source
  audio without deleting its durable show.
- Keep archive behavior for Compiled Capture Replay separate from the learned
  library.

#### Tests

- Restart recovers each interrupted transaction boundary.
- Storage counts separate durable learned artifacts from temporary audio.
- Deleting retained MP3 leaves analysis/show usable.
- Deleting a learned entry removes only the selected track ID/profile artifacts.
- Cleanup cannot escape configured cache/capture roots.

#### Completion criteria

- A crash during learning cannot produce a false cache hit or silently discard
  the only recoverable source audio.

### Phase 7: Real-device MVP validation and fan-out decision

No shared-input refactor occurs before this phase.

#### Manual validation

Use at least three full Spotify tracks with no cached shows:

1. Start Spotify Live — Learning in simulation.
2. Confirm current lighting is Reactive immediately.
3. Confirm capture runs concurrently without audible duplication.
4. Let each song transition naturally.
5. Confirm the completed prior track enters analyzing/compiling while the new
   track remains Reactive.
6. Confirm durable show/analysis/manifest files appear.
7. Replay a learned Spotify track and confirm `PRECOMPILED` activates immediately
   at the current Spotify position.
8. Repeat with hardware output.
9. Exercise pause, resume, seek, and skip.
10. Record CPU, Reactive dropped blocks, capture gaps/restarts, compile latency,
    cache hits, and visual drift.

#### Fan-out decision gate

Implement `LoopbackInputService` only if one or more occur:

- the second reader cannot open the device reliably;
- capture gaps/restarts occur during Reactive operation;
- Reactive dropped-block rate increases materially;
- audio clocks produce unacceptable capture-boundary error; or
- compile/capture operation makes live output visibly unstable.

#### Completion criteria

- The target Windows machine completes the three-song session with stable live
  output and produces reusable shows, or the measurements provide a concrete
  justification and requirements for the shared-input follow-up.

## Suggested Public Interfaces

Names are illustrative; implementation may adapt them to existing conventions.

```python
class SpotifyLearnedLiveSession:
    def run(self, stop_event: threading.Event) -> dict[str, Any]: ...
    def snapshot(self) -> LearnedLiveSnapshot: ...


@dataclass(frozen=True)
class LearnedLiveSnapshot:
    active_track_id: str = ""
    active_track_title: str = ""
    active_strategy: str = "waiting"  # waiting|compiled|reactive
    learning_state: str = "idle"
    learning_track_id: str = ""
    learning_reason: str = ""
    cache_hits: int = 0
    cache_misses: int = 0
    tracks_learned: int = 0
    compile_failures: int = 0


class LearnedTrackStore:
    def lookup(self, track_id: str, profile) -> ShowTimeline | None: ...
    def publish(self, candidate, structure, timeline, profile) -> Path: ...
    def recover(self) -> tuple[RecoveryItem, ...]: ...


class CaptureEligibilityValidator:
    def validate(self, candidate: SpotifyCaptureCandidate) -> CaptureEligibility: ...
```

Keep Qt objects out of these interfaces so lifecycle, cache, and state-machine
tests can run headlessly.

## Automated Test Plan

### Focused suites

Add or extend:

- `dev/tests/test_spotify_models.py`
- `dev/tests/test_gui_services.py`
- `dev/tests/test_fetch_timing.py`
- `dev/tests/test_metadata_writer.py`
- `dev/tests/test_capture_scanner.py`
- `dev/tests/test_cache.py` or the existing cache suites
- `dev/tests/test_show_pipeline_worker.py`
- `dev/tests/test_capture_eligibility.py`
- `dev/tests/test_learned_track.py`
- `dev/tests/test_spotify_learned_live_session.py`
- `dev/tests/test_gui_runtime_supervisor.py`
- `dev/tests/test_gui_settings_recovery.py`
- `dev/tests/test_v3_position.py`
- `dev/tests/test_v3_session.py`

### Required regression coverage

- Precompiled local playback remains unchanged.
- Basic Reactive Live remains unchanged.
- Visualizer remains unchanged.
- Compiled Capture Replay still retains/replays MP3s according to its own
  settings.
- Legacy capture sidecars and title/artist cache entries remain readable.
- Spotify watcher polling and capture timing remain operational when learning is
  disabled.
- GUI shutdown cleans up all session/capture/worker resources.

### Verification commands

Run focused tests after each phase, then the broader development suite:

```powershell
python -m pytest -q dev/tests/test_v3_position.py dev/tests/test_v3_session.py
python -m pytest -q dev/tests/test_queue_watcher.py dev/tests/test_timing_integrator.py
python -m pytest -q dev/tests/test_orchestrator.py dev/tests/test_show_pipeline_worker.py
python -m pytest -q dev/tests/test_gui_runtime_supervisor.py dev/tests/test_gui_services.py
python -m pytest -q dev/tests
```

## Observability

At minimum, record these structured events:

- `spotify_track_started`
- `learned_live_cache_hit`
- `learned_live_cache_miss`
- `learned_live_reactive_started`
- `learning_capture_started`
- `learning_capture_ineligible`
- `learning_analysis_started`
- `learning_compile_started`
- `learning_publish_completed`
- `learning_publish_failed`
- `learning_source_deleted`
- `learned_live_runtime_switched`
- `spotify_seek_detected`

Useful metrics:

- cache-hit ratio;
- tracks learned;
- ineligible captures by reason;
- analysis and compile duration;
- pending learning jobs;
- Reactive dropped audio blocks;
- capture gaps and restarts;
- Spotify/show clock drift and correction magnitude;
- retained MP3 bytes;
- durable learned-library bytes.

Never log access/refresh tokens or authorization headers.

## Rollout

### Stage 1: Simulation-only opt-in

- Feature remains off by default.
- Learned artifacts are retained alongside the most recent 10 MP3s.
- GUI marks the mode experimental.

### Stage 2: Hardware opt-in

- Enable after the real-device MVP passes.
- Keep Reactive/compiled strategy badges and diagnostics visible.

### Stage 3: Default Spotify live behavior

- Consider making it the default Spotify mode only after cache identity,
  retention, recovery, and live CPU behavior have been validated over extended
  sessions.
- Compiled Capture Replay remains available as a deliberate quality/replay mode.

## Final Acceptance Criteria

The implementation is complete when all of the following are true:

1. Starting an uncached Spotify track produces Reactive lighting without waiting
   for the song to end.
2. The same play is captured without interrupting Reactive output.
3. A complete eligible track produces a durable analysis, compiled show, and
   versioned manifest keyed by Spotify track ID.
4. The next play of that Spotify track uses the saved show immediately and does
   not require the MP3.
5. Compiled lighting follows Spotify pause, resume, seek, and current progress.
6. Skipped, sought, mid-track, or damaged captures never become canonical shows.
7. Background compilation does not materially degrade live Reactive output.
8. MP3 deletion occurs only after verified durable artifact publication.
9. The GUI clearly distinguishes Spotify playback, active lighting strategy,
   background learning, and delayed captured replay.
10. Existing Precompiled, Reactive, Visualizer, and Compiled Capture Replay modes
    pass their regression suites.

## Explicit Non-Goals

- Spotify OAuth/configuration UI.
- Windows/VB-Cable installation or routing management.
- Spotify search, playlist management, or full queue control.
- Mid-song switching from Reactive to a show compiled during that same play.
- Predicting/compiling an uncaptured upcoming Spotify track.
- Replacing both input readers before the MVP produces evidence that it is
  necessary.
- Removing the existing delayed replay pipeline.

