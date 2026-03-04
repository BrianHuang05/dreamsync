# Feature 3, Component 1 — Narrative Arc Planner

**Status**: DONE

---

## Overview

The Narrative Arc Planner assigns an intensity multiplier (0.0–1.0) to each section of a song based on its position in the song's narrative arc. It takes the `sections` tuple from a `SongStructure` and produces a list of `ArcWeight`s — one per section — that scale each section's raw `energy_mean` into a final intensity value. The arc planner does not change moods or labels; it only controls how bright / intense each section feels relative to the rest of the song. Energy builds across verse → pre-chorus → chorus, resets at bridge/breakdown, and peaks at the final chorus/drop.

---

## Architecture

```
┌──────────────────────────────────────────────┐
│              SongStructure.sections           │
│  tuple[Section, ...] — ordered by start_t     │
│  Each Section has: label, energy_mean, mood,  │
│  bpm, section_id                              │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│           NarrativeArcPlanner                 │
│                                               │
│  1. Count label occurrences (verse, chorus…)  │
│  2. Find last chorus/drop (peak target)       │
│  3. Per-section:                              │
│     a. Compute positional buildup ramp        │
│     b. Apply label-specific rules:            │
│        - Intro/outro caps                     │
│        - Bridge/breakdown dip                 │
│        - Final chorus/drop peak boost         │
│     c. Compute arc_multiplier                 │
│     d. final_intensity = clamp(energy_mean    │
│                           * arc_multiplier)   │
│                                               │
│  Output: list[ArcWeight] — one per section    │
└──────────────────────────────────────────────┘
```

### File Layout

```
src/dreamsync/
├── compiler/
│   ├── __init__.py
│   └── arc.py              # ArcWeight, NarrativeArcPlanner
```

### Integration Points

- **SongStructure (Feature 2, Component 4)** — Input: `structure.sections` provides the section list with labels, energy values, moods, and structural IDs.
- **TimelineAssembler (Feature 3, Component 4)** — Output: `list[ArcWeight]` is consumed by the assembler to set `ShowCue.intensity`.
- **compile_show() (Feature 3, Component 5)** — The orchestrator creates a `NarrativeArcPlanner` and calls `plan()` as the first step of compilation.

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
        bridge_reduction: float = 0.80,  # bridges get 80% of their raw energy
    ) -> None: ...

    def plan(self, sections: tuple[Section, ...]) -> list[ArcWeight]:
        """Compute arc weights for each section."""
