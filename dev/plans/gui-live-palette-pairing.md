# GUI Live Palette Loading and Palette-to-Song Pairing

## Context

DreamSync's GUI shell now exists, but the queue and palette surfaces are still
mostly independent:

- `src/dreamsync/gui/widgets/queue_panel.py` renders two simple list widgets
  with no selection workflow beyond display.
- `src/dreamsync/gui/controllers/queue_controller.py` and
  `src/dreamsync/gui/models/queue_state.py` only expose queue row names, not
  stable track identifiers, absolute paths, or any assignment metadata.
- `src/dreamsync/gui/controllers/palette_controller.py` and
  `src/dreamsync/gui/services/profile_service.py` support loading and editing
  one profile at a time, but there is no concept of a palette being attached to
  a specific queued song.
- `src/dreamsync/local_session.py` compiles local playlist tracks with a single
  session-wide `ProfileConfig`, which means there is currently no clean runtime
  path for song-specific palette choices.

The desired operator flow is:

1. select a song from the local queue
2. load an existing palette or generate a new one
3. assign that palette to the selected song
4. let the upcoming playback for that song use the assigned palette without
   leaving the GUI

## Decision

Implement this as a **local-queue-first GUI workflow** backed by
**song-scoped palette assignments** that resolve into **derived per-track
profiles** at compile time.

### Why this approach

- The compiler and show cache already key behavior off `ProfileConfig`, not raw
  palettes. Extending playback through derived profiles reuses the existing
  compiler and cache paths instead of introducing a second rendering path.
- A queue assignment should be keyed to a stable song identity, not to queue
  index or filename-only display text.
- The GUI needs persistence separate from hand-authored profile YAML so users
  can experiment with pairings without mutating a shared base profile each time.

## Scope

### MVP

- local queue song selection
- load palette from the active profile or another profile file
- generate a new palette/profile candidate from GUI controls
- assign or clear a palette pairing for a local queued song
- persist pairings across GUI restarts
- use the pairing automatically when an assigned song is compiled for playback
- show the current assignment in the queue UI

### Explicitly out of scope for the first pass

- hot-swapping the currently playing track mid-song after it has already been
  compiled
- full Spotify pairing support that affects playback
- a new standalone profile editor redesign
- cross-device or multi-user assignment sync

### Important guardrail

For the first implementation, a pairing change made to the **currently playing**
track should be treated as **applies on next compile / replay**, not as an
instant live lighting mutation. DreamSync's current runtime plays a compiled
timeline, so true mid-song palette replacement would require a more invasive
runtime change than this feature needs.

## Product Behavior

### Queue workflow

The queue tab becomes the primary workspace for this feature:

- left side: local queue list with assignment badges or inline summary
- right side: selected-song detail pane
- detail pane actions:
  - `Load From Active Profile`
  - `Load From Profile File`
  - `Generate Palette`
  - `Assign To Song`
  - `Clear Assignment`
  - optional `Open In Palette Editor`

### Assignment semantics

- A song pairing should attach to the song identity, not the current queue slot.
- Reordering the queue must not lose the pairing.
- Re-adding the same file later should recover the pairing when possible.
- Clearing a pairing returns that song to normal session-wide profile behavior.

### First-pass playback semantics

- Upcoming local tracks use their assigned palette automatically when they are
  compiled.
- Already compiled cache entries remain valid for their prior profile
  fingerprint; a newly assigned palette produces a different derived profile and
  therefore a different cache entry.
- If the selected track is currently playing, the UI should communicate that the
  change will apply on replay or next start.

## Architecture

### 1. Introduce explicit song-pairing models

Add GUI-facing data models that separate queue rows from palette assignments.

Recommended additions:

- `QueueTrackState`
  - `track_key`
  - `display_name`
  - `path`
  - `is_current`
  - `assignment_label`
  - `assignment_colors`
- `SongPaletteAssignment`
  - `track_key`
  - `mode` (`existing_palette`, `generated_profile`, `custom_palette`)
  - `source_profile_path`
  - `palette_name`
  - `colors`
  - generation metadata when applicable

This should replace the current queue model shape that only stores tuples of
track names.

### 2. Persist assignments outside profile YAML

Store queue pairings in a GUI-owned JSON file next to existing GUI settings,
instead of editing profile YAML for every assignment.

Recommended storage location:

