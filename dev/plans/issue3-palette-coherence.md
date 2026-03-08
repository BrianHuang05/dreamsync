# Plan: Issue 3 — Color Palette Coherence ("Rainbow" Problem)

Priority: P1 | Effort: Low-Medium | Affects: visual identity per song is lost

---

## Deliverable 1: Song-Level Palette Selection

### Prerequisites
- Read `src/dreamsync/compiler/treatments.py` — `TreatmentSelector.select()`, `_resolve_palette_pool()`, palette re-roll logic
- Read `src/dreamsync/effects.py` — `PALETTES`, `MOOD_PALETTES`
- Read `src/dreamsync/compiler/assemble.py` — `TimelineAssembler` (where treatments are consumed)

### Problem
`TreatmentSelector.select()` is called once per section. Each call independently picks a palette from the mood's pool (`MOOD_PALETTES`). The only constraint is a single re-roll to avoid the same palette as the *immediately previous* section. With 4-6 sections and 3-4 palettes per mood pool, the result is 3-4 different palettes across the song. Visually, this creates a "rainbow" effect — warm oranges in verse 1, cool blues in verse 2, neon pinks in the chorus, fire reds in the bridge.

The palette pools overlap across moods (e.g., "vivid" appears in GROOVE, HYPE, and DROP), so mood changes can accidentally pick the same palette, or dramatically different ones, with no coherent identity.

### Solution

1. **Add a `select_song_palette()` method** that picks 1-2 palettes for the entire song before any per-section selection:

   ```python
   # src/dreamsync/compiler/treatments.py

   class TreatmentSelector:
       def __init__(self, ...):
           ...
           self._song_primary_palette: str | None = None
           self._song_accent_palette: str | None = None

       def select_song_palettes(self, dominant_mood: str, all_moods: list[str]) -> None:
           """Pick 1-2 palettes for the entire song. Call once before per-section select()."""
           mood_enum = Mood(dominant_mood)
           pool = self._resolve_palette_pool(mood_enum)

           # Primary palette: random from dominant mood
           self._song_primary_palette = self._rng.choice(pool)

           # Accent palette: pick from a different mood if the song has mood variety
           unique_moods = set(all_moods) - {dominant_mood}
           if unique_moods:
               accent_mood = self._rng.choice(list(unique_moods))
               accent_pool = self._resolve_palette_pool(Mood(accent_mood))
               # Pick one that's different from primary
               candidates = [p for p in accent_pool if p != self._song_primary_palette]
               self._song_accent_palette = self._rng.choice(candidates) if candidates else accent_pool[0]
           else:
               # Mono-mood song: pick a second palette from same pool
               candidates = [p for p in pool if p != self._song_primary_palette]
               self._song_accent_palette = self._rng.choice(candidates) if candidates else self._song_primary_palette
   ```

2. **Modify `select()` to prefer song palettes**:
   ```python
   def select(self, mood, section_label, section_bpm) -> Treatment:
       ...
       # 2. Palette selection — use song palette if set
       if self._song_primary_palette is not None:
           # Primary for main sections, accent for bridges/breakdowns
           if section_label in ("bridge", "breakdown", "outro"):
               palette_name = self._song_accent_palette or self._song_primary_palette
           else:
               palette_name = self._song_primary_palette
       else:
           # Fallback to existing per-section random selection
           palette_pool = self._resolve_palette_pool(mood_enum)
           palette_name = self._rng.choice(palette_pool)
           ...
       ...
   ```

3. **Call `select_song_palettes()` in the compiler** before the per-section loop:
   ```python
   # In the compile orchestrator
   moods = [s.mood for s in sections]
   dominant_mood = max(set(moods), key=moods.count)
   treatment_selector.select_song_palettes(dominant_mood, moods)
   ```

### Files to Modify
- `src/dreamsync/compiler/treatments.py` — add `select_song_palettes()`, modify `select()`, add `reset()` cleanup
- Compiler orchestrator (wherever `TreatmentSelector.select()` is called in a loop)

