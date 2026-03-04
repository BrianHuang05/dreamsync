# Feature 3, Component 2 — Treatment Selector

**Status**: DONE

---

## Overview

The Treatment Selector maps each section's mood to a concrete lighting treatment: effect name, render mode, color palette, renderer parameters, and speed. It draws from the active profile's mood-to-effect/palette pools when available, falling back to built-in `MOOD_EFFECTS` / `MOOD_PALETTES` defaults. To avoid visual monotony when consecutive sections share a mood (e.g. verse → verse, both "groove"), the selector tracks recently-used effects and palettes and avoids re-picking them. Component 2 is a pure data-mapping step with no DSP or I/O — it translates the abstract mood label and BPM into renderer-ready parameters.

---

## Architecture

```
┌──────────────────────────────────────────────┐
│  Per-section inputs:                          │
│    mood: str         ("chill"|"groove"|…)      │
│    section_label: str ("verse"|"chorus"|…)     │
│    section_bpm: float                         │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│          TreatmentSelector                    │
│                                               │
│  ┌─────────────────────────────────────────┐  │
│  │  1. Resolve effect pool                 │  │
│  │     profile.moods[mood].effects         │  │
│  │     OR built-in MOOD_EFFECTS[Mood]      │  │
│  ├─────────────────────────────────────────┤  │
│  │  2. Weighted random pick (re-roll once  │  │
│  │     if same as previous for this mood)  │  │
│  ├─────────────────────────────────────────┤  │
│  │  3. Resolve palette pool                │  │
│  │     profile.moods[mood].palettes        │  │
│  │     OR built-in MOOD_PALETTES[Mood]     │  │
│  ├─────────────────────────────────────────┤  │
│  │  4. Pick palette (re-roll once on       │  │
│  │     repeat for this mood)               │  │
│  ├─────────────────────────────────────────┤  │
│  │  5. Resolve palette name → hex colors   │  │
│  │     profile.palettes first, then        │  │
│  │     built-in PALETTES                   │  │
│  ├─────────────────────────────────────────┤  │
│  │  6. Get render_mode from EffectPreset   │  │
│  ├─────────────────────────────────────────┤  │
│  │  7. Merge params: effect defaults ←     │  │
│  │     profile mood params (profile wins)  │  │
│  ├─────────────────────────────────────────┤  │
│  │  8. Compute speed from BPM              │  │
│  │     <90→0.3, 90-120→0.5, 120-140→0.7,  │  │
│  │     140+→0.9, DROP always→1.0           │  │
│  └─────────────────────────────────────────┘  │
│                                               │
│  Output: Treatment (per section)              │
└──────────────────────────────────────────────┘

Special cases:
  - DROP mood → always "drop_blast" effect
  - Profile TransitionRules → force palette on mood change
```

### File Layout

```
src/dreamsync/
├── compiler/
│   ├── __init__.py
│   └── treatments.py       # Treatment, TreatmentSelector
```

### Integration Points

- **ProfileConfig (profile.py)** — Provides per-mood effect pools (`MoodProfileConfig.effects`), palette pools (`MoodProfileConfig.palettes`), param overrides (`MoodProfileConfig.params`), transition rules (`TransitionRule`), and custom palette definitions (`ProfileConfig.palettes`).
- **Effects system (effects.py)** — Built-in `MOOD_EFFECTS`, `MOOD_PALETTES`, `PALETTES`, `EFFECTS` provide defaults when no profile is loaded or when a profile doesn't override a mood.
- **EffectPreset (effects.py)** — Each named effect maps to a `render_mode` and default `params` dict.
- **Mood enum (mood.py)** — Used for pool lookups. String mood values from sections are converted to `Mood` enum.
- **TimelineAssembler (Feature 3, Component 4)** — Consumes the `list[Treatment]` output.
- **compile_show() (Feature 3, Component 5)** — The orchestrator creates a `TreatmentSelector` and calls `select()` per section.

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

**Selection logic (detailed):**

1. **Effect pool resolution**:
   - Convert mood string to `Mood` enum: `mood_enum = Mood(mood)`.
   - Check `profile.moods[mood].effects` if profile is not None and the mood key exists and effects tuple is non-empty.
   - If found, build pool as `[(entry.name, entry.weight) for entry in mood_cfg.effects]`.
   - Otherwise, use `MOOD_EFFECTS[mood_enum]` (list of `(name, weight)` tuples).

2. **Weighted random selection with repeat avoidance**:
   - Use `self._rng.choices(names, weights=weights, k=1)[0]` to pick an effect.
   - If the picked effect matches `self._last_effect[mood]` AND the pool has more than 1 entry, re-roll once.
   - If still a repeat after re-roll, accept it (avoids infinite loops with single-entry pools).

3. **DROP override**: If `mood == "drop"`, skip the pool entirely and force `effect_name = "drop_blast"`. No re-roll needed.

4. **Palette pool resolution**:
   - Check `profile.moods[mood].palettes` if profile is not None and the mood key exists and palettes tuple is non-empty.
   - If found, use that tuple of palette names.
   - Otherwise, use `MOOD_PALETTES[mood_enum]`.

