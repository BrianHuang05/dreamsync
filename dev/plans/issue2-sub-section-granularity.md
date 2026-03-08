# Plan: Issue 2 — Sub-Section Granularity (Effect Granularity Too Coarse)

Priority: P1 | Effort: Medium | Affects: per-section monotony, missing structural detail

---

## Deliverable 1: Phrase Boundary Detection (4-Bar / 8-Bar Phrases)

### Prerequisites
- Read `src/dreamsync/analyzer/sections.py` — `SectionSegmenter`, `Section` dataclass
- Read `src/dreamsync/analyzer/bpm.py` — `BeatGrid` (downbeat_times, beat_times, time_signature)
- Read `src/dreamsync/analyzer/features.py` — `FeatureRow` (energy, bass_ratio, kick_spectral_flux)

### Problem
`SectionSegmenter` detects major structural boundaries (verse → chorus) using a self-similarity matrix with a checkerboard kernel and an 8-second minimum section length. This produces 4-8 sections for a typical 3.5-minute song. Within a 32-second verse, there's no structure — the compiler assigns one effect, one palette, one intensity for the entire duration. The listener hears obvious musical changes (kick enters, bass drops, new instrument layers) that the lightshow ignores.

### Solution

Add a `PhraseSegmenter` class that subdivides existing sections into 4-bar and 8-bar phrases using the beat grid and energy contour:

```python
# src/dreamsync/analyzer/phrases.py

@dataclass(frozen=True)
class Phrase:
    start_t: float
    end_t: float
    parent_section_index: int    # which Section this phrase belongs to
    phrase_type: str             # "steady" | "build" | "drop" | "breakdown"
    energy_delta: float          # change in energy from phrase start to end
    has_kick: bool               # whether kick onsets are present in this phrase

class PhraseSegmenter:
    def __init__(
        self,
        bars_per_phrase: int = 4,
        energy_change_threshold: float = 0.15,
        kick_presence_threshold: float = 0.3,
    ) -> None: ...

    def segment(
        self,
        sections: list[Section],
        features: list[FeatureRow],
        beat_grid: BeatGrid,
    ) -> list[Phrase]:
        """Subdivide each section into phrases at downbeat boundaries."""
        phrases = []
        for idx, section in enumerate(sections):
            # 1. Find all downbeats within this section
            section_downbeats = [
                d for d in beat_grid.downbeat_times
                if section.start_t <= d < section.end_t
            ]

            # 2. Group into N-bar phrases
            bars = beat_grid.time_signature  # 4 for 4/4
            phrase_len = self.bars_per_phrase  # default 4 bars = 4 downbeats

            for i in range(0, len(section_downbeats), phrase_len):
                phrase_start = section_downbeats[i]
                if i + phrase_len < len(section_downbeats):
                    phrase_end = section_downbeats[i + phrase_len]
                else:
                    phrase_end = section.end_t

                # 3. Analyze phrase features
                phrase_features = [f for f in features if phrase_start <= f.t < phrase_end]
                energy_delta = self._compute_energy_delta(phrase_features)
                has_kick = self._detect_kick_presence(phrase_features)
                phrase_type = self._classify_phrase(energy_delta, has_kick, phrase_features)

                phrases.append(Phrase(
                    start_t=phrase_start,
                    end_t=phrase_end,
                    parent_section_index=idx,
                    phrase_type=phrase_type,
                    energy_delta=energy_delta,
                    has_kick=has_kick,
                ))

        return phrases
```

The phrase types are classified as:
- **"steady"**: energy_delta < threshold, consistent kick presence
- **"build"**: energy_delta > +threshold (energy increasing)
- **"drop"**: energy_delta < -threshold (energy decreasing, but high overall)
- **"breakdown"**: low energy, no kick

### Files to Create
- `src/dreamsync/analyzer/phrases.py` — `PhraseSegmenter`, `Phrase`

### Files to Modify
- None in this deliverable (phrases are consumed in Deliverable 3)

### Unit Tests
- New test file: `dev/tests/test_analyzer_phrases.py`
- Tests:
  - `test_phrase_segmenter_subdivides_long_section` — 32-second section at 120 BPM → 4 phrases of ~8 seconds each
  - `test_phrase_segmenter_short_section_single_phrase` — 6-second section → 1 phrase (no split)
  - `test_phrase_type_build_detected` — features with rising energy → phrase_type="build"
  - `test_phrase_type_breakdown_no_kick` — features with low kick_spectral_flux → phrase_type="breakdown", has_kick=False
  - `test_phrase_boundaries_align_to_downbeats` — all phrase start/end times appear in beat_grid.downbeat_times
  - `test_phrase_parent_index_correct` — phrases reference the correct parent section

### Completion Criteria
- Sections longer than 4 bars are subdivided into 4-bar phrases
- Phrase boundaries align exactly to downbeat times from the beat grid
- Phrase types accurately reflect energy contour and kick presence

