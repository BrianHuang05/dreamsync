# Plan: Issue 6 — Show End Behavior (Fade-Out After Last Musical Beat)

Priority: P2 | Effort: Low | Affects: awkward lingering lights after music ends

---

## Deliverable 1: Detect Last Musical Beat and Insert Fade-Out Cue

### Prerequisites
- Read `src/dreamsync/compiler/assemble.py` — `TimelineAssembler.assemble()` (creates ShowCues from sections)
- Read `src/dreamsync/compiler/arc.py` — `NarrativeArcPlanner.plan()` (outro intensity capped at 0.10)
- Read `src/dreamsync/show/models.py` — `ShowCue`, `ShowTimeline` (cue structure and validation)
- Read `src/dreamsync/analyzer/bpm.py` — `BeatGrid.beat_times`
- Read `src/dreamsync/analyzer/features.py` — `FeatureRow.energy`

### Problem
The last `ShowCue` in the compiled timeline extends from the last section's start to the song's total duration. If the last section is "outro", the arc planner caps its intensity at 0.10, but the effect keeps playing (scrolling, pulsing, etc.) until the audio player reports EOF. Many songs have 5-15 seconds of silence or low-level reverb tail after the last musical beat, during which the lights awkwardly continue their pattern at low intensity instead of fading gracefully to black.

`ShowPlaybackRuntime` has no concept of "the music is over" — it blindly follows the timeline until audio EOF.

### Solution

1. **Detect the last energetic beat** during compilation:
   ```python
   # src/dreamsync/compiler/assemble.py

   def _find_last_musical_beat(
       self,
       beat_times: tuple[float, ...],
       features: list[FeatureRow],
       energy_threshold: float = 0.10,
   ) -> float | None:
       """Find the last beat time where surrounding energy exceeds threshold.

       Scans backward from the end of the beat grid to find the last beat
       that coincides with audible musical content.
       """
       if not beat_times or not features:
           return None

       # Build a quick energy lookup: for each beat, average energy in a ±0.5s window
       for beat_t in reversed(beat_times):
           nearby_energy = [
               f.energy for f in features
               if abs(f.t - beat_t) <= 0.5
           ]
           if nearby_energy and sum(nearby_energy) / len(nearby_energy) > energy_threshold:
               return beat_t

       return None
   ```

2. **Insert a fade-to-black cue** after the last musical beat:
   ```python
   def assemble(self, structure, arc_weights, treatments, transition_plans) -> ShowTimeline:
       ...
       # After building all section cues:

       # Find last musical beat
       last_beat = self._find_last_musical_beat(
           structure.beat_grid.beat_times,
           structure.features,  # need features passed through or stored
           energy_threshold=0.10,
       )

       if last_beat is not None:
           fade_start = last_beat
           fade_duration = min(4.0, structure.duration - last_beat)  # 4s or whatever remains

           if fade_duration > 0.5:  # only if there's meaningful time to fade
               # Insert a fade-to-black cue
               last_cue = cues[-1]
               fade_cue = ShowCue(
                   t=fade_start,
                   render_mode="solid",  # simple solid for clean fade
                   color_palette=last_cue.color_palette,  # keep last palette
                   intensity=0.0,  # target: black
                   speed=0.0,
                   params={},
                   transition="fade",
                   transition_beats=min(8, int(fade_duration * structure.bpm / 60)),
               )
               cues.append(fade_cue)

       # Sort cues by time
       cues.sort(key=lambda c: c.t)
       ...
   ```

3. **The runtime already handles fade transitions** via `_on_cue_change()` — when it encounters the fade-to-black cue, it will interpolate from the previous cue's intensity to 0.0 over the transition beats. No runtime changes needed.

### Files to Modify
- `src/dreamsync/compiler/assemble.py` — add `_find_last_musical_beat()`, insert fade-to-black cue in `assemble()`

### Unit Tests
- `python -m pytest dev/tests/test_compiler_assemble.py -v`
- New tests:
  - `test_fade_to_black_inserted_after_last_beat` — song with 10s of silence at end → fade cue inserted at last energetic beat
  - `test_fade_to_black_intensity_is_zero` — the fade cue has intensity=0.0
  - `test_fade_to_black_transition_is_fade` — the fade cue has transition="fade" with transition_beats > 0
  - `test_no_fade_cue_if_song_ends_abruptly` — song where last beat is within 0.5s of duration → no fade cue inserted
  - `test_fade_cue_sorted_correctly` — fade cue appears after all section cues in timeline order
  - `test_fade_duration_capped_at_4s` — 20 seconds of silence after last beat → fade_duration = 4.0, not 20.0