5. **Palette selection with repeat avoidance**:
   - `self._rng.choice(palette_pool)` to pick a palette name.
   - If matches `self._last_palette[mood]` AND pool has > 1 entry, re-roll once.

6. **Transition rule check**: If profile defines `TransitionRule`s, and the previous mood differs from the current mood, check for a matching rule. If found, override the palette name with the rule's forced palette.

7. **Resolve palette name → hex colors**:
   - Look up in `profile.palettes[palette_name]` first.
   - Fall back to `PALETTES[palette_name]`.
   - Result is a `tuple[str, ...]` of hex color strings.

8. **Render mode**: Look up `EFFECTS[effect_name].render_mode.value` to get the string render mode.

9. **Params merge**:
   - Start with `dict(EFFECTS[effect_name].params)` (copy of effect defaults).
   - If profile is not None and `profile.moods[mood].params` is non-empty, update: `params.update(mood_cfg.params)`. Profile params win on conflict.

10. **Speed from BPM**:
    - `mood == "drop"` → `speed = 1.0` (always full speed).
    - `bpm < 90` → `speed = 0.3`.
    - `90 <= bpm < 120` → `speed = 0.5`.
    - `120 <= bpm < 140` → `speed = 0.7`.
    - `bpm >= 140` → `speed = 0.9`.

11. **Update history**: `self._last_effect[mood] = effect_name`, `self._last_palette[mood] = palette_name`.

12. **Track previous mood** for transition rule checks: `self._prev_mood = mood`.

### Implementation Steps

1. Create `src/dreamsync/compiler/treatments.py`:
   - Import `Mood` from `dreamsync.mood`.
   - Import `EFFECTS`, `PALETTES`, `MOOD_EFFECTS`, `MOOD_PALETTES` from `dreamsync.effects`.
   - Import `ProfileConfig`, `MoodProfileConfig`, `TransitionRule` from `dreamsync.profile`.
   - Import `random` from stdlib.
   - `Treatment` frozen dataclass with `render_mode`, `color_palette`, `params`, `speed`, `effect_name`.
   - `TreatmentSelector`:
     - `__init__(profile, seed)`:
       1. Store `self._profile = profile` (may be `None`).
       2. Create `self._rng = random.Random(seed)`.
       3. Initialize `self._last_effect: dict[str, str] = {}` — per-mood last-used effect name.
       4. Initialize `self._last_palette: dict[str, str] = {}` — per-mood last-used palette name.
       5. Initialize `self._prev_mood: str | None = None` — for transition rule checks.
     - `select(mood, section_label, section_bpm)`:
       1. Resolve effect pool (profile or built-in).
       2. If `mood == "drop"`, force `effect_name = "drop_blast"`, skip pool selection.
       3. Otherwise, weighted random pick with one re-roll on repeat.
       4. Resolve palette pool (profile or built-in).
       5. Pick palette with one re-roll on repeat.
       6. Check transition rules if `self._prev_mood` differs from `mood`.
       7. Resolve palette name to hex colors.
       8. Get `render_mode` from `EFFECTS[effect_name].render_mode.value`.
       9. Merge params: `dict(EFFECTS[effect_name].params)` + profile mood params.
       10. Compute speed from BPM (with DROP override).
       11. Update `_last_effect[mood]`, `_last_palette[mood]`, `_prev_mood`.
       12. Return `Treatment(render_mode, color_palette, params, speed, effect_name)`.
     - `reset()` — clear `_last_effect`, `_last_palette`, `_prev_mood = None`.
     - Private helpers:
       - `_resolve_effect_pool(mood_enum)` — returns `list[tuple[str, float]]`.
       - `_resolve_palette_pool(mood_enum)` — returns `tuple[str, ...]` of palette names.
       - `_resolve_palette_colors(palette_name)` — returns `tuple[str, ...]` of hex colors.
       - `_compute_speed(mood, bpm)` — returns `float`.

### Done When

- [ ] Every valid mood ("chill", "groove", "hype", "drop") produces a valid `Treatment`
- [ ] `Treatment.render_mode` is a valid `RenderMode` value string
- [ ] `Treatment.color_palette` is a non-empty tuple of hex color strings
- [ ] `Treatment.params` is a dict (may be empty but never None)
- [ ] `Treatment.speed` is in [0.3, 1.0]
- [ ] Treatment uses profile overrides when available, falls back to built-in defaults when not
- [ ] Consecutive same-mood sections get different effects (when pool size > 1)
- [ ] Consecutive same-mood sections get different palettes (when pool size > 1)
- [ ] DROP mood always selects `drop_blast` effect
- [ ] DROP mood always gets speed = 1.0 regardless of BPM
- [ ] Profile transition rules force the correct palette when `from_mood → to_mood` matches
- [ ] Speed multiplier scales with BPM correctly across all breakpoints
- [ ] Params merge order is correct: effect defaults < profile mood params (profile wins)
- [ ] Works with `profile=None` (all built-in defaults)
- [ ] Deterministic output with a fixed seed (for testing)
- [ ] `reset()` clears all history so the selector behaves as fresh
- [ ] 15 unit tests passing