---

## Deliverable 2: Instrument-Aware Triggers (Kick Entrance / Bass Drop Detection)

### Prerequisites
- Deliverable 1 (`PhraseSegmenter` exists and provides `has_kick` per phrase)
- `FeatureRow` already contains `kick_spectral_flux` and `bass_ratio`

### Problem
The `SectionSegmenter` labels sections using aggregate energy thresholds. It can't distinguish "verse without drums" from "verse with drums entering" — both are labeled "verse" with similar average energy. Key musical moments (first kick hit, bass drop, drum fill leading into chorus) are invisible to the show compiler.

### Solution

Add instrument entrance detection as a post-processing step on phrase boundaries:

```python
# In src/dreamsync/analyzer/phrases.py

@dataclass(frozen=True)
class InstrumentEvent:
    t: float
    event_type: str   # "kick_enter" | "kick_exit" | "bass_enter" | "bass_drop"
    confidence: float

class InstrumentEventDetector:
    def __init__(
        self,
        kick_enter_threshold: float = 0.25,
        kick_exit_threshold: float = 0.10,
        bass_enter_ratio: float = 0.20,
        bass_drop_ratio: float = 0.45,
        min_sustain_seconds: float = 2.0,
    ) -> None: ...

    def detect(
        self,
        phrases: list[Phrase],
        features: list[FeatureRow],
    ) -> list[InstrumentEvent]:
        """Detect instrument entrance/exit events at phrase boundaries."""
        events = []
        for i in range(1, len(phrases)):
            prev = phrases[i - 1]
            curr = phrases[i]

            prev_features = [f for f in features if prev.start_t <= f.t < prev.end_t]
            curr_features = [f for f in features if curr.start_t <= f.t < curr.end_t]

            prev_kick_mean = mean([f.kick_spectral_flux for f in prev_features])
            curr_kick_mean = mean([f.kick_spectral_flux for f in curr_features])

            # Kick entrance: previous phrase had no kick, current does
            if prev_kick_mean < self.kick_exit_threshold and curr_kick_mean > self.kick_enter_threshold:
                events.append(InstrumentEvent(curr.start_t, "kick_enter", confidence=...))

            # Similar logic for kick_exit, bass_enter, bass_drop
            ...

        return events
```

### Files to Modify
- `src/dreamsync/analyzer/phrases.py` — add `InstrumentEventDetector`, `InstrumentEvent`

### Unit Tests
- Add to `dev/tests/test_analyzer_phrases.py`:
  - `test_kick_enter_detected_at_phrase_boundary` — phrase N has low kick_spectral_flux, phrase N+1 has high → kick_enter event at N+1.start_t
  - `test_bass_drop_detected` — bass_ratio jumps from 0.15 to 0.50 → bass_drop event
  - `test_no_false_events_in_steady_section` — constant energy, constant kick → no events
  - `test_kick_exit_detected` — kick present then absent → kick_exit event

### Completion Criteria
- Kick entrance/exit detected within 1 phrase of ground truth on synthetic signals
- Bass drop detection triggers on high bass_ratio transitions
- No false positives on steady-state sections

---

## Deliverable 3: Micro-Cue System (Compiler Integration)

### Prerequisites
- Deliverables 1 and 2 (`Phrase`, `InstrumentEvent` data available)
- Read `src/dreamsync/compiler/assemble.py` — `TimelineAssembler`
- Read `src/dreamsync/compiler/treatments.py` — `TreatmentSelector`
- Read `src/dreamsync/compiler/arc.py` — `NarrativeArcPlanner`, `ArcWeight`
- Read `src/dreamsync/show/models.py` — `ShowCue`, `ShowTimeline`

### Problem
`TimelineAssembler.assemble()` creates exactly one `ShowCue` per `Section`. There's no mechanism to insert sub-section cues. This means even with perfect phrase detection, the compiler can't express "switch from breathe to pulse when the kick enters at bar 9."

### Solution

1. **Extend `SongStructure`** to carry phrase and instrument event data:
   ```python
   # src/dreamsync/analyzer/models.py
   @dataclass(frozen=True)
   class SongStructure:
       ...
       phrases: tuple[Phrase, ...]               # NEW
       instrument_events: tuple[InstrumentEvent, ...]  # NEW
   ```

2. **Add phrase-level cue generation** to `TimelineAssembler`:
   ```python
   # src/dreamsync/compiler/assemble.py
   def assemble(self, structure, arc_weights, treatments, transition_plans) -> ShowTimeline:
       ...
       cues = []
       for i in range(n):
           # Main section cue (existing)
           cues.append(ShowCue(t=sections[i].start_t, ...))

           # Sub-cues for phrases within this section
           section_phrases = [p for p in structure.phrases if p.parent_section_index == i]
           section_events = [e for e in structure.instrument_events
                            if sections[i].start_t <= e.t < sections[i].end_t]

           for phrase in section_phrases[1:]:  # skip first (already covered by section cue)
               micro_cue = self._build_micro_cue(
                   phrase, treatments[i], arc_weights[i], section_events
               )
               if micro_cue is not None:
                   cues.append(micro_cue)

       # Sort by time
       cues.sort(key=lambda c: c.t)
       ...
   ```