### Unit Tests
- `python -m pytest dev/tests/test_compiler_treatments.py -v`
- New tests:
  - `test_song_palette_limits_to_two` — call select_song_palettes(), then select() 8 times → at most 2 unique palette names across all treatments
  - `test_song_palette_accent_differs_from_primary` — song_primary != song_accent (when pool size > 1)
  - `test_song_palette_bridge_gets_accent` — section_label="bridge" → uses accent palette
  - `test_song_palette_verse_chorus_get_primary` — section_label="verse"/"chorus" → uses primary palette
  - `test_no_song_palette_fallback` — don't call select_song_palettes() → existing random behavior preserved
  - `test_reset_clears_song_palettes` — call reset() → song palettes are None → next select() uses random

### Completion Criteria
- A compiled show uses at most 2 palette names across all cues
- Bridge/breakdown sections use the accent palette for visual contrast
- Existing behavior preserved when `select_song_palettes()` is not called (backward compat)

---

## Deliverable 2: Reduce Color Cycling Rate (Downbeat-Only)

### Prerequisites
- Read `src/dreamsync/show/runtime.py` — `ShowPlaybackRuntime.tick()`, `self._color_index` increment on beat
- Read `src/dreamsync/show/models.py` — `ShowTimeline.is_beat()`, `ShowTimeline.is_downbeat()`

### Problem
`ShowPlaybackRuntime.tick()` advances `_color_index` on **every beat**. At 120 BPM, that's a color change every 500ms — 2 colors per second. With a 6-color palette, the full cycle takes 3 seconds, and the eye perceives it as rapid random flashing rather than a coherent color scheme.

The `ShowTimeline` already exposes `is_downbeat()` (every 4 beats in 4/4), which would give color changes every 2 seconds at 120 BPM — much more natural.

### Solution

1. **Change color cycling from beat to downbeat** in `tick()`:
   ```python
   def tick(self, t: float) -> bool:
       ...
       # Check beat and downbeat grids
       beat = self._timeline.is_beat(t, tolerance=self._beat_tolerance)
       downbeat = self._timeline.is_downbeat(t, tolerance=self._beat_tolerance)

       # Advance color on DOWNBEAT only (was: every beat)
       if downbeat and not self._beat_fired:
           self._color_index = (self._color_index + 1) % len(cue.color_palette)
           self._beat_fired = True
           self._beats_hit += 1
       elif not downbeat:
           self._beat_fired = False
       ...
   ```

2. **Add a `color_cycle_mode` parameter** to allow per-show control:
   ```python
   class ShowPlaybackRuntime:
       def __init__(self, timeline, multi_adapter, *, color_cycle_mode: str = "downbeat"):
           """color_cycle_mode: "beat" (every beat), "downbeat" (every bar), "phrase" (every N bars)"""
           ...
           self._color_cycle_mode = color_cycle_mode

       def tick(self, t):
           ...
           should_cycle = False
           if self._color_cycle_mode == "beat":
               should_cycle = beat and not self._beat_fired
           elif self._color_cycle_mode == "downbeat":
               should_cycle = downbeat and not self._beat_fired

           if should_cycle:
               self._color_index = (self._color_index + 1) % len(cue.color_palette)
               ...
   ```

3. **Keep the `beat` flag for effect rendering** — pulse/scroll still need beat-level triggers, just color cycling is slowed down.

### Files to Modify
- `src/dreamsync/show/runtime.py` — `tick()`, `__init__()`, add `color_cycle_mode` parameter

### Unit Tests
- `python -m pytest dev/tests/test_show_runtime.py -v`
- New tests:
  - `test_color_cycles_on_downbeat_not_beat` — tick through 8 beats in 4/4 → color_index advances twice (was 8 times)
  - `test_color_cycle_mode_beat_preserves_old_behavior` — construct with color_cycle_mode="beat" → color changes on every beat
  - `test_beat_flag_still_fires_for_effects` — even in downbeat mode, the `beat` flag is passed to `send_frame()` on every beat (effects still trigger on beats)
  - `test_color_cycle_mode_downbeat_default` — default construction → downbeat mode

