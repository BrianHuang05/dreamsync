# Feature 3, Component 4 — Timeline Assembler

**Status**: DONE

---

## Overview

The Timeline Assembler combines the outputs of the three preceding components — arc weights (D3.1), treatments (D3.2), and transition plans (D3.3) — into a single `ShowTimeline`. This is a straightforward assembly step with no complex logic: it maps each section's data into a `ShowCue`, attaches the beat grid and metadata from the `SongStructure`, and returns a validated `ShowTimeline` ready for playback by Component 5's `ShowPlaybackRuntime`. The assembler validates that all input lists have matching lengths and that the output passes `ShowTimeline.__post_init__` validation.

---

## Architecture

```
┌─────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  list[ArcWeight] │  │ list[Treatment]   │  │list[TransitionPlan]│
│  (from D3.1)     │  │ (from D3.2)       │  │ (from D3.3)       │
│                  │  │                   │  │                    │
│  .final_intensity│  │  .render_mode     │  │  .transition       │
│                  │  │  .color_palette   │  │  .transition_beats │
│                  │  │  .speed           │  │                    │
│                  │  │  .params          │  │                    │
└────────┬─────────┘  └────────┬──────────┘  └────────┬───────────┘
         │                     │                       │
         └─────────────┬───────┘───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│           TimelineAssembler                   │
│                                               │
│  For each section i:                          │
│    ShowCue(                                   │
│      t = sections[i].start_t,                 │
│      render_mode = treatments[i].render_mode, │
│      color_palette = treatments[i].palette,   │
│      intensity = arc_weights[i].final_intensity│
│      speed = treatments[i].speed,             │
│      params = treatments[i].params,           │
│      transition = transitions[i].transition,  │
│      transition_beats = transitions[i].beats, │
│    )                                          │
│                                               │
│  Wrap in ShowTimeline(                        │
│    song_path, duration, bpm, time_signature,  │
│    beat_times, downbeat_times,                │
│    cues=tuple(cues), metadata                 │
│  )                                            │
└──────────────────────────────────────────────┘

                       │
                       ▼
┌──────────────────────────────────────────────┐
│  ShowTimeline                                 │
│    (validated via __post_init__)               │
│    - duration > 0                             │
│    - bpm > 0                                  │
│    - time_signature in (3, 4)                 │
│    - cues non-empty                           │
│    - cues sorted by t                         │
└──────────────────────────────────────────────┘
```

### File Layout

```
src/dreamsync/
├── compiler/
│   ├── __init__.py
│   └── assemble.py          # TimelineAssembler
```

### Integration Points

- **NarrativeArcPlanner (D3.1)** — Input: `list[ArcWeight]` provides `final_intensity` per section.
- **TreatmentSelector (D3.2)** — Input: `list[Treatment]` provides `render_mode`, `color_palette`, `speed`, `params`, `effect_name` per section.
- **TransitionPlanner (D3.3)** — Input: `list[TransitionPlan]` provides `transition` (cut/fade) and `transition_beats` per section.
- **SongStructure (analyzer/models.py)** — Input: provides `sections` (for cue timing), `beat_grid` (beat/downbeat times), `path`, `duration`, `bpm`, `time_signature`, `metadata`.
- **ShowTimeline, ShowCue (show/models.py)** — Output: the exact format consumed by `ShowPlaybackRuntime` for playback.
- **compile_show() (D3.5)** — The orchestrator calls `TimelineAssembler().assemble()` as the final step.

---

## D3.4: Timeline Assembler — `TimelineAssembler`

### Design

Combines the outputs of D3.1 (arc weights), D3.2 (treatments), and D3.3 (transition plans) into a `ShowTimeline`. This is a straightforward assembly step — no complex logic, just mapping.

```python
class TimelineAssembler:
    def assemble(
        self,
        structure: SongStructure,
        arc_weights: list[ArcWeight],
        treatments: list[Treatment],
        transition_plans: list[TransitionPlan],
    ) -> ShowTimeline:
        """Build a ShowTimeline from the compiler's intermediate outputs."""
```

**Assembly logic:**