### Completion Criteria
- Songs with trailing silence get a smooth fade-to-black over 4 seconds (or remaining time)
- Songs that end abruptly (last beat near EOF) don't get an unnecessary micro-fade
- The fade uses the existing runtime fade mechanism (no new runtime code)
- All existing assembler tests pass

---

## Deliverable 2: Outro Section Forced Linear Fade

### Prerequisites
- Deliverable 1 (fade-to-black cue mechanism exists)
- Read `src/dreamsync/compiler/arc.py` — `NarrativeArcPlanner.plan()`, outro intensity cap

### Problem
Even with the fade-to-black cue, the outro section itself (before the silence) plays at a flat 0.10 intensity. A real outro should feel like the show is winding down — intensity should decrease linearly from the previous section's level to near-zero, not jump to a flat low value.

Currently the arc planner computes one `final_intensity` per section, applied as a constant. There's no within-section intensity envelope.

### Solution

1. **Add an `intensity_envelope` field to `ShowCue`**:
   ```python
   # src/dreamsync/show/models.py
   @dataclass(frozen=True)
   class ShowCue:
       ...
       intensity_start: float | None = None  # NEW: if set, intensity ramps from start to intensity over the cue duration
   ```

   When `intensity_start is not None`, the runtime linearly interpolates from `intensity_start` to `intensity` over the cue's duration.

2. **Set intensity_start for outro cues** in the assembler:
   ```python
   # In assemble(), when building the outro cue
   if sections[i].label == "outro" and i > 0:
       # Ramp from previous section's intensity down to the arc planner's outro value
       cue = ShowCue(
           ...
           intensity=arc_weights[i].final_intensity,  # 0.10 (from arc planner)
           intensity_start=arc_weights[i-1].final_intensity,  # previous section's intensity
           ...
       )
   ```

3. **Apply the ramp in the runtime**:
   ```python
   # src/dreamsync/show/runtime.py — _build_intent()
   def _build_intent(self, cue, t):
       intensity = cue.intensity

       # Apply linear ramp if intensity_start is set
       if cue.intensity_start is not None:
           # Calculate progress through this cue
           cue_duration = self._cue_duration(cue)
           if cue_duration > 0:
               progress = min(1.0, (t - cue.t) / cue_duration)
               intensity = cue.intensity_start + (cue.intensity - cue.intensity_start) * progress

       ...
   ```

4. **`_cue_duration()` helper**: find duration by looking at the next cue's start time or song duration:
   ```python
   def _cue_duration(self, cue: ShowCue) -> float:
       """Duration of this cue (time until next cue or song end)."""
       cue_times = [c.t for c in self._timeline.cues]
       idx = cue_times.index(cue.t)
       if idx + 1 < len(cue_times):
           return cue_times[idx + 1] - cue.t
       return self._timeline.duration - cue.t
   ```

### Files to Modify
- `src/dreamsync/show/models.py` — add `intensity_start` to `ShowCue` (default None), update `to_dict()` / `from_dict()`
- `src/dreamsync/show/runtime.py` — apply intensity ramp in `_build_intent()`, add `_cue_duration()`
- `src/dreamsync/compiler/assemble.py` — set `intensity_start` for outro cues

### Unit Tests
- `python -m pytest dev/tests/test_show_runtime.py dev/tests/test_show_models.py dev/tests/test_compiler_assemble.py -v`
- New tests:
  - `test_intensity_ramp_midway` — cue with intensity_start=0.8, intensity=0.1, query at 50% through cue → intensity≈0.45
  - `test_intensity_ramp_start` — query at cue.t → intensity = intensity_start
  - `test_intensity_ramp_end` — query at cue end → intensity = cue.intensity
  - `test_no_ramp_when_intensity_start_none` — cue without intensity_start → flat intensity (existing behavior)
  - `test_outro_cue_has_intensity_start` — assemble with outro section → outro cue has intensity_start set to previous section's intensity
  - `test_show_cue_roundtrip_with_intensity_start` — serialize/deserialize ShowCue with intensity_start

### Completion Criteria
- Outro sections ramp from previous section's intensity to the arc planner's outro value
- Non-outro sections are unaffected (intensity_start=None)
- Combined with Deliverable 1: outro ramps down, then fade-to-black kicks in for the tail silence
- ShowCue serialization preserves intensity_start field

---

## Summary

| # | Deliverable | Files | Effort |
|---|-------------|-------|--------|
| 1 | Fade-to-black after last musical beat | `assemble.py` | Low |
| 2 | Outro linear fade ramp | `models.py`, `runtime.py`, `assemble.py` | Low-Medium |

Recommended order: 1 (simple, self-contained) → 2 (builds on 1, adds intensity envelope)