```

**Algorithm:**

1. **Base energy**: Start with each section's `energy_mean` as the raw intensity.
2. **Position ramp**: Sections later in the song get a slight boost. For sections with the same label (e.g. verse appearing 3 times), each occurrence gets `+buildup_ramp` more intensity than the previous.
3. **Intro / outro dampening**: Sections labelled `intro` are capped at `intro_intensity`. Sections labelled `outro` are capped at `outro_intensity`.
4. **Final chorus boost**: The last section labelled `chorus` (or `drop`) gets multiplied by `peak_boost`.
5. **Bridge reset**: Sections labelled `bridge` or `breakdown` get their raw energy reduced by `bridge_reduction` (multiply by 0.80) to create a deliberate energy valley before the next chorus.
6. **Clamp**: All final intensities are clamped to `[0.05, 1.0]`.

**Edge cases:**

| Case | Behavior |
|------|----------|
| Single section | `arc_multiplier = 1.0`, `final_intensity = clamp(energy_mean)` |
| All same label | Each occurrence still gets `+buildup_ramp` |
| No chorus or drop | No peak boost applied — highest-energy section is the natural peak |
| Very short song (< 3 sections) | Arc is essentially flat — minimal ramp applied |
| Zero energy_mean | Clamped to minimum 0.05 (never fully dark) |

### Implementation Steps

1. Create `src/dreamsync/compiler/__init__.py` (empty, or re-export `compile_show` — placeholder for now).
2. Create `src/dreamsync/compiler/arc.py`:
   - Import `Section` from `dreamsync.analyzer.sections`.
   - `ArcWeight` frozen dataclass with `section_index`, `base_energy`, `arc_multiplier`, `final_intensity`.
   - `NarrativeArcPlanner`:
     - `__init__(intro_intensity, outro_intensity, peak_boost, buildup_ramp, bridge_reduction)` — store all config parameters as instance attributes.
     - `plan(sections)`:
       1. Handle empty sections: return `[]`.
       2. Build a label occurrence counter: `label_counts: dict[str, int] = {}` — tracks how many times each label has been seen so far.
       3. Scan all sections to identify the index of the **last** section labelled `"chorus"` or `"drop"`. Store as `peak_index`. If none found, set `peak_index = -1`.
       4. Iterate sections in order (`for i, section in enumerate(sections)`):
          a. Record the current occurrence number for this label: `occurrence = label_counts.get(section.label, 0)`.
          b. Start with `arc_multiplier = 1.0`.
          c. **Buildup ramp**: `arc_multiplier += occurrence * self._buildup_ramp`. This makes verse 2 louder than verse 1, chorus 2 louder than chorus 1, etc.
          d. **Intro cap**: If `section.label == "intro"`, set `arc_multiplier = min(arc_multiplier, self._intro_intensity / max(section.energy_mean, 0.01))`. This caps the final intensity at `intro_intensity` regardless of energy_mean. If energy_mean is very small, the multiplier stays at 1.0 (clamped later).
          e. **Outro cap**: If `section.label == "outro"`, apply same logic with `outro_intensity`.
          f. **Bridge / breakdown dip**: If `section.label in ("bridge", "breakdown")`, multiply: `arc_multiplier *= self._bridge_reduction`.
          g. **Final chorus/drop boost**: If `i == peak_index`, multiply: `arc_multiplier *= self._peak_boost`.
          h. Compute `final_intensity = section.energy_mean * arc_multiplier`.
          i. Clamp: `final_intensity = max(0.05, min(1.0, final_intensity))`.
          j. Append `ArcWeight(section_index=i, base_energy=section.energy_mean, arc_multiplier=round(arc_multiplier, 4), final_intensity=round(final_intensity, 4))`.
          k. Increment `label_counts[section.label] = occurrence + 1`.
       5. Return the list of `ArcWeight`s.

### Done When

- [ ] Each section gets an `ArcWeight` with a valid `final_intensity` in [0.05, 1.0]
- [ ] Repeated sections (verse 1, verse 2, verse 3) have progressively increasing intensity
- [ ] Intro sections are capped at low intensity regardless of energy_mean
- [ ] Outro sections are capped at low intensity
- [ ] The final chorus/drop has the highest intensity in the song (when energy_mean is comparable)
- [ ] Bridge/breakdown sections create a visible energy dip (80% of raw energy)
- [ ] A single-section song (no structure) gets a flat arc with intensity = energy_mean (clamped)
- [ ] All-same-label song (e.g. all "verse") still ramps progressively
- [ ] Empty sections list returns empty list
- [ ] Very high energy_mean values are clamped to 1.0
- [ ] Very low energy_mean values are floored at 0.05
- [ ] 12 unit tests passing

---

## Tests

All tests in `dev/tests/test_compiler_arc.py`. Target: **12 tests**.

| # | Test Name | Description |
|---|-----------|-------------|
| 1 | `test_basic_arc_shape` | Standard verse-chorus-verse-chorus song: verify increasing intensity across repeated sections |
| 2 | `test_intro_cap` | Intro section with high energy_mean is capped at intro_intensity |
| 3 | `test_outro_cap` | Outro section with high energy_mean is capped at outro_intensity |
| 4 | `test_final_chorus_boost` | Last chorus gets peak_boost applied, is the highest intensity |
| 5 | `test_final_drop_boost` | Last drop (not chorus) gets peak_boost — drop label also triggers boost |
| 6 | `test_bridge_dip` | Bridge section intensity = energy_mean * bridge_reduction |
| 7 | `test_breakdown_dip` | Breakdown label also gets the same reduction as bridge |
| 8 | `test_repeated_sections_ramp` | Three verses: verse 3 intensity > verse 2 > verse 1 |
| 9 | `test_single_section` | Song with one section: arc_multiplier = 1.0, final_intensity = clamp(energy_mean) |
| 10 | `test_all_same_label` | Song where every section is "verse": each gets progressively higher intensity |
| 11 | `test_no_chorus_no_drop` | Song with no chorus or drop section: no peak boost applied, but intensities are still valid |
| 12 | `test_clamp_bounds` | Verify final_intensity is always in [0.05, 1.0]: test with energy_mean=0.0 and energy_mean=2.0 |

### Test Strategy

- Construct `Section` tuples with known labels and energy values. No audio processing needed — pure data transformation testing.
- Verify `plan()` output arc weights match expected intensity curves by checking relative ordering (verse 2 > verse 1) and absolute bounds ([0.05, 1.0]).
- Use approximate floating-point comparisons (`pytest.approx`) for intensity values.
- Create helper function `make_section(label, energy_mean, ...)` to reduce boilerplate in test fixtures.

---

## Parameters

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| `intro_intensity` | 0.15 | Intros should be subtle — just enough to signal the show has started |
| `outro_intensity` | 0.10 | Outros wind down; near-off but not completely dark |
| `peak_boost` | 1.2 | Final chorus gets 20% extra intensity above its natural energy |
| `buildup_ramp` | 0.15 | Each repeated section type gets +15% intensity over the previous occurrence |
| `bridge_reduction` | 0.80 | Bridges get 80% of their raw energy (20% dip to create contrast before the next high-energy section) |
| `min_intensity` | 0.05 | Floor — never fully dark, even for silent sections |
| `max_intensity` | 1.0 | Ceiling — hardware max brightness |

---

## Build Order

| Phase | Step | Files Created/Modified |
|-------|------|----------------------|
| 1 | Create `compiler/` package | `src/dreamsync/compiler/__init__.py` |
| 2 | Implement `ArcWeight` and `NarrativeArcPlanner` | `src/dreamsync/compiler/arc.py` |
| 3 | Write unit tests | `dev/tests/test_compiler_arc.py` |

Note: This component has no dependencies on other Feature 3 components. It can be built in parallel with D3.2 (TreatmentSelector) and D3.3 (TransitionPlanner).

---

## Non-Goals

- **Dynamic arc adjustment**: The arc is computed once from the static `SongStructure`. There is no runtime feedback loop where the arc adjusts based on audience reaction or live audio.
- **Sub-section intensity curves**: The planner produces one intensity per section. It does not create per-bar or per-beat intensity ramps within a section. Within-section dynamics (pulse decay, breathe sine wave) are handled by the renderer at playback time.
- **Genre-specific arc shapes**: The algorithm is genre-agnostic. A hip-hop track and an EDM track use the same rules. Genre-specific behavior comes from profiles (D3.2) and the section labels produced by the Analyzer.
- **User-defined arc curves**: There is no UI or config for manually drawing an intensity curve. The arc is fully automatic.

---

## Dependencies

| Dependency | Type | Notes |
|------------|------|-------|
| `Section` | Existing code | `src/dreamsync/analyzer/sections.py` — `start_t`, `end_t`, `label`, `energy_mean`, `mood`, `bpm`, `section_id` |
| `dataclasses` | Stdlib | Frozen dataclass for `ArcWeight` |

No new pip dependencies required. This component is pure data transformation — no DSP, no audio, no I/O.
