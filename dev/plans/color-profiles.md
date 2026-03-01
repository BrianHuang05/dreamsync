# Mk II: Color Profile System — IMPLEMENTED (2/26)

## Context

DreamSync's color/palette system is currently hardcoded in `effects.py` — 8 named palettes mapped to moods via `MOOD_PALETTES`, with `MOOD_EFFECTS` mapping moods to weighted effect pools. The `EffectCycler` randomly picks from these on mood changes and every ~16s. This works but offers zero creative control. The goal is to let users define **standalone profile YAML files** that override which colors, palettes, effects, and parameters are used per mood — loadable via CLI, hot-swappable mid-session, and shippable as built-in presets.

## Architecture Overview

```
profile.yaml  ──load──>  ProfileConfig (frozen dataclass)
                              │
                              ▼
                     EffectCycler.set_profile()
                              │
            ┌─────────────────┼─────────────────┐
            ▼                 ▼                  ▼
    _pick_palette()    _pick_effect()    _apply_palette()
    (profile-aware)    (profile-aware)   (merges profile params)
            │                 │                  │
            └─────────────────┼──────────────────┘
                              ▼
                     EffectPreset (colors + mode + params)
                              │
                              ▼
                     Director.set_colors() → Renderer
```

**Key design decision:** The `EffectCycler` is the single integration point. Director, MoodClassifier, and SegmentRenderer don't know about profiles. This keeps the change surface minimal.

---

## Profile YAML Schema

```yaml
name: "Aurora Borealis"                    # required
description: "Northern lights palette"     # optional metadata
author: "dreamsync"
tags: ["ambient", "nature"]
version: 1

# Named palettes local to this profile (3-8 hex colors each)
palettes:
  aurora_green: ["#003300", "#006633", "#009966", "#00cc99", "#33ffcc", "#66ffcc"]
  aurora_purple: ["#330066", "#6600cc", "#9933ff", "#cc66ff", "#9900ff", "#6633cc"]
  aurora_vivid: ["#00ff99", "#9933ff", "#ff0099", "#00ffcc", "#cc00ff", "#ff3366"]

# Per-mood config: palettes, optional effect overrides, optional param overrides
moods:
  chill:
    palettes: ["aurora_green"]              # picks from these (profile-local OR built-in names)
    effects:                                # optional: override effect pool
      - name: "wave_drift"
        weight: 3.0
      - name: "slow_breathe"
        weight: 2.0
    params:                                 # optional: merged onto active effect's params
      wave_rate_mult: 0.3
  groove:
    palettes: ["aurora_green", "aurora_purple"]
  hype:
    palettes: ["aurora_vivid", "aurora_purple"]
  drop:
    palettes: ["aurora_vivid"]
    params: { pulse_decay: 3.5 }

# Force a specific palette on mood transitions (optional)
transitions:
  - from: "chill"
    to: "groove"
    palette: "aurora_green"
  - from: "groove"
    to: "hype"
    palette: "aurora_purple"

# Override cycle interval for this profile (optional, default 16s)
cycle_interval: 24.0
```

**Validation rules:**
- `name` required, non-empty string
- `version` must be `1`
- Each palette: list of 3–8 hex strings matching `^#[0-9a-fA-F]{6}$`
- Mood keys must be `chill|groove|hype|drop`; each mood needs ≥1 palette reference
- Palette references resolve profile-local first, then built-in (`PALETTES` in effects.py)
- Effect names must exist in `EFFECTS` dict
- Transition mood/palette names must be valid

---

## New Files

### `src/dreamsync/profile.py` — Core module

**Dataclasses:**
- `MoodEffectEntry(name: str, weight: float)` — one weighted effect entry
- `MoodProfileConfig(palettes, effects, params)` — per-mood config within a profile
- `TransitionRule(from_mood, to_mood, palette)` — forced palette on mood change
- `ProfileConfig(name, description, author, tags, version, source_path, palettes, moods, transitions, cycle_interval)` — full runtime profile

**Functions:**
- `load_profile(path: Path) -> ProfileConfig` — parse YAML, validate, return frozen config
- `resolve_profile_path(name_or_path: str) -> Path` — resolution order: explicit path → CWD with .yaml → built-in profiles dir
- `list_available_profiles() -> list[dict]` — scan built-in dir, return name/description/tags
- `suggest_profile(hour: int | None) -> str` — time-of-day suggestion (morning/afternoon/evening/night)
- `validate_color_harmony(colors) -> list[str]` — warn on low-contrast or same-brightness palettes

**Classes:**
- `ProfileWatcher(profile_path, on_change_callback, poll_interval=2.0)` — mtime-polling watcher (same pattern as `ConfigWatcher`), calls `effect_cycler.set_profile()` directly on change. Thread-safe because `set_profile()` is a single reference assignment under GIL.
- `ProfileRotation(profiles, interval_seconds=300)` — rotates through multiple profiles on a timer, returns new `ProfileConfig` when it's time to rotate

### `src/dreamsync/profiles/` — Built-in profiles directory

8 built-in profiles + 1 template:

| File | Vibe |
|---|---|
| `_template.yaml` | Documented template with commented examples for every field |
| `warm_sunset.yaml` | Golden oranges, deep reds, ambers |
| `ocean_deep.yaml` | Blues, teals, seafoam |
| `midnight_rave.yaml` | Deep purples, electric blues, laser greens |
| `neon_city.yaml` | Hot pinks, electric blues, lime greens |
| `forest_canopy.yaml` | Deep greens, earth tones, golden light |
| `aurora.yaml` | Shifting greens, purples, pinks (northern lights) |
| `monochrome.yaml` | Grayscale + single red accent |
| `candy.yaml` | Pastels and bright saturated pops |

