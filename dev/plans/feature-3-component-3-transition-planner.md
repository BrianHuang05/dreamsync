# Feature 3, Component 3 — Transition Planner

**Status**: DONE

---

## Overview

The Transition Planner decides the transition type (hard cut vs. crossfade) and fade duration (in beats) for each section boundary. Transitions are chosen based on a rules table that maps `(outgoing_label, incoming_label)` pairs to transition parameters. Fade durations are aligned to the beat grid and capped at a configurable maximum. The planner produces one `TransitionPlan` per section, including the first section (which always gets a hard cut). Component 3 is a pure rule-evaluation step — no randomness, no profiles, no audio.

---

## Architecture

```
┌──────────────────────────────────────────────┐
│  Inputs:                                      │
│    sections: tuple[Section, ...]              │
│    beat_grid: BeatGrid                        │
│      .beat_times: tuple[float, ...]           │
│      .downbeat_times: tuple[float, ...]       │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│          TransitionPlanner                    │
│                                               │
│  For each section boundary (i-1 → i):         │
│                                               │
│  1. Look up (outgoing.label, incoming.label)  │
│     in rules table                            │
│                                               │
│  2. Rules table:                              │
│     ┌──────────┬──────────┬──────┬───────┐   │
│     │ Outgoing │ Incoming │ Type │ Beats │   │
│     ├──────────┼──────────┼──────┼───────┤   │
│     │ (first)  │ any      │ cut  │ 0     │   │
│     │ any      │ drop     │ cut  │ 0     │   │
│     │ any      │ intro    │ cut  │ 0     │   │
│     │ chorus   │ chorus   │ cut  │ 0     │   │
│     │ verse,   │ chorus,  │ fade │ 2     │   │
│     │  bridge  │  drop    │      │       │   │
│     │ chorus,  │ verse,   │ fade │ 4     │   │
│     │  drop    │  bridge, │      │       │   │
│     │          │  breakdown│     │       │   │
│     │ verse    │ verse    │ fade │ 4     │   │
│     │ bridge,  │ chorus,  │ fade │ 2     │   │
│     │ breakdown│  drop    │      │       │   │
│     │ any      │ outro    │ fade │ 8     │   │
│     │ (default)│ (default)│ fade │ 4     │   │
│     └──────────┴──────────┴──────┴───────┘   │
│                                               │
│  3. Cap fade beats at max_fade_beats          │
│  4. Reduce if insufficient beats remain       │
│     at the boundary timestamp                 │
│                                               │
│  Output: list[TransitionPlan] — one per       │
│          section                              │
└──────────────────────────────────────────────┘
```

### File Layout

```
src/dreamsync/
├── compiler/
│   ├── __init__.py
│   └── transitions.py      # TransitionPlan, TransitionPlanner
```

### Integration Points

- **Section (analyzer/sections.py)** — Input: `Section.label` determines which rule applies. Labels are: `"intro"`, `"verse"`, `"chorus"`, `"bridge"`, `"drop"`, `"outro"`, `"breakdown"`.
- **BeatGrid (analyzer/bpm.py)** — Input: `BeatGrid.beat_times` is used to verify that enough beats remain after a boundary for the requested fade duration.
- **TimelineAssembler (Feature 3, Component 4)** — Output: `list[TransitionPlan]` is consumed by the assembler to set `ShowCue.transition` and `ShowCue.transition_beats`.
- **compile_show() (Feature 3, Component 5)** — The orchestrator creates a `TransitionPlanner` and calls `plan()`.

---

## D3.3: Transition Planner — `TransitionPlanner`

### Design

Decides the transition type (cut vs. fade) and fade duration (in beats) for each section boundary. Transitions are aligned to bar boundaries using the beat grid.

```python
@dataclass(frozen=True)
class TransitionPlan:
    section_index: int
    transition: str             # "cut" | "fade"
    transition_beats: int       # 0 for cut, N for fade (must be > 0 if fade)

class TransitionPlanner:
    def __init__(
        self,
        default_fade_beats: int = 4,
        max_fade_beats: int = 16,
    ) -> None: ...

    def plan(
        self,
        sections: tuple[Section, ...],
        beat_grid: BeatGrid,
    ) -> list[TransitionPlan]:
        """Decide transitions for all section boundaries."""
```

**Transition rules table (in priority order):**