---

## Tests

All tests in `dev/tests/test_compiler_treatments.py`. Target: **15 tests**.

| # | Test Name | Description |
|---|-----------|-------------|
| 1 | `test_chill_mood_defaults` | CHILL mood with no profile produces valid treatment from MOOD_EFFECTS/MOOD_PALETTES |
| 2 | `test_groove_mood_defaults` | GROOVE mood with no profile produces valid treatment |
| 3 | `test_hype_mood_defaults` | HYPE mood with no profile produces valid treatment |
| 4 | `test_drop_always_drop_blast` | DROP mood always selects "drop_blast" effect regardless of profile |
| 5 | `test_profile_effect_override` | Profile with custom effect pool for a mood: verify the selected effect comes from the profile pool |
| 6 | `test_profile_palette_override` | Profile with custom palette pool: verify selected palette comes from profile |
| 7 | `test_builtin_fallback` | Profile exists but doesn't define effects for one mood: verify fallback to MOOD_EFFECTS |
| 8 | `test_repeat_avoidance_effect` | Call select() twice with same mood, pool size > 1: second call should pick a different effect |
| 9 | `test_repeat_avoidance_palette` | Call select() twice with same mood, pool size > 1: second call should pick a different palette |
| 10 | `test_single_pool_entry_no_crash` | Mood with single-entry effect pool: re-roll should not crash or infinite loop |
| 11 | `test_transition_rule_palette` | Profile with TransitionRule from "chill" to "hype": verify forced palette on mood change |
| 12 | `test_speed_from_bpm` | Verify all BPM breakpoints: <90→0.3, 90→0.5, 120→0.7, 140→0.9 |
| 13 | `test_drop_speed_override` | DROP mood speed = 1.0 even when BPM < 90 |
| 14 | `test_params_merge` | Profile mood params override effect defaults: verify merged dict has profile values |
| 15 | `test_deterministic_seed` | Same seed produces identical treatment sequence across two independent selectors |

### Test Strategy

- Construct `ProfileConfig` objects in-memory with known palettes and effect pools. No YAML loading needed in tests.
- Use fixed seeds (`seed=42`) for deterministic assertions.
- For repeat avoidance tests: call `select()` twice for the same mood and verify the two treatments differ (effect name or palette name).
- For transition rule tests: call `select()` with mood "chill" first, then "hype", and verify the forced palette.
- Create a minimal `ProfileConfig` helper factory for tests (only the fields relevant to TreatmentSelector).

---

## Parameters

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| `speed_breakpoints` | (90, 120, 140) | BPM ranges mapping to speed tiers: slow, medium, fast, very fast |
| `speed_values` | (0.3, 0.5, 0.7, 0.9) | Speed multiplier per BPM tier — slow enough for chill, fast enough for hype |
| `drop_speed` | 1.0 | DROP mood always runs at full speed for maximum impact |
| `re_roll_attempts` | 1 | Number of re-rolls when a repeat is detected — just 1 to avoid bias |

---

## Build Order

| Phase | Step | Files Created/Modified |
|-------|------|----------------------|
| 1 | Implement `Treatment` and `TreatmentSelector` | `src/dreamsync/compiler/treatments.py` |
| 2 | Write unit tests | `dev/tests/test_compiler_treatments.py` |

Note: This component is independent of D3.1 (Arc Planner) and D3.3 (Transition Planner). All three can be built in parallel.

---

## Non-Goals

- **Effect blending**: The selector picks one discrete effect per section. There is no interpolation between effects within a section. Smooth transitions between effects are handled by the renderer's transition system (fade/cut).
- **Tempo-responsive effect switching**: The effect does not change mid-section if BPM changes. Each section gets one treatment for its entire duration.
- **Custom effect presets**: The selector uses only the built-in `EFFECTS` dict. There is no mechanism for profiles to define entirely new effect presets (only to weight existing ones differently).
- **Color harmony validation**: The selector does not check whether the chosen palette looks good. It trusts that the palettes defined in profiles and built-ins are already harmonious. `validate_color_harmony()` exists but is not called by the compiler.

---

## Dependencies

| Dependency | Type | Notes |
|------------|------|-------|
| `Mood` | Existing code | `src/dreamsync/mood.py` — enum for pool lookups |
| `EFFECTS`, `EffectPreset` | Existing code | `src/dreamsync/effects.py` — effect render_mode and default params |
| `PALETTES`, `MOOD_PALETTES`, `MOOD_EFFECTS` | Existing code | `src/dreamsync/effects.py` — built-in fallback pools |
| `ProfileConfig`, `MoodProfileConfig`, `MoodEffectEntry`, `TransitionRule` | Existing code | `src/dreamsync/profile.py` — profile structure |
| `random` | Stdlib | Weighted random selection |
| `dataclasses` | Stdlib | Frozen dataclass for `Treatment` |

No new pip dependencies required. This component is pure data mapping — no DSP, no audio, no I/O.