### `tests/test_profile.py` — Test suite

63 tests covering: load/validate (happy + error paths, 12 error cases), path resolution, EffectCycler integration (8 tests), ProfileWatcher (2 tests), ProfileRotation (4 tests), hot-swap integration (7 tests), smart features, all 8 built-ins load successfully (parametrized).

---

## Modified Files

### `src/dreamsync/effects.py` — EffectCycler becomes profile-aware

- Constructor gains `profile: ProfileConfig | None = None`
- `set_profile(profile)` — hot-swap the active profile; clears `_current_mood` to force re-pick on next `update()` (avoids stale palette name KeyError)
- `_pick_palette(mood)` — checks `profile.moods[mood].palettes` first, falls back to `MOOD_PALETTES`
- `_pick_effect(mood, exclude)` — checks `profile.moods[mood].effects` first, falls back to `MOOD_EFFECTS`
- `_resolve_palette_colors(name)` — checks `profile.palettes[name]` first, falls back to `PALETTES`
- `_apply_palette(effect, palette)` — merges `profile.moods[mood].params` on top of effect base params
- `_check_transition_palette(old_mood, new_mood)` — checks `profile.transitions` for forced palette
- `update()` — on mood change, consults transition rules before random palette pick; tracks `_prev_mood`

### `src/dreamsync/cli.py` — New flags and subcommands

- `--profile <name-or-path>` on `session` and `govee-live` subcommands
- `--auto-profile` flag — uses `suggest_profile()` based on time of day
- `--profile-rotation <name1,name2,...>` — rotate through profiles (with `--rotation-interval`)
- `profiles` subcommand — lists built-in and CWD profiles (with `--verbose` flag)
- `profile-validate <path>` subcommand — loads profile, runs harmony checks, reports warnings

### `src/dreamsync/live.py` — Accept profile params

- `run_live_to_govee()` gains `profile: ProfileConfig | None` and `effect_cycler: EffectCycler | None` parameters
- If `effect_cycler` is passed in (pre-built by session.py for hot-reload wiring), uses it directly
- Otherwise constructs one as before, passing `profile` through

### `src/dreamsync/session.py` — Profile wiring + hot-reload

- `run_session()` gains `profile: ProfileConfig | None` and `profile_path: Path | None`
- Creates `EffectCycler` upfront (moved out of live.py) so `ProfileWatcher` can hold a reference
- Starts `ProfileWatcher` if profile has a `source_path` and `hot_reload=True`
- Stops watcher in cleanup

### `pyproject.toml` — Package data

- Add `profiles/*.yaml` to package data so built-in profiles ship with the package

---

## Smart Features

### Time-of-day suggestions (`--auto-profile`)
Maps hour ranges to built-in profiles: morning → warm_sunset, daytime → ocean_deep, evening → forest_canopy, night → midnight_rave, late night → neon_city.

### Profile rotation (`--profile-rotation`)
`ProfileRotation` class holds a list of loaded `ProfileConfig` objects and a rotation interval. On each `update(t)` call, checks if interval elapsed and returns the next profile (or None). Wired into the live loop alongside `EffectCycler.set_profile()`.

### Transition rules
Profile YAML `transitions:` section forces a specific palette on known mood transitions. Creates a deliberate visual narrative arc (e.g., always enter HYPE from purple, always DROP with green flash).

### Color harmony validation (`profile-validate`)
CLI subcommand that loads a profile and checks every palette for:
- Adjacent colors too similar (Euclidean RGB distance < 30)
- All colors same perceived brightness (ITU-R BT.601)
- Reports warnings per palette

---

## Implementation Order

| Phase | What | Files |
|---|---|---|
| 1 | Core data model + loader + validation + tests | `profile.py`, `tests/test_profile.py` |
| 2 | EffectCycler integration + tests | `effects.py`, `tests/test_profile.py` |
| 3 | CLI wiring (`--profile`, `profiles` cmd) | `cli.py`, `live.py` |
| 4 | Built-in profiles + template + pyproject.toml | `profiles/*.yaml`, `pyproject.toml` |
| 5 | Hot-swap (`ProfileWatcher`) + session wiring | `profile.py`, `session.py` |
| 6 | Smart features (suggest, rotation, harmony) | `profile.py`, `cli.py` |

---

## Verification — Results

1. **Unit tests:** `python -m pytest tests/test_profile.py -v` — 63 tests pass
2. **Built-in profiles:** All 8 load without errors, all have 4 moods defined (parametrized test)
3. **CLI smoke test:** `python -m dreamsync profiles` lists all built-ins with descriptions
4. **Profile validation:** `python -m dreamsync profile-validate aurora` — no warnings
5. **Live test:** Pending — `python -m dreamsync govee-live --device <addr> --duration 120 --profile aurora --debug-mood`
6. **Hot-swap test:** Unit tested (7 integration tests); live test pending
7. **Existing tests still pass:** `python -m pytest` — 496 total (433 existing + 63 new), zero regressions

### Notable bug fix during implementation
`set_profile()` originally only swapped the `_profile` reference. This caused a `KeyError` when the new profile didn't define the old profile's palette names (stale `_palette_name` cache). Fixed by clearing `_current_mood` in `set_profile()`, which forces the next `update()` to re-pick both effect and palette from the new profile.