- `%APPDATA%/DreamSync/song-palette-assignments.json`
- fallback: `~/.dreamsync/song-palette-assignments.json`

Reasons:

- keeps experimental pairings out of reusable profile source files
- matches the existing `GuiSettingsStore` persistence pattern
- supports restoring assignments across GUI sessions

### 3. Use stable local track keys

Do not key pairings by queue index or basename.

Preferred key strategy:

- local files: `content_hash_track_id(path)` when the file exists
- fallback: normalized absolute path key for incomplete or transient states

This is stronger than the current GUI queue snapshot behavior and better aligned
with the user expectation that a song keeps its pairing after reorder or rename.

### 4. Resolve pairings into derived profiles

Add a runtime helper that takes:

- the session base `ProfileConfig`
- a `SongPaletteAssignment`
- optional generated profile metadata

and returns a deterministic derived `ProfileConfig` for that song.

Recommended behavior for the MVP:

- `existing_palette` or `custom_palette`
  - inject a synthetic palette name such as `song_assigned`
  - rewrite each mood's palette list to use that synthetic palette
  - preserve effects, params, transitions, and other non-palette behavior from
    the base profile
- `generated_profile`
  - use the generated profile directly, or merge its palette set into the base
    profile if we want to preserve the base profile's effect pools

This keeps palette pairing focused on color identity while preserving the
project's existing compile-time architecture.

### 5. Make local playlist compilation profile-aware per track

`LocalPlaylistSession` currently compiles every track with the same `_profile`.
That needs to become "resolve effective profile for the current track, then
compile."

Recommended change:

- keep a session-wide base profile
- add an optional assignment resolver/store on the session
- before `load_track()` or `_compile_for_file()`, resolve the track's effective
  profile from its assignment
- continue using the existing show cache keyed by track id + profile
  fingerprint

This is the core runtime seam that turns a GUI assignment into actual playback
behavior.

## File Plan

### Files to modify

| File | Planned change |
|------|----------------|
| `src/dreamsync/gui/widgets/queue_panel.py` | replace simple list-only UI with a queue + detail workspace for pairing |
| `src/dreamsync/gui/controllers/queue_controller.py` | track selection, assignment actions, and richer row state |
| `src/dreamsync/gui/models/queue_state.py` | introduce structured queue row state and selected-song metadata |
| `src/dreamsync/gui/main_window.py` | wire queue selection, palette actions, and assignment persistence |
| `src/dreamsync/gui/services/queue_service.py` | return stable track metadata, not only display names |
| `src/dreamsync/gui/services/profile_service.py` | expose palette-loading and generation helpers needed by the queue pane |
| `src/dreamsync/gui/services/session_service.py` | pass assignment-aware dependencies into local preview sessions |
| `src/dreamsync/local_session.py` | resolve per-track effective profiles before compile/play |
| `src/dreamsync/gui/settings.py` | optionally share path helpers with a new assignment store |

### Files to add

| File | Purpose |
|------|---------|
| `src/dreamsync/gui/services/song_palette_store.py` | persisted load/save API for song-palette assignments |
| `src/dreamsync/gui/models/song_palette_state.py` | reusable assignment and selected-song detail models |
| `src/dreamsync/gui/controllers/song_palette_controller.py` or fold into `queue_controller.py` | orchestrate select/load/generate/assign/clear actions |
| `src/dreamsync/profile_overrides.py` | derive deterministic per-song profiles from base profile + assignment |

The controller split is optional. If the team wants fewer GUI controllers, the
pairing logic can live in `queue_controller.py`, but the persistence and profile
derivation pieces should still be separated from widget code.

## Implementation Phases

## Phase 1 — Data Contracts and Persistence

### Goal

Make queue rows and assignments first-class data instead of implicit UI state.

### Deliverables

- structured queue row model with stable keys
- assignment store with load/save/clear APIs
- tests covering persistence and key stability

### Completion criteria

- [ ] queue snapshots include stable keys, display names, and full paths
- [ ] assignments can be saved, loaded, and cleared independently of queue order
- [ ] controller tests cover selection and persisted assignment restoration

## Phase 2 — Palette Source and Generation Flow

### Goal

Let the queue pane source a palette from existing profiles or procedural
generation without leaving the GUI.

### Deliverables

- active-profile palette picker
- file-backed profile palette picker
- generation action built on existing `profile_generator.py`
- preview of the selected/generated colors before assignment

### Notes