Rules are evaluated top-to-bottom. The first matching rule wins.

| # | Outgoing Label | Incoming Label | Transition | Beats | Rationale |
|---|----------------|----------------|------------|-------|-----------|
| 0 | — (first section) | any | cut | 0 | Song starts clean — no preceding section to fade from |
| 1 | any | intro | cut | 0 | Intro should start fresh (handles edge case of repeated intros) |
| 2 | any | drop | cut | 0 | Drops hit instantly — no fade-in dilutes the impact |
| 3 | chorus | chorus | cut | 0 | Maintain energy; fresh palette swap on the hard boundary |
| 4 | verse, bridge | chorus | fade | 2 | Quick 2-beat build into high-energy chorus |
| 5 | bridge, breakdown | chorus | fade | 2 | Short buildup before climax |
| 6 | bridge, breakdown | drop | cut | 0 | (Overridden by rule 2 — drops always cut) |
| 7 | chorus, drop | verse | fade | 4 | Gentle 4-beat pull-back from peak energy |
| 8 | chorus, drop | bridge | fade | 4 | Gentle wind-down into bridge |
| 9 | chorus, drop | breakdown | fade | 4 | Gentle wind-down into breakdown |
| 10 | verse | verse | fade | 4 | Smooth continuation between similar-energy sections |
| 11 | any | outro | fade | 8 | Long wind-down to close the show |
| 12 | (default) | (default) | fade | `default_fade_beats` | Catch-all for unlisted label combinations |

**Beat availability check:**

After determining `transition_beats` from the rules table:
1. Find the boundary time: `t_boundary = sections[i].start_t`.
2. Count how many beats in `beat_grid.beat_times` are >= `t_boundary` and < `sections[i].end_t` (i.e., beats available in the incoming section).
3. If `available_beats < transition_beats`, reduce `transition_beats` to `available_beats`.
4. If `transition_beats` would become 0, switch to `transition = "cut"`.

### Implementation Steps

1. Create `src/dreamsync/compiler/transitions.py`:
   - Import `Section` from `dreamsync.analyzer.sections`.
   - Import `BeatGrid` from `dreamsync.analyzer.bpm`.
   - `TransitionPlan` frozen dataclass with `section_index`, `transition`, `transition_beats`.
   - `TransitionPlanner`:
     - `__init__(default_fade_beats, max_fade_beats)` — store config.
     - Define `_RULES` as a class-level or instance-level list of `(outgoing_labels, incoming_labels, transition, beats)` tuples. Use `None` for "any" match.
     - `plan(sections, beat_grid)`:
       1. Handle empty sections: return `[]`.
       2. Handle single section: return `[TransitionPlan(0, "cut", 0)]`.
       3. First section always gets `TransitionPlan(0, "cut", 0)`.
       4. For each subsequent section `i` (from 1 to len-1):
          a. `outgoing = sections[i - 1]`, `incoming = sections[i]`.
          b. Iterate `_RULES` in priority order. Check if `outgoing.label` matches outgoing set and `incoming.label` matches incoming set.
          c. First matching rule determines `transition` and `beats`.
          d. If no rule matches, use `("fade", self._default_fade_beats)`.
          e. Cap: `beats = min(beats, self._max_fade_beats)`.
          f. Check beat availability at `incoming.start_t`: count beats in `beat_grid.beat_times` within `[incoming.start_t, incoming.end_t)`. If `available < beats`, reduce.
          g. If `beats == 0` and `transition == "fade"`, switch to `"cut"`.
          h. Append `TransitionPlan(section_index=i, transition=transition, transition_beats=beats)`.
       5. Return list of `TransitionPlan`s.
     - `_count_beats_in_range(beat_grid, start_t, end_t)` — helper: returns count of beats in `beat_grid.beat_times` within `[start_t, end_t)`. Use `bisect` for efficiency.

### Done When