For each section `i`:
```python
ShowCue(
    t=structure.sections[i].start_t,
    render_mode=treatments[i].render_mode,
    color_palette=treatments[i].color_palette,
    intensity=arc_weights[i].final_intensity,
    speed=treatments[i].speed,
    params=treatments[i].params,
    transition=transition_plans[i].transition,
    transition_beats=transition_plans[i].transition_beats,
)
```

Then wrap in:
```python
ShowTimeline(
    song_path=structure.path,
    duration=structure.duration,
    bpm=structure.bpm,
    time_signature=structure.time_signature,
    beat_times=structure.beat_grid.beat_times,
    downbeat_times=structure.beat_grid.downbeat_times,
    cues=tuple(cues),
    metadata=structure.metadata,
)
```

**Validation:**

1. **Input length check**: `len(sections) == len(arc_weights) == len(treatments) == len(transition_plans)`. If mismatched, raise `ValueError` with a message listing all lengths.
2. **ShowTimeline.__post_init__** handles output validation: duration > 0, bpm > 0, time_signature in (3, 4), cues non-empty, cues sorted by t.
3. The assembler does **not** sort cues — sections are already in time order, so cues inherit that order. If sections are out of order (which shouldn't happen), `ShowTimeline.__post_init__` will catch it.

### Implementation Steps

1. Create `src/dreamsync/compiler/assemble.py`:
   - Import `SongStructure` from `dreamsync.analyzer.models`.
   - Import `ShowTimeline`, `ShowCue` from `dreamsync.show.models`.
   - Import `ArcWeight` from `dreamsync.compiler.arc`.
   - Import `Treatment` from `dreamsync.compiler.treatments`.
   - Import `TransitionPlan` from `dreamsync.compiler.transitions`.
   - `TimelineAssembler`:
     - `assemble(structure, arc_weights, treatments, transition_plans)`:
       1. Get `sections = structure.sections`.
       2. Validate lengths:
          ```python
          n = len(sections)
          if len(arc_weights) != n or len(treatments) != n or len(transition_plans) != n:
              raise ValueError(
                  f"Input length mismatch: sections={n}, "
                  f"arc_weights={len(arc_weights)}, "
                  f"treatments={len(treatments)}, "
                  f"transition_plans={len(transition_plans)}"
              )
          ```
       3. Build cue list:
          ```python
          cues = []
          for i in range(n):
              cues.append(ShowCue(
                  t=sections[i].start_t,
                  render_mode=treatments[i].render_mode,
                  color_palette=treatments[i].color_palette,
                  intensity=arc_weights[i].final_intensity,
                  speed=treatments[i].speed,
                  params=treatments[i].params,
                  transition=transition_plans[i].transition,
                  transition_beats=transition_plans[i].transition_beats,
              ))
          ```
       4. Construct and return `ShowTimeline`:
          ```python
          return ShowTimeline(
              song_path=structure.path,
              duration=structure.duration,
              bpm=structure.bpm,
              time_signature=structure.time_signature,
              beat_times=structure.beat_grid.beat_times,
              downbeat_times=structure.beat_grid.downbeat_times,
              cues=tuple(cues),
              metadata=structure.metadata,
          )
          ```
       5. `ShowTimeline.__post_init__` runs automatically and will raise `ValueError` if the timeline is invalid.

### Done When

- [ ] Produces a valid `ShowTimeline` that passes `ShowTimeline.__post_init__` validation
- [ ] One `ShowCue` per section, with correct field mapping from all three inputs
- [ ] `ShowCue.t` matches `Section.start_t` for each section
- [ ] `ShowCue.intensity` comes from `ArcWeight.final_intensity` (not `Section.energy_mean`)
- [ ] `ShowCue.render_mode` comes from `Treatment.render_mode`
- [ ] `ShowCue.color_palette` comes from `Treatment.color_palette`
- [ ] `ShowCue.speed` comes from `Treatment.speed`
- [ ] `ShowCue.params` comes from `Treatment.params`
- [ ] `ShowCue.transition` comes from `TransitionPlan.transition`
- [ ] `ShowCue.transition_beats` comes from `TransitionPlan.transition_beats`
- [ ] Cues are sorted by `t` (inherits from section ordering)
- [ ] Beat grid is correctly copied from `SongStructure.beat_grid`
- [ ] Metadata is preserved from `SongStructure.metadata`
- [ ] Mismatched input lengths raise a clear `ValueError` with all lengths listed
- [ ] Output is JSON-serializable via `ShowTimeline.to_json()`
- [ ] Round-trip: `ShowTimeline.from_dict(tl.to_dict())` produces equivalent timeline
- [ ] 8 unit tests passing

---

## Tests

All tests in `dev/tests/test_compiler_assemble.py`. Target: **8 tests**.

| # | Test Name | Description |
|---|-----------|-------------|
| 1 | `test_basic_assembly` | 3-section song: verify ShowTimeline has 3 cues with correct field mapping |
| 2 | `test_field_mapping_intensity` | Verify cue intensity comes from ArcWeight.final_intensity, not Section.energy_mean |
| 3 | `test_field_mapping_render_mode` | Verify cue render_mode comes from Treatment.render_mode |
| 4 | `test_beat_grid_copied` | Verify beat_times and downbeat_times match structure.beat_grid |
| 5 | `test_metadata_preserved` | Verify metadata dict from SongStructure appears in ShowTimeline |
| 6 | `test_mismatched_lengths_error` | Pass lists of different lengths: verify ValueError with informative message |
| 7 | `test_json_round_trip` | Assemble timeline, serialize to dict, deserialize: verify equivalence |
| 8 | `test_single_section` | Song with one section: produces valid timeline with one cue |

### Test Strategy

- Construct all intermediate outputs (`ArcWeight`, `Treatment`, `TransitionPlan`) manually with known values. No dependency on D3.1/D3.2/D3.3 implementation — use the frozen dataclasses directly.
- Construct `SongStructure` in-memory with `Section` tuples, a `BeatGrid`, and placeholder metadata. No audio files needed.
- Verify exact field values in the assembled `ShowTimeline` (not just types).
- For the round-trip test: use `ShowTimeline.to_dict()` and `ShowTimeline.from_dict()` to verify serialization fidelity.
- Create helper factories for `SongStructure`, `ArcWeight`, `Treatment`, `TransitionPlan` to reduce test boilerplate.

---

## Parameters

This component has no configurable parameters. It is a pure mapping operation.

---

## Build Order

| Phase | Step | Files Created/Modified |
|-------|------|----------------------|
| 1 | Implement `TimelineAssembler` | `src/dreamsync/compiler/assemble.py` |
| 2 | Write unit tests | `dev/tests/test_compiler_assemble.py` |

Note: This component depends on the **data models** from D3.1, D3.2, and D3.3 (i.e., it imports `ArcWeight`, `Treatment`, `TransitionPlan`), but it does **not** depend on their business logic. The assembler can be implemented as soon as those dataclasses exist, even before the planners/selectors are complete.

---

## Non-Goals

- **Cue merging or deduplication**: The assembler creates exactly one cue per section. It does not merge adjacent sections with identical treatments into a single cue.
- **Cue interpolation**: The assembler does not insert intermediate cues between sections (e.g., for gradual palette transitions). Within-section animation is handled by the renderer at playback time.
- **Validation beyond post_init**: The assembler relies on `ShowTimeline.__post_init__` for structural validation. It does not check whether render_mode values are valid `RenderMode` strings or whether palette colors are valid hex — those are the responsibility of the upstream components.
- **Show file writing**: The assembler returns a `ShowTimeline` object. File I/O (`to_json()`) is the caller's responsibility (the orchestrator or CLI).

---

## Dependencies

| Dependency | Type | Notes |
|------------|------|-------|
| `SongStructure` | Existing code | `src/dreamsync/analyzer/models.py` — structure, sections, beat_grid, metadata |
| `ShowTimeline`, `ShowCue` | Existing code | `src/dreamsync/show/models.py` — output format |
| `ArcWeight` | Feature 3, D3.1 | `src/dreamsync/compiler/arc.py` — import for type |
| `Treatment` | Feature 3, D3.2 | `src/dreamsync/compiler/treatments.py` — import for type |
| `TransitionPlan` | Feature 3, D3.3 | `src/dreamsync/compiler/transitions.py` — import for type |
| `dataclasses` | Stdlib | (Implicit — used by upstream dataclasses) |

No new pip dependencies required. This component is pure data mapping — no DSP, no audio, no I/O, no randomness.
