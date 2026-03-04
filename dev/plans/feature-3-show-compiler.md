# Feature 3 — Show Compiler

**Status**: **IN PROGRESS** (C1–C4 done, C5 remaining)

---

## Overview

- The Show Compiler takes a `SongStructure` (produced by Feature 2's Analyzer) and a `ProfileConfig` (the active lighting profile), and compiles a complete `ShowTimeline` — a self-contained JSON file of timestamped lighting cues that the Show Playback Runtime (Feature 2, Component 5) can play back directly.
- For each detected section (intro, verse, chorus, bridge, drop, outro), the compiler selects a lighting treatment: effect mode, color palette, intensity, speed, and renderer parameters — using the profile's mood→effect/palette pools.
- A narrative arc engine adjusts intensity and effect selection across the song so the show builds tension through verse → pre-chorus → chorus, resets at bridge, and peaks at the final chorus.
- Transition planning decides whether each section boundary is a hard cut or a crossfade, and how many beats the crossfade spans — aligned to bar boundaries.
- The output is the exact `ShowTimeline` format defined in Component 5's `show/models.py`, so the compiler and player are fully decoupled.

---

## Pain Points

| Pain Point | Description |
|---|---|
| Treatment variety | Avoiding monotony — the same mood appearing in multiple sections (e.g. two verses both labelled "groove") should get different effects/palettes so the show doesn't feel repetitive |
| Narrative arc | Translating the abstract idea of "build energy across the song" into concrete parameter adjustments (intensity curves, speed ramps, palette warmth) without over-engineering |
| Profile compatibility | The compiler must work with any valid profile (built-in or custom YAML), falling back to built-in `MOOD_EFFECTS` / `MOOD_PALETTES` when a profile doesn't override a mood's effect pool |

---

## Architecture

```
                     ┌──────────────────────────────┐
                     │        SongStructure           │
                     │  (from Analyzer / Feature 2)   │
                     │  sections, beat_grid, bpm,     │
                     │  tempo_regions, metadata        │
                     └──────────────┬───────────────┘
                                    │
                                    ▼
                     ┌──────────────────────────────┐
                     │      NarrativeArcPlanner       │
                     │  Assign per-section intensity   │
                     │  multipliers based on position  │
                     │  in the song and section label  │
                     └──────────────┬───────────────┘
                                    │ sections + arc weights
                                    ▼
┌────────────────┐   ┌──────────────────────────────┐
│  ProfileConfig │──▶│      TreatmentSelector         │
│  (active       │   │  mood → effect pool → pick     │
│   profile)     │   │  mood → palette pool → pick    │
│                │   │  effect → render_mode + params  │
│  OR            │   │  Avoids repeating same effect   │
│  Built-in      │   │  in consecutive same-mood       │
│  defaults      │   │  sections                       │
└────────────────┘   └──────────────┬───────────────┘
                                    │ per-section treatments
                                    ▼
                     ┌──────────────────────────────┐
                     │      TransitionPlanner         │
                     │  Decide cut vs. fade per       │
                     │  section boundary              │
                     │  Compute transition_beats      │
                     │  Snap to bar boundaries         │
                     └──────────────┬───────────────┘
                                    │ treatments + transitions
                                    ▼
                     ┌──────────────────────────────┐
                     │      TimelineAssembler         │
                     │  Combine treatments, arc       │
                     │  weights, transitions into      │
                     │  ShowCue list                   │
                     │  Attach beat grid + metadata    │
                     │  → ShowTimeline                 │
                     └──────────────────────────────┘


Orchestrator:  compile_show(structure, profile) → ShowTimeline
               - Wires arc → treatments → transitions → assembly
               - Single function entry point
               - Also exposed as CLI subcommand
```

### File Layout

```
src/dreamsync/
├── compiler/
│   ├── __init__.py
│   ├── arc.py                  # NarrativeArcPlanner (D3.1)
│   ├── treatments.py           # TreatmentSelector (D3.2)
│   ├── transitions.py          # TransitionPlanner (D3.3)
│   ├── assemble.py             # TimelineAssembler (D3.4)
│   └── compile.py              # compile_show() orchestrator (D3.5)
```

### Integration Points

- **Feature 2, Component 4 (Analyzer)** — `SongStructure` is the input. The compiler consumes `sections`, `beat_grid`, `bpm`, `tempo_regions`, `duration`, `metadata`.
- **Feature 2, Component 5 (Show Player)** — `ShowTimeline` is the output. The compiler produces the exact format that `ShowPlaybackRuntime` consumes.
- **Profile system** — `ProfileConfig` drives palette/effect selection. Falls back to built-in `MOOD_PALETTES` / `MOOD_EFFECTS` when a profile doesn't define overrides.
- **cli.py** — `compile` subcommand for one-off compilation. `compile-and-play` for the full pipeline.
- **session.py** — Future: auto-compile shows when the Analyzer produces a new `SongStructure` during a Spotify-connected session.

---

## D3.1: Narrative Arc Planner — `NarrativeArcPlanner`

### Design

Assigns an intensity multiplier (0.0–1.0) to each section based on its position in the song's narrative arc. The goal: energy builds across verse → pre-chorus → chorus, resets at bridge/breakdown, and peaks at the final chorus. This multiplier scales the raw `section.energy_mean` to produce the final `ShowCue.intensity`.

The arc planner does not change moods or labels — it only adjusts how "bright" / "intense" each section feels relative to the rest of the song. A verse near the end of the song should be slightly more intense than the first verse, even if both have the same energy_mean.

```python
@dataclass(frozen=True)
class ArcWeight:
    section_index: int
    base_energy: float          # section.energy_mean (unchanged)
    arc_multiplier: float       # 0.0–1.0 (position-based scaling)
    final_intensity: float      # base_energy * arc_multiplier, clamped to [0.05, 1.0]

class NarrativeArcPlanner:
    def __init__(
        self,
        intro_intensity: float = 0.15,
        outro_intensity: float = 0.10,
        peak_boost: float = 1.2,        # final chorus gets 120% of its natural energy
        buildup_ramp: float = 0.15,      # per-repeat intensity increase for same label
    ) -> None: ...

    def plan(self, sections: tuple[Section, ...]) -> list[ArcWeight]:
        """Compute arc weights for each section."""
```

**Algorithm:**

1. **Base energy**: Start with each section's `energy_mean` as the raw intensity.
2. **Position ramp**: Sections later in the song get a slight boost. For sections with the same label (e.g. verse appearing 3 times), each occurrence gets `+buildup_ramp` more intensity than the previous.
3. **Intro / outro dampening**: Sections labelled `intro` are capped at `intro_intensity`. Sections labelled `outro` are capped at `outro_intensity`.
4. **Final chorus boost**: The last section labelled `chorus` (or `drop`) gets multiplied by `peak_boost`.
5. **Bridge reset**: Sections labelled `bridge` or `breakdown` get their raw energy reduced by 20% to create a deliberate energy valley before the next chorus.
6. **Clamp**: All final intensities are clamped to `[0.05, 1.0]`.

### Implementation Steps

1. Create `src/dreamsync/compiler/__init__.py` (empty).
2. Create `src/dreamsync/compiler/arc.py`:
   - `ArcWeight` frozen dataclass.
   - `NarrativeArcPlanner`:
     - `__init__` — store config parameters.
     - `plan(sections)`:
       1. Build a label occurrence counter (e.g. `{"verse": 0, "chorus": 0, ...}`).
       2. Identify the index of the last `chorus` or `drop` section.
       3. Iterate sections in order:
          a. Compute positional boost: `occurrence_count * buildup_ramp`.
          b. Apply label-specific rules (intro cap, outro cap, bridge reduction, final chorus boost).
          c. Compute `arc_multiplier` from the combination.
          d. Compute `final_intensity = clamp(energy_mean * arc_multiplier, 0.05, 1.0)`.
          e. Increment occurrence counter for this label.
       4. Return list of `ArcWeight`s.

### Done When

- [ ] Each section gets an `ArcWeight` with a valid `final_intensity` in [0.05, 1.0]
- [ ] Repeated sections (verse 1, verse 2, verse 3) have progressively increasing intensity
- [ ] Intro sections are capped at low intensity regardless of energy_mean
- [ ] Outro sections are capped at low intensity
- [ ] The final chorus/drop has the highest intensity in the song
- [ ] Bridge/breakdown sections create a visible energy dip
- [ ] A single-section song (no structure) gets a flat arc with intensity = energy_mean
- [ ] 12 unit tests covering: basic arc shape, repeated sections, intro/outro caps, final chorus boost, bridge dip, single section, all-same-label, edge cases (very short song, no chorus)

---

## D3.2: Treatment Selector — `TreatmentSelector`

### Design

Given a section's mood and the active profile, selects a concrete lighting treatment: effect name, render mode, color palette, renderer parameters, and speed. Uses the profile's mood-to-effect/palette pools, falling back to built-in `MOOD_EFFECTS` / `MOOD_PALETTES` defaults.

To avoid repetition when consecutive sections share a mood (e.g. verse → verse, both "groove"), the selector tracks recently-used effects and palettes and avoids re-picking them.

```python
@dataclass(frozen=True)
class Treatment:
    render_mode: str              # "solid" | "pulse" | "breathe" | "scroll" | "wave" | "gradient"
    color_palette: tuple[str, ...]  # hex colors from the selected palette
    params: dict                  # renderer-specific params (pulse_decay, breathe_rate_mult, etc.)
    speed: float                  # effect speed multiplier
    effect_name: str              # for debugging/logging: which effect preset was chosen

class TreatmentSelector:
    def __init__(
        self,
        profile: ProfileConfig | None = None,
        seed: int | None = None,          # for deterministic tests
    ) -> None: ...

    def select(
        self,
        mood: str,
        section_label: str,
        section_bpm: float,
    ) -> Treatment:
        """Pick a treatment for one section. Avoids repeating the previous selection."""

    def reset(self) -> None:
        """Clear history (call between songs)."""
```

**Selection logic:**

1. **Effect pool**: Look up `profile.moods[mood].effects` if defined, otherwise `MOOD_EFFECTS[Mood(mood)]`. The pool is a list of `(name, weight)` pairs.
2. **Weighted random selection**: Pick an effect from the pool using weights. If the picked effect matches the previously-used effect for this mood, re-roll once. (If still a repeat, accept it — better than infinite loops with small pools.)
3. **Palette pool**: Look up `profile.moods[mood].palettes` if defined, otherwise `MOOD_PALETTES[Mood(mood)]`. Each entry is a palette name that resolves to a tuple of hex colors.
4. **Palette selection**: Pick a palette from the pool. If it matches the previous palette for this mood, re-roll once.
5. **Resolve palette name**: Look up in `profile.palettes` first, then fall back to built-in `PALETTES`.
6. **Render mode**: Determined by the selected effect's `EffectPreset.render_mode`.
7. **Params**: Start with the effect's default params, merge profile-level `moods[mood].params` on top.
8. **Speed**: Map section BPM to a speed multiplier:
   - < 90 BPM → speed 0.3
   - 90–120 BPM → speed 0.5
   - 120–140 BPM → speed 0.7
   - 140+ BPM → speed 0.9
   - DROP mood always → speed 1.0

**Special cases:**

- **DROP sections**: Always use `drop_blast` effect (hard-coded in v2 behavior). Palette from profile's `drop` mood pool.
- **Transition palettes**: If the profile defines a `TransitionRule` matching `from_mood → to_mood`, force that palette on the incoming section.

### Implementation Steps

1. Create `src/dreamsync/compiler/treatments.py`:
   - `Treatment` frozen dataclass.
   - `TreatmentSelector`:
     - `__init__(profile, seed)`:
       1. Store profile (may be None → use built-in defaults).
       2. Create `random.Random(seed)` for deterministic selection.
       3. Initialize `_last_effect: dict[str, str]` — per-mood last-used effect name.
       4. Initialize `_last_palette: dict[str, str]` — per-mood last-used palette name.
     - `select(mood, section_label, section_bpm)`:
       1. Resolve effect pool (profile or built-in).
       2. Weighted random pick with one re-roll on repeat.
       3. Resolve palette pool (profile or built-in).
       4. Pick palette with one re-roll on repeat.
       5. Resolve palette name to hex colors.
       6. Get render_mode from the `EffectPreset`.
       7. Merge params: effect defaults + profile mood params.
       8. Compute speed from BPM.
       9. Update `_last_effect[mood]`, `_last_palette[mood]`.
       10. Return `Treatment(...)`.
     - `reset()` — clear `_last_effect`, `_last_palette`.

### Done When

- [ ] Every valid mood ("chill", "groove", "hype", "drop") produces a valid `Treatment`
- [ ] Treatment uses profile overrides when available, falls back to built-in defaults when not
- [ ] Consecutive same-mood sections get different effects (when pool size > 1)
- [ ] Consecutive same-mood sections get different palettes (when pool size > 1)
- [ ] DROP mood always selects `drop_blast` effect
- [ ] Profile transition rules force the correct palette when matched
- [ ] Speed multiplier scales with BPM correctly
- [ ] Params merge order is correct: effect defaults ← profile mood params (profile wins)
- [ ] Works with `profile=None` (all built-in defaults)
- [ ] Deterministic with a fixed seed (for testing)
- [ ] 15 unit tests covering: each mood, profile overrides, built-in fallback, repeat avoidance, drop special case, transition rules, speed mapping, param merging, no-profile mode, deterministic seed

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

**Transition rules:**

| Incoming Section | Outgoing Section | Transition | Beats | Rationale |
|---|---|---|---|---|
| (any) | intro | cut | 0 | Song starts clean |
| verse, bridge | chorus, drop | fade | 2 | Quick build into high-energy |
| chorus, drop | verse, bridge, breakdown | fade | 4 | Gentle pull-back from peak |
| verse | verse | fade | 4 | Smooth continuation |
| chorus | chorus | cut | 0 | Maintain energy, fresh palette swap |
| (any) | drop | cut | 0 | Drops hit instantly — no fade-in |
| (any) | outro | fade | 8 | Long wind-down |
| bridge, breakdown | chorus, drop | fade | 2 | Short buildup before climax |

- All fade durations are capped at `max_fade_beats`.
- If the beat grid has fewer remaining beats than `transition_beats` at a boundary, reduce to the available beats.

### Implementation Steps

1. Create `src/dreamsync/compiler/transitions.py`:
   - `TransitionPlan` frozen dataclass.
   - `TransitionPlanner`:
     - `__init__(default_fade_beats, max_fade_beats)` — store config.
     - `plan(sections, beat_grid)`:
       1. First section always gets `cut, 0` (no preceding section).
       2. For each subsequent section, look up the rule based on `(outgoing.label, incoming.label)` pair.
       3. If no specific rule matches, use `fade` with `default_fade_beats`.
       4. Cap at `max_fade_beats`.
       5. Verify beats are available at the boundary timestamp.
       6. Return list of `TransitionPlan`s (one per section, including the first).

### Done When

- [ ] First section always gets `transition="cut", transition_beats=0`
- [ ] Drop sections always get `transition="cut"` (instant impact)
- [ ] Outro sections get a long fade
- [ ] Chorus-to-chorus boundaries get a hard cut (fresh energy)
- [ ] Verse-to-chorus boundaries get a short fade (quick build)
- [ ] Default fade applies when no specific rule matches
- [ ] Fade beats are capped at `max_fade_beats`
- [ ] Fade beats are reduced when insufficient beats remain
- [ ] Single-section songs get a cut with 0 beats
- [ ] 10 unit tests covering: first section, drop cut, outro fade, chorus-chorus cut, verse-chorus fade, default fallback, max cap, insufficient beats, single section, all labels

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

### Implementation Steps

1. Create `src/dreamsync/compiler/assemble.py`:
   - `TimelineAssembler`:
     - `assemble(structure, arc_weights, treatments, transition_plans)`:
       1. Validate input lengths match: `len(sections) == len(arc_weights) == len(treatments) == len(transition_plans)`.
       2. Build one `ShowCue` per section using the mapping above.
       3. Construct `ShowTimeline` with beat grid copied from `structure.beat_grid`.
       4. Return the timeline (validation happens in `ShowTimeline.__post_init__`).

### Done When

- [ ] Produces a valid `ShowTimeline` that passes `ShowTimeline.__post_init__` validation
- [ ] Cues are sorted by `t` (inherits from section ordering)
- [ ] Beat grid is correctly copied from `SongStructure.beat_grid`
- [ ] Metadata is preserved from `SongStructure.metadata`
- [ ] Mismatched input lengths raise a clear error
- [ ] Output is JSON-serializable via `ShowTimeline.to_json()`
- [ ] Round-trip: `ShowTimeline.from_json(tl.to_json(path))` matches original
- [ ] 8 unit tests covering: basic assembly, field mapping correctness, validation pass-through, mismatched lengths, JSON round-trip, single section, many sections, empty metadata

---

## D3.5: Orchestrator — `compile_show()`

### Design

The top-level entry point that wires D3.1–D3.4 together. Single function call: structure in, timeline out.

```python
def compile_show(
    structure: SongStructure,
    profile: ProfileConfig | None = None,
    *,
    seed: int | None = None,
    intro_intensity: float = 0.15,
    outro_intensity: float = 0.10,
    peak_boost: float = 1.2,
    buildup_ramp: float = 0.15,
    default_fade_beats: int = 4,
    max_fade_beats: int = 16,
) -> ShowTimeline:
    """Compile a SongStructure into a ShowTimeline.

    Steps:
    1. Plan narrative arc → arc_weights
    2. Select treatments for each section → treatments
    3. Plan transitions → transition_plans
    4. Assemble timeline → ShowTimeline
    """
```

### Implementation Steps

1. Create `src/dreamsync/compiler/compile.py`:
   - `compile_show(structure, profile, *, ...)`:
     1. `NarrativeArcPlanner(intro_intensity, outro_intensity, peak_boost, buildup_ramp).plan(structure.sections)` → `arc_weights`.
     2. Create `TreatmentSelector(profile, seed)`.
     3. For each section, call `selector.select(section.mood, section.label, section.bpm)` → `treatments`.
     4. `TransitionPlanner(default_fade_beats, max_fade_beats).plan(structure.sections, structure.beat_grid)` → `transition_plans`.
     5. `TimelineAssembler().assemble(structure, arc_weights, treatments, transition_plans)` → `ShowTimeline`.
     6. Return the timeline.

2. Add CLI subcommands to `cli.py`:
   - `compile` subcommand:
     - `dreamsync compile structure.json --profile midnight_rave --output show.json`
     - Loads `SongStructure.from_json()`, loads profile, calls `compile_show()`, writes `ShowTimeline.to_json()`.
     - `--seed` for deterministic output (testing/debugging).
     - `--summary` prints human-readable summary (section count, cue list, total duration).
   - `compile-and-play` subcommand (convenience):
     - `dreamsync compile-and-play song.mp3 --profile midnight_rave --config devices.yaml`
     - Runs `analyze_song()` → `compile_show()` → `run_show_playback()` in sequence.
     - Prints progress: "Analyzing... Compiling... Playing..."

3. Wire into `compiler/__init__.py`:
   - Re-export `compile_show` for clean imports: `from dreamsync.compiler import compile_show`.

### Done When

- [ ] `compile_show(structure)` produces a valid `ShowTimeline` with no profile (all defaults)
- [ ] `compile_show(structure, profile)` respects profile overrides
- [ ] CLI `dreamsync compile structure.json --output show.json` works end-to-end
- [ ] CLI `dreamsync compile-and-play song.mp3 --profile default --config devices.yaml` runs the full pipeline
- [ ] `--seed` produces identical output on repeated runs
- [ ] `--summary` prints a useful human-readable overview
- [ ] Compilation completes in < 1 second (no audio processing — just data mapping)
- [ ] 10 unit tests covering: full pipeline, no-profile default, profile override, seed determinism, CLI argument parsing, compile-and-play wiring, error handling (missing structure file, invalid profile)

---

## End-to-End Validation

The Show Compiler is considered complete when:

| # | Criterion | Verified By |
|---|-----------|-------------|
| 1 | `SongStructure` → `ShowTimeline` with built-in defaults | `compile_show(structure)` unit test |
| 2 | `SongStructure` → `ShowTimeline` with custom profile | `compile_show(structure, profile)` unit test |
| 3 | Every section in the structure has a corresponding cue in the timeline | Assembly validation |
| 4 | Narrative arc creates visible energy progression (not flat) | Arc planner unit tests + manual review |
| 5 | Treatments are non-repetitive for consecutive same-mood sections | Treatment selector unit tests |
| 6 | Transitions are musically appropriate (drops = cut, outros = long fade) | Transition planner unit tests |
| 7 | Output `ShowTimeline` is playable by Component 5 without modification | `run_show_playback()` with compiler output |
| 8 | CLI `compile` + `compile-and-play` work end-to-end | CLI integration tests |
| 9 | 5+ songs across genres compiled and reviewed for quality | Manual validation |
| 10 | Full test suite passes (55 tests) | `pytest dev/tests/test_compiler_*.py` |

---

## Tests

All tests in `dev/tests/test_compiler_*.py`. Target: **55 tests** across 5 files.

| File | Area | Count |
|------|------|-------|
| `dev/tests/test_compiler_arc.py` | NarrativeArcPlanner: arc shape, repeated sections, caps, boost, dip, edge cases | 12 |
| `dev/tests/test_compiler_treatments.py` | TreatmentSelector: mood mapping, profile overrides, repeat avoidance, drop, speed, params | 15 |
| `dev/tests/test_compiler_transitions.py` | TransitionPlanner: per-label rules, defaults, caps, edge cases | 10 |
| `dev/tests/test_compiler_assemble.py` | TimelineAssembler: assembly, validation, round-trip, error handling | 8 |
| `dev/tests/test_compiler_compile.py` | compile_show orchestrator + CLI: full pipeline, profiles, seed, CLI args | 10 |

### Test Strategy

- **Arc tests**: Construct `Section` tuples with known labels and energy values. Verify `plan()` output arc weights match expected intensity curves. Test patterns: verse-chorus-verse-chorus, intro-only, single section, all-chorus.
- **Treatment tests**: Mock or construct `ProfileConfig` with known palettes/effects. Verify treatment selection respects pools, weights, and fallbacks. Use fixed seed for deterministic assertions. Verify repeat avoidance with two consecutive calls for the same mood.
- **Transition tests**: Construct section pairs covering all label combinations in the rules table. Verify correct transition type and beat count. Test boundary cases: single section, fade beat cap, insufficient remaining beats.
- **Assembly tests**: Construct all intermediate outputs (arc weights, treatments, transition plans) manually. Verify the assembled `ShowTimeline` has correct field mapping. Test JSON round-trip.
- **Orchestrator tests**: Use a real `SongStructure` (constructed in-memory, no audio) and verify `compile_show()` produces a valid timeline. Test with and without profile. Test CLI argument parsing with `build_parser().parse_args()`.

---

## Parameters

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| `arc.intro_intensity` | 0.15 | Intros should be subtle — just enough to signal the show has started |
| `arc.outro_intensity` | 0.10 | Outros wind down; near-off but not completely dark |
| `arc.peak_boost` | 1.2 | Final chorus gets 20% extra intensity above its natural energy |
| `arc.buildup_ramp` | 0.15 | Each repeated section type gets +15% intensity over the previous occurrence |
| `arc.bridge_reduction` | 0.80 | Bridges get 80% of their raw energy (20% dip to create contrast) |
| `transitions.default_fade_beats` | 4 | 4-beat crossfade ≈ 1 bar at 4/4, a natural transition length |
| `transitions.max_fade_beats` | 16 | Cap at 4 bars — longer fades feel sluggish |
| `treatment.speed_breakpoints` | (90, 120, 140) | BPM ranges mapping to speed tiers: slow, medium, fast, very fast |
| `treatment.speed_values` | (0.3, 0.5, 0.7, 0.9) | Speed multiplier per BPM tier |
| `treatment.drop_speed` | 1.0 | DROP mood always runs at full speed |

---

## Build Order

Steps are sequential within each deliverable. D3.1 can be built first (needs only `Section`). D3.2 is independent of D3.1 (needs `Section` + `ProfileConfig`). D3.3 is independent of D3.1/D3.2 (needs `Section` + `BeatGrid`). D3.4 depends on all three. D3.5 wires everything.

| Phase | Step | Deliverable | Files Created/Modified |
|-------|------|-------------|----------------------|
| 1 | Create `compiler/` package | Setup | `src/dreamsync/compiler/__init__.py` |
| 2 | Implement `NarrativeArcPlanner` | D3.1 | `src/dreamsync/compiler/arc.py` |
| 3 | Write arc tests | Tests | `dev/tests/test_compiler_arc.py` |
| 4 | Implement `TreatmentSelector` | D3.2 | `src/dreamsync/compiler/treatments.py` |
| 5 | Write treatment tests | Tests | `dev/tests/test_compiler_treatments.py` |
| 6 | Implement `TransitionPlanner` | D3.3 | `src/dreamsync/compiler/transitions.py` |
| 7 | Write transition tests | Tests | `dev/tests/test_compiler_transitions.py` |
| 8 | Implement `TimelineAssembler` | D3.4 | `src/dreamsync/compiler/assemble.py` |
| 9 | Write assembly tests | Tests | `dev/tests/test_compiler_assemble.py` |
| 10 | Implement `compile_show()` orchestrator | D3.5 | `src/dreamsync/compiler/compile.py` |
| 11 | Wire into CLI | Integration | `src/dreamsync/cli.py` |
| 12 | Write orchestrator + CLI tests | Tests | `dev/tests/test_compiler_compile.py` |
| 13 | Manual validation: compile 5+ songs, review show quality | Validation | — |

Note: Phases 2–3, 4–5, and 6–7 are independent of each other and can be built in parallel.

---

## Non-Goals

- **Sub-section cues**: The compiler produces one cue per section. It does not sub-divide sections into per-bar or per-beat cues. Within a section, the renderer handles frame-level animation (pulse decay, scroll motion, breathe sine wave) — the compiler only sets the macro parameters. Future enhancement: optional per-bar "accent cues" for beat drops within a section.
- **Mood override / manual editing**: The compiler's output is fully automatic. There is no GUI or manual cue editor. The `ShowTimeline` JSON is human-readable and could be hand-edited, but that's outside scope.
- **Genre-specific profiles**: The compiler is genre-agnostic. Genre-specific behavior comes from profiles (which are already extensible via YAML). The compiler applies whatever profile is loaded.
- **Multi-device choreography**: All devices receive the same `LightingIntent` per tick (same as v2). Per-device spatial choreography (e.g., "left strip does verse, right strip does chorus") is not in scope.
- **Audio-reactive overlay**: The compiler produces a static timeline. There is no hybrid mode where live audio reactivity adjusts the compiled show in real time. The v2 fallback (Feature 6) is a full switch, not a blend.

---

## Dependencies

| Dependency | Type | Notes |
|------------|------|-------|
| `SongStructure` | Existing code | `src/dreamsync/analyzer/models.py` (Feature 2) |
| `Section`, `BeatGrid`, `TempoRegion` | Existing code | `src/dreamsync/analyzer/sections.py`, `bpm.py` |
| `ShowTimeline`, `ShowCue` | Existing code | `src/dreamsync/show/models.py` (Feature 2, C5) |
| `ProfileConfig`, `MoodProfileConfig`, `MoodEffectEntry`, `TransitionRule` | Existing code | `src/dreamsync/profile.py` |
| `EFFECTS`, `EffectPreset` | Existing code | `src/dreamsync/effects.py` |
| `PALETTES`, `MOOD_PALETTES`, `MOOD_EFFECTS` | Existing code | `src/dreamsync/effects.py` |
| `Mood` | Existing code | `src/dreamsync/mood.py` |
| `load_profile` | Existing code | `src/dreamsync/profile.py` |
| `random` | Stdlib | Weighted effect/palette selection |
| `json` | Stdlib | SongStructure/ShowTimeline serialization (via existing models) |

No new pip dependencies required. The compiler is pure data transformation — no DSP, no audio, no I/O beyond JSON file reads/writes.