- [ ] First section always gets `transition="cut", transition_beats=0`
- [ ] Drop sections always get `transition="cut"` (instant impact)
- [ ] Intro sections always get `transition="cut"` (fresh start)
- [ ] Outro sections get a long fade (8 beats by default)
- [ ] Chorus-to-chorus boundaries get a hard cut (fresh energy)
- [ ] Verse-to-chorus boundaries get a short fade (2-beat build)
- [ ] Bridge/breakdown-to-chorus boundaries get a short fade (2-beat build)
- [ ] Chorus/drop-to-verse boundaries get a medium fade (4-beat pull-back)
- [ ] Verse-to-verse boundaries get a smooth fade (4 beats)
- [ ] Default fade applies when no specific rule matches
- [ ] Fade beats are capped at `max_fade_beats`
- [ ] Fade beats are reduced when insufficient beats remain in the incoming section
- [ ] Reduced-to-zero fade becomes a cut (transition_beats=0 → transition="cut")
- [ ] Single-section song gets a cut with 0 beats
- [ ] Empty sections list returns empty list
- [ ] 10 unit tests passing

---

## Tests

All tests in `dev/tests/test_compiler_transitions.py`. Target: **10 tests**.

| # | Test Name | Description |
|---|-----------|-------------|
| 1 | `test_first_section_always_cut` | Any section at index 0 gets cut with 0 beats |
| 2 | `test_drop_always_cut` | Incoming drop section always gets cut regardless of outgoing label |
| 3 | `test_outro_long_fade` | Incoming outro gets 8-beat fade |
| 4 | `test_chorus_to_chorus_cut` | Chorus followed by chorus gets a hard cut |
| 5 | `test_verse_to_chorus_short_fade` | Verse → chorus gets a 2-beat fade |
| 6 | `test_chorus_to_verse_medium_fade` | Chorus → verse gets a 4-beat fade |
| 7 | `test_default_fallback` | Unknown label pair (e.g. "unknown" → "unknown") gets default_fade_beats |
| 8 | `test_max_fade_cap` | Custom max_fade_beats=2: a normally-8-beat outro transition is capped at 2 |
| 9 | `test_insufficient_beats_reduction` | Section with very few beats available: transition_beats reduced to available count |
| 10 | `test_single_section` | Song with one section: returns `[TransitionPlan(0, "cut", 0)]` |

### Test Strategy

- Construct `Section` tuples with specific labels, `start_t`, `end_t` values. Energy, mood, BPM, section_id can be placeholder values.
- Construct `BeatGrid` with known `beat_times` tuples to control beat availability.
- Verify exact `transition` and `transition_beats` values in the output.
- For the insufficient beats test: create a short section (e.g. 1.0s) with only 2 beats, and request an 8-beat fade — verify it reduces to 2.
- No randomness — all tests are deterministic.

---

## Parameters

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| `default_fade_beats` | 4 | 4-beat crossfade = 1 bar at 4/4, a natural transition length for unlisted combinations |
| `max_fade_beats` | 16 | Cap at 4 bars — longer fades feel sluggish and blur section boundaries |

---

## Build Order

| Phase | Step | Files Created/Modified |
|-------|------|----------------------|
| 1 | Implement `TransitionPlan` and `TransitionPlanner` | `src/dreamsync/compiler/transitions.py` |
| 2 | Write unit tests | `dev/tests/test_compiler_transitions.py` |

Note: This component is independent of D3.1 (Arc Planner) and D3.2 (Treatment Selector). All three can be built in parallel.

---

## Non-Goals

- **Bar-aligned fade start**: The planner specifies fade duration in beats, not the exact start timestamp. The renderer is responsible for aligning the fade to the nearest bar boundary at playback time.
- **Variable fade curves**: All fades are linear crossfades. There is no support for exponential, S-curve, or other fade shapes. The renderer applies a simple linear interpolation.
- **Per-beat transition cues**: The planner produces one transition decision per section boundary. It does not generate intermediate cues (e.g. "at beat 3 of the fade, shift palette 50%"). The renderer handles continuous interpolation.
- **Section-internal transitions**: The planner only handles inter-section boundaries. There is no mechanism for mid-section lighting changes (e.g. a 4-bar intensity ramp within a verse). Those would require sub-section cues, which are out of scope.

---

## Dependencies

| Dependency | Type | Notes |
|------------|------|-------|
| `Section` | Existing code | `src/dreamsync/analyzer/sections.py` — `label`, `start_t`, `end_t` |
| `BeatGrid` | Existing code | `src/dreamsync/analyzer/bpm.py` — `beat_times` for availability check |
| `bisect` | Stdlib | Efficient beat counting in range |
| `dataclasses` | Stdlib | Frozen dataclass for `TransitionPlan` |

No new pip dependencies required. This component is pure rule evaluation — no DSP, no audio, no I/O, no randomness.