3. **Micro-cue rules** (`_build_micro_cue()`):
   - **kick_enter**: boost intensity by +0.15, switch to pulse render mode
   - **build phrase**: ramp intensity linearly from base to base+0.20 over phrase duration
   - **breakdown phrase**: reduce intensity by -0.20, switch to breathe render mode
   - **steady phrase in long section**: shift to next palette color (prevents color staleness)

4. **Preserve backward compatibility**: if `structure.phrases` is empty (old analysis files), the assembler produces the same output as before (one cue per section).

### Files to Modify
- `src/dreamsync/analyzer/models.py` — add `phrases` and `instrument_events` fields to `SongStructure` (with defaults for backward compat)
- `src/dreamsync/compiler/assemble.py` — add `_build_micro_cue()`, modify `assemble()` loop
- `src/dreamsync/show/models.py` — no changes needed (ShowCue already supports arbitrary t values)

### Unit Tests
- `python -m pytest dev/tests/test_compiler_assemble.py -v`
- New tests:
  - `test_micro_cues_inserted_at_phrase_boundaries` — structure with 2 sections, 4 phrases each → 8+ cues in timeline (was 2)
  - `test_micro_cues_sorted_by_time` — verify cue.t values are monotonically increasing
  - `test_kick_enter_micro_cue_boosts_intensity` — phrase with kick_enter event → micro-cue intensity > section base
  - `test_backward_compat_no_phrases` — structure with empty phrases → same output as before (1 cue per section)
  - `test_micro_cue_inherits_section_palette` — micro-cues use the same palette as their parent section cue
  - `test_breakdown_micro_cue_reduces_intensity` — breakdown phrase → micro-cue intensity < section base

### Completion Criteria
- Songs with 4+ sections produce 12-20 cues (was 4-8)
- Instrument events generate visible effect changes at the correct time
- Backward compatibility: old SongStructure files without phrases field still compile correctly
- All existing assembler tests pass

---

## Deliverable 4: Wire Phrase Analysis into the Full Pipeline

### Prerequisites
- Deliverables 1, 2, 3 all implemented and tested

### Problem
The phrase segmenter and instrument detector exist but aren't called anywhere in the analysis pipeline. The `analyze` CLI command and the streaming pipeline need to invoke them and store results in `SongStructure`.

### Solution

1. **Update the analyzer orchestrator** (wherever `SongStructure` is constructed after feature extraction + BPM + sections):
   ```python
   from dreamsync.analyzer.phrases import PhraseSegmenter, InstrumentEventDetector

   phrase_segmenter = PhraseSegmenter()
   phrases = phrase_segmenter.segment(sections, features, beat_grid)

   event_detector = InstrumentEventDetector()
   instrument_events = event_detector.detect(phrases, features)

   structure = SongStructure(
       ...,
       phrases=tuple(phrases),
       instrument_events=tuple(instrument_events),
   )
   ```

2. **Update `SongStructure.to_dict()` / `from_dict()`** to serialize phrases and instrument events.

3. **Update `SongStructure.from_dict()`** to handle missing fields (backward compat with old JSON files):
   ```python
   phrases = tuple(Phrase(...) for p in data.get("phrases", []))
   instrument_events = tuple(InstrumentEvent(...) for e in data.get("instrument_events", []))
   ```

### Files to Modify
- `src/dreamsync/analyzer/models.py` — `to_dict()`, `from_dict()` for new fields
- Wherever `SongStructure` is constructed (likely in a `compile` or `analyze` orchestrator module)

### Unit Tests
- `python -m pytest dev/tests/test_analyzer_models.py -v`
- New tests:
  - `test_song_structure_roundtrip_with_phrases` — create SongStructure with phrases, serialize to JSON, deserialize, compare
  - `test_song_structure_from_dict_missing_phrases` — old JSON without phrases field → empty tuple (no crash)

### Completion Criteria
- `analyze` CLI command produces SongStructure with populated phrases
- JSON roundtrip preserves phrase and event data
- Old JSON files still load without errors

---

## Summary

| # | Deliverable | Files | Effort |
|---|-------------|-------|--------|
| 1 | Phrase boundary detection | `phrases.py` (new) | Medium |
| 2 | Instrument event detection | `phrases.py` | Medium |
| 3 | Micro-cue compiler integration | `assemble.py`, `models.py` | Medium |
| 4 | Pipeline wiring | `models.py`, orchestrator | Low |

Recommended order: 1 → 2 → 3 → 4 (strictly sequential — each builds on the previous)