Generation does not need a full-blown designer in the first pass. A compact
form with:

- harmony
- temperature
- saturation
- optional seed or randomize

is enough to drive `generate_profile()` and expose its palette set.

### Completion criteria

- [ ] queue pane can preview palettes from the active profile
- [ ] queue pane can preview palettes from another profile file
- [ ] queue pane can generate a candidate palette/profile and preview its colors

## Phase 3 — Queue UI Integration

### Goal

Turn queue selection into the main pairing workflow.

### Deliverables

- selected-song detail pane
- assignment summary on each local queue row
- assign / clear actions
- status text for "applies on replay" when current track is selected

### UI recommendations

- keep Spotify list visible but clearly marked as read-only for this feature in
  the MVP
- show assigned colors as small swatches to reduce ambiguity
- surface the assignment source, for example:
  - `warm_sunset / calm`
  - `generated / seed 42`
  - `custom / 6 colors`

### Completion criteria

- [ ] selecting a local song updates the detail pane
- [ ] assigning a palette updates both the selected-song detail and the queue row
- [ ] clearing an assignment removes the row badge and persisted entry

## Phase 4 — Session and Runtime Consumption

### Goal

Ensure the queue pairing changes actual local playback behavior.

### Deliverables

- assignment-aware profile resolution in local sessions
- deterministic derived profiles
- cache-compatible compilation behavior

### Runtime design notes

- Resolve the effective profile as late as practical, ideally right before
  compile or precompile.
- Reuse `profile_fingerprint()` so a changed assignment naturally produces a new
  cache entry.
- Keep the base session profile unchanged in memory; apply pairing through
  derivation per track.

### Completion criteria

- [ ] assigned upcoming songs compile with their derived profile
- [ ] unassigned songs still use the base session profile
- [ ] changing an assignment before playback changes the effective compile path
- [ ] current-track changes are clearly deferred, not silently ignored

## Phase 5 — Verification and UX Polish

### Goal

Harden the behavior and remove confusing edges before broader GUI expansion.

### Tests to add or extend

- `dev/tests/test_gui_queue_controller.py`
  - selection state
  - assignment summaries
  - clear assignment behavior
- `dev/tests/test_gui_services.py`
  - structured queue snapshots
  - palette loading/generation helpers
- new `dev/tests/test_gui_song_palette_store.py`
  - persistence round-trip
  - overwrite and clear semantics
- new `dev/tests/test_profile_overrides.py`
  - derived profile determinism
  - mood palette rewriting
  - profile fingerprint changes when assignment changes
- `dev/tests/test_local_session.py`
  - local playlist uses assigned profile per track
  - queue reorder does not break assignments

### Manual validation

1. Launch the GUI with a directory-based local playlist.
2. Select an upcoming song in the local queue.
3. Load an existing palette and assign it.
4. Start local preview playback and verify the assigned track compiles with the
   expected derived profile.
5. Reorder the queue and confirm the pairing follows the song, not the slot.
6. Generate a new palette, assign it to a different upcoming song, stop the GUI,
   relaunch, and confirm the assignment restores.
7. Change the currently playing track's assignment and verify the UI explains
   that it applies on replay.

## Risks and Mitigations

### Risk: palette vs profile terminology confusion

Mitigation:

- keep the UI user-facing language as "palette pairing"
- keep the runtime language as "derived profile"
- document clearly that palette pairing only changes the song's color source,
  not every other profile behavior

### Risk: generated profiles may drift from the base session style

Mitigation:

- prefer deriving from the active base profile for existing/custom palettes
- only use a fully generated profile directly when the user explicitly chooses a
  generated result

### Risk: queue state becomes too coupled to widget code

Mitigation:

- keep assignment logic in services/controllers
- keep widgets dumb and data-driven

### Risk: Spotify support expectations expand prematurely

Mitigation:

- label Spotify queue pairing as deferred in both docs and UI
- build the feature on local queue abstractions first

## Recommended order of implementation

1. add structured queue row state and stable track keys
2. add persisted assignment store
3. add derived-profile helper
4. make local playlist compilation assignment-aware
5. upgrade the queue panel UI
6. add palette loading and generation actions
7. finish tests and manual validation

## Success criteria

This feature is complete when a user can stay inside the desktop GUI, choose an
upcoming local song from the queue, preview or generate a palette, assign it to
that song, and have DreamSync use that pairing automatically the next time the
song compiles for playback.