### Completion Criteria
- Default color cycling is every 4 beats (1 bar) instead of every beat
- Pulse/scroll effects still trigger on every beat (only COLOR cycling is slowed)
- Old behavior available via `color_cycle_mode="beat"` for backward compat

---

## Deliverable 3: Palette Interpolation (Smooth Color Transitions)

### Prerequisites
- Deliverable 2 (color cycling already on downbeat cadence)
- Read `src/dreamsync/show/runtime.py` — `_build_intent()`, color selection via `_color_index`

### Problem
Even with downbeat-only cycling, the color change is instantaneous — one frame is orange, the next is blue. This hard cut between unrelated colors in the palette contributes to the "rainbow" perception, especially on bulbs where color is the primary visual element.

### Solution

1. **Add color interpolation** in `_build_intent()`:
   ```python
   class ShowPlaybackRuntime:
       def __init__(self, ...):
           ...
           self._prev_color_index: int = 0
           self._color_blend_start_t: float = 0.0
           self._color_blend_duration: float = 0.0  # set from BPM

       def _build_intent(self, cue, t) -> LightingIntent:
           ...
           # Interpolate between previous and current palette color
           blend_progress = 1.0  # default: fully at current color
           if self._color_blend_duration > 0:
               elapsed = t - self._color_blend_start_t
               blend_progress = min(1.0, elapsed / self._color_blend_duration)

           if blend_progress < 1.0 and len(cue.color_palette) > 1:
               prev_color = cue.color_palette[self._prev_color_index % len(cue.color_palette)]
               curr_color = cue.color_palette[self._color_index % len(cue.color_palette)]
               color = _interpolate_hex(prev_color, curr_color, blend_progress)
           else:
               color = cue.color_palette[self._color_index % len(cue.color_palette)]
           ...
   ```

2. **Set blend duration** when color index changes:
   ```python
   if should_cycle:
       self._prev_color_index = self._color_index
       self._color_index = (self._color_index + 1) % len(cue.color_palette)
       self._color_blend_start_t = t
       # Blend over 2 beats (half a bar at 4/4)
       self._color_blend_duration = 2 * (60.0 / self._timeline.bpm)
   ```

3. **Add `_interpolate_hex()` utility**:
   ```python
   def _interpolate_hex(a: str, b: str, t: float) -> str:
       """Linear RGB interpolation between two hex colors."""
       ra, ga, ba = _parse_hex(a)
       rb, gb, bb = _parse_hex(b)
       r = int(ra + (rb - ra) * t)
       g = int(ga + (gb - ga) * t)
       b_val = int(ba + (bb - ba) * t)
       return f"#{r:02x}{g:02x}{b_val:02x}"
   ```

### Files to Modify
- `src/dreamsync/show/runtime.py` — add blend state, modify `_build_intent()`, add color interpolation

### Unit Tests
- `python -m pytest dev/tests/test_show_runtime.py -v`
- New tests:
  - `test_color_interpolation_midway` — color change at t=10.0, query at t=10.5 (half of 1-second blend at 120bpm) → color is midpoint between old and new
  - `test_color_interpolation_complete` — query after blend_duration → fully at new color
  - `test_interpolate_hex_basic` — `_interpolate_hex("#ff0000", "#0000ff", 0.5)` → `"#800080"` (or close)
  - `test_no_interpolation_when_disabled` — blend_duration=0 → immediate color switch

### Completion Criteria
- Color transitions are smoothly interpolated over 2 beats
- No visual pop between palette colors
- Performance: `_interpolate_hex` adds negligible overhead to the 200Hz tick loop

---

## Summary

| # | Deliverable | Files | Effort |
|---|-------------|-------|--------|
| 1 | Song-level palette selection | `treatments.py`, compiler orchestrator | Medium |
| 2 | Downbeat-only color cycling | `runtime.py` | Low |
| 3 | Smooth color interpolation | `runtime.py` | Low |

Recommended order: 2 (quick win, biggest impact) → 1 (medium effort) → 3 (polish)
