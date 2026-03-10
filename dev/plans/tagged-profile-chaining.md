# Tagged Profile Generation & Semantic Chaining

Priority: P1 | Effort: Medium | Affects: profile switching quality, visual variety, session coherence

---

## Context

Today, `pick_next_profile()` selects the next profile using raw RGB centroid distance between palettes. This is purely geometric — it has no understanding of color identity. It can't distinguish "orange complementary" from "red triadic" except by how far apart the centroids happen to land in RGB space. The result is that profile transitions sometimes feel arbitrary rather than intentional.

The generator already knows the base hue, harmony scheme, temperature, and saturation for each profile — but this information is discarded into a freeform name string (`"triadic_220_cool"`) and generic tags (`("triadic", "cool", "medium", "generated")`). The chaining logic never reads these tags.

### Current profile selection

```python
def pick_next_profile(current, pool, history, rng, max_distance, min_distance):
    # 1. Compute RGB centroid distance from current to all candidates
    # 2. Exclude history
    # 3. Filter by [min_distance, max_distance]
    # 4. Weighted random: closer profiles more likely
```

This weights toward *similar* profiles (inverse distance). There's no mechanism for intentional contrast ("follow warm orange with cool blue"), no temperature continuity, and no awareness of which colors are actually in the palette.

### Goal

Replace the distance-only heuristic with **tag-aware semantic selection** that understands what a profile looks like and can make intentional aesthetic choices:

1. **Auto-compute color tags** from generated palette colors (primary/secondary color in ROYGBIVW, temperature, intensity)
2. **Tag-aware chaining** that uses these tags for smarter selection — prefer contrasting primary colors, maintain temperature continuity within calm sections, vary intensity across transitions
3. **Seed-only persistence** — a single integer seed deterministically reproduces the full pool, so storing the seed is equivalent to storing all profiles

### User Stories

1. **Endless variety** — "I want new palettes constantly generated and rolled through during a session without me touching anything."
   → `--auto-palette` with auto-generated seed, smart chaining.

2. **Discover and save** — "That was a cool palette. I want to see what it was and be able to reuse it later."
   → Every profile switch is logged with its seed, index, and tags. The user can later recreate it by seed+index, or export it to a YAML file to add to a manual rotation deck.

3. **Curated deck** — "I have 5 favorite palettes I found during sessions. I want to rotate through just those."
   → Export discovered profiles to YAML, use `--profile-rotation` or `--smart-rotation` with them.

4. **Manual show with fixed palette** — "I want to pregenerate a show for a specific song using a specific palette."
   → `--profile my_saved_palette.yaml` works the same as today. Generated profiles can be exported to YAML and used this way.

### Non-Goals

- Changing the within-song behavior (calm/energy/intense palette tiers within a profile already handle section energy — that's unchanged)
- AI/ML-based palette generation (pure algorithmic color theory)

---

## Feature 1: Color Tag Computation

### Overview

Map hex palette colors to human-readable color categories (ROYGBIVW) and derive semantic tags from `GeneratorParams` + actual palette colors. Tags are stored in `ProfileConfig.tags` and are available to the chainer.

### Prerequisites
- Read `src/dreamsync/color_utils.py` — `hex_to_hsl()` for hue extraction
- Read `src/dreamsync/profile_generator.py` — `GeneratorParams`, `generate_profile()`, current tag generation
- Read `src/dreamsync/profile.py` — `ProfileConfig.tags` (`tuple[str, ...]`)

### Deliverable 1A: Hue-to-Color Classification

**File to modify:** `src/dreamsync/color_utils.py`

Add a function that maps a hue (0-360) to a ROYGBIVW color name, and a function that classifies a palette's dominant colors.

```python
# Hue ranges for ROYGBIVW classification
# These are perceptual boundaries, not equal divisions
HUE_BUCKETS: list[tuple[str, float, float]] = [
    ("R", 345.0, 15.0),    # Red: 345-360, 0-15
    ("O", 15.0, 45.0),     # Orange: 15-45
    ("Y", 45.0, 70.0),     # Yellow: 45-70
    ("G", 70.0, 165.0),    # Green: 70-165
    ("B", 165.0, 255.0),   # Blue: 165-255
    ("I", 255.0, 285.0),   # Indigo: 255-285
    ("V", 285.0, 345.0),   # Violet: 285-345
]

def hue_to_color_name(hue: float) -> str:
    """Map a hue (0-360) to a ROYGBIVW letter.

    Achromatic colors (saturation < 0.1) return 'W' (white/neutral).
    """

def classify_palette_colors(colors: tuple[str, ...]) -> tuple[str, str]:
    """Return (primary, secondary) ROYGBIVW letters for a palette.

    Primary = most frequent hue bucket across all colors.
    Secondary = second most frequent (or same as primary if monochromatic).
    Achromatic colors (very low saturation) are excluded from counting
    unless all colors are achromatic, in which case both return 'W'.
    """
```

#### Unit Tests — `dev/tests/test_color_tags.py`

- `test_hue_to_color_red` — hue 0 → "R"
- `test_hue_to_color_red_wrap` — hue 350 → "R" (wraps past 345)
- `test_hue_to_color_orange` — hue 30 → "O"
- `test_hue_to_color_yellow` — hue 55 → "Y"
- `test_hue_to_color_green` — hue 120 → "G"
- `test_hue_to_color_blue` — hue 220 → "B"
- `test_hue_to_color_indigo` — hue 270 → "I"
- `test_hue_to_color_violet` — hue 300 → "V"
- `test_classify_palette_monochromatic` — all-red palette → ("R", "R")
- `test_classify_palette_complementary` — red+cyan palette → ("R", "B") or ("B", "R")
- `test_classify_palette_triadic` — red+green+blue → primary is whichever has most colors
- `test_classify_palette_achromatic` — all grays → ("W", "W")

#### Completion Criteria
- All ROYGBIVW boundaries tested
- Wrap-around at 360/0 handled correctly
- Low-saturation colors classified as W

---

### Deliverable 1B: Profile Tag Generation

**File to modify:** `src/dreamsync/profile_generator.py`

Update `generate_profile()` to compute and store semantic tags on the returned `ProfileConfig`. Tags are derived from both the `GeneratorParams` (known at generation time) and the actual palette colors (computed post-generation).

```python
# Tag format (stored in ProfileConfig.tags):
# ("generated", "primary:O", "secondary:V", "warm", "vivid", "complementary")
#
# Tag categories:
#   "primary:X"     — dominant ROYGBIVW color across all 3 palettes
#   "secondary:X"   — second most common color
#   "warm" | "cool" | "neutral" — from GeneratorParams.temperature
#   "muted" | "medium" | "vivid" — from GeneratorParams.saturation
#   harmony name    — from GeneratorParams.harmony
#   "generated"     — always present on generated profiles

def compute_profile_tags(params: GeneratorParams,
                         palettes: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    """Compute semantic tags from generator params and actual palette colors.

    Analyzes the 'energy' palette (middle tier) for primary/secondary color
    since it best represents the profile's overall character. Calm is too
    desaturated, intense is too dark.
    """
```

#### Unit Tests — `dev/tests/test_color_tags.py` (continued)

- `test_profile_tags_contain_primary` — tags include "primary:X" for some X in ROYGBIVW
- `test_profile_tags_contain_secondary` — tags include "secondary:X"
- `test_profile_tags_contain_temperature` — tags include one of "warm", "cool", "neutral"
- `test_profile_tags_contain_intensity` — tags include one of "muted", "medium", "vivid"
- `test_profile_tags_contain_harmony` — tags include the harmony scheme name
- `test_profile_tags_contain_generated` — tags include "generated"
- `test_profile_tags_deterministic` — same params → same tags
- `test_profile_set_tags_vary` — generate_profile_set(12) → at least 4 distinct primary colors

#### Completion Criteria
- Every generated profile has all 6 tag categories populated
- Tags are deterministic for same params
- A 12-profile set uses at least 4 distinct primary colors (golden angle hue spacing ensures this)

---

## Feature 2: Tag-Aware Chaining

### Overview

Replace the distance-only heuristic in `pick_next_profile()` with a scoring function that considers both color distance and tag relationships. The scoring function enables intentional aesthetic choices:
- **Contrast primary color** — prefer profiles whose primary color differs from the current one (don't follow orange with orange)
- **Temperature continuity** — during CHILL mood, prefer same-temperature transitions; during HYPE, prefer temperature shifts for dramatic contrast
- **Intensity coherence** — prefer profiles with similar intensity level to smooth the visual feel

### Prerequisites
- Deliverable 1B (profiles have semantic tags)
- Read `src/dreamsync/profile_chain.py` — `pick_next_profile()`, `ProfileChain.update()`

### Deliverable 2A: Tag Parsing Helpers

**File to modify:** `src/dreamsync/profile_chain.py`

```python
def _get_tag(profile: ProfileConfig, prefix: str) -> str | None:
    """Extract a tag value by prefix. e.g., _get_tag(p, 'primary:') → 'O'"""

def _tag_score(current: ProfileConfig, candidate: ProfileConfig,
               mood: str | None = None) -> float:
    """Score a candidate profile based on tag relationships to current.

    Returns a value in [0.0, 1.0] where 1.0 = ideal transition.

    Scoring rules:
      Primary color contrast:
        - Different primary → +0.4 (variety)
        - Same primary → +0.0 (boring repetition)

      Secondary color relationship:
        - Candidate secondary == current primary → +0.2 (color thread)
        - Otherwise → +0.0

      Temperature:
        - If mood is "chill" or "groove": same temperature → +0.2 (cohesion)
        - If mood is "hype" or "drop": different temperature → +0.2 (drama)
        - Otherwise → +0.1

      Intensity:
        - Same intensity → +0.1
        - Adjacent (muted↔medium or medium↔vivid) → +0.05
        - Opposite (muted↔vivid) → +0.0

      Color distance (normalized):
        - Within ideal range [60, 150] → +0.1
        - Outside → +0.0
    """
```

#### Unit Tests — `dev/tests/test_tag_chaining.py`

- `test_get_tag_primary` — profile with "primary:O" tag → returns "O"
- `test_get_tag_missing` — profile without "primary:" tag → returns None
- `test_tag_score_different_primary_higher` — different primary scores > same primary
- `test_tag_score_color_thread_bonus` — candidate secondary == current primary → higher score
- `test_tag_score_chill_same_temp` — mood="chill", same temperature → higher than different
- `test_tag_score_hype_different_temp` — mood="hype", different temperature → higher than same
- `test_tag_score_same_intensity` — same intensity → higher than opposite
- `test_tag_score_range_zero_to_one` — score always in [0.0, 1.0]

---

### Deliverable 2B: Updated Profile Selection

**File to modify:** `src/dreamsync/profile_chain.py`

Update `pick_next_profile()` to use the tag score as a weighting factor alongside color distance. The current inverse-distance weighting is replaced with a combined score.

```python
def pick_next_profile(
    current: ProfileConfig,
    pool: list[ProfileConfig],
    history: list[str],
    rng: random.Random,
    max_distance: float | None = None,
    min_distance: float | None = None,
    mood: str | None = None,  # NEW: passed from ProfileChain.update()
) -> ProfileConfig:
    """Select next profile using combined tag score + color distance.

    Scoring:
      1. Filter: exclude current, history, out-of-distance-range
      2. For each candidate:
         - tag_weight = _tag_score(current, candidate, mood)
         - dist_weight = 1.0 / (distance + 1.0)  (inverse distance, normalized)
         - combined = (0.6 * tag_weight) + (0.4 * dist_weight)
      3. Weighted random choice using combined scores

    Fallback: if no tagged profiles (e.g., hand-crafted YAML profiles without
    tags), falls back to pure distance weighting (current behavior).
    """
```

Also update `ProfileChain.update()` to pass `mood` through to `pick_next_profile()`:

```python
# In ProfileChain.update():
if should_switch:
    self._next = pick_next_profile(
        self._current,
        self._pool,
        self._history,
        self._rng,
        max_distance=self._config.max_color_distance,
        min_distance=self._config.min_color_distance,
        mood=mood,  # NEW
    )
```

#### Unit Tests — `dev/tests/test_tag_chaining.py` (continued)

- `test_pick_next_prefers_different_primary` — pool has same-primary and different-primary; over 100 picks, different-primary is chosen more often
- `test_pick_next_color_thread` — pool has one profile whose secondary matches current primary; it's picked more often than others at same distance
- `test_pick_next_chill_prefers_same_temp` — mood="chill"; same-temperature profiles picked more often
- `test_pick_next_hype_prefers_different_temp` — mood="hype"; different-temperature profiles picked more often
- `test_pick_next_no_tags_falls_back` — pool of hand-crafted profiles with no "primary:" tags → still picks (no crash, pure distance fallback)
- `test_pick_next_mixed_pool` — pool of tagged + untagged profiles → tagged logic applies to tagged, distance-only for untagged
- `test_pick_next_still_avoids_history` — tag scoring doesn't override history exclusion
- `test_pick_next_still_respects_distance_bounds` — tag scoring doesn't override min/max distance
- `test_pick_next_deterministic_with_seed` — same seed + mood → same sequence (regression from existing test)

#### Completion Criteria
- Tagged profiles are selected using combined tag+distance scoring
- Untagged profiles (built-in YAML) fall back to pure distance (backward compat)
- History exclusion and distance bounds still enforced
- All existing `test_profile_chain.py` tests still pass (no regressions)

---

## Feature 3: Seed Persistence & CLI

### Overview

The seed is the only state needed to reproduce a profile pool. Provide a way to save the current seed so a user can re-use a palette set they liked, and display tags in profile preview output.

### Prerequisites
- Deliverable 1B (tags on generated profiles)
- Read `src/dreamsync/cli.py` — `profiles` subcommand, `_resolve_profile_chain_from_args()`

### Deliverable 3A: Enhanced Profile Preview

**File to modify:** `src/dreamsync/cli.py`

Update the `profiles --generate` output to show tags:

```
python -m dreamsync profiles --generate 12 --seed 42

  1. complementary_230_warm    [primary:B  secondary:O  warm/muted]
  2. triadic_7_neutral         [primary:R  secondary:G  neutral/medium]
  3. analogous_145_cool        [primary:G  secondary:G  cool/vivid]
  ...

Seed: 42 (use --auto-palette-seed 42 to reuse this palette set)
```

Update `profiles --generate --chain-preview` to show tag reasoning:

```
Chain sequence (seed=42, 12 profiles):
  1. complementary_230_warm (B)  → triadic_7_neutral (R)    score=0.82 dist=142 [contrast primary]
  2. triadic_7_neutral (R)      → analogous_145_cool (G)   score=0.74 dist=98  [color thread R→G]
  ...
```

### Deliverable 3B: Seed Display on Live Session

**File to modify:** `src/dreamsync/cli.py`

When `--auto-palette` is used without `--auto-palette-seed`, generate a random seed, print it, and use it:

```python
# In _resolve_profile_chain_from_args():
if auto_palette:
    seed = getattr(args, "auto_palette_seed", None)
    if seed is None:
        import time
        seed = int(time.time()) % 100000  # short, memorable
        print(f"Auto-palette seed: {seed} (reuse with --auto-palette-seed {seed})")
    pool = generate_profile_set(count, seed=seed)
```

This way the user always sees the seed and can replay a palette set they enjoyed.

#### Unit Tests — `dev/tests/test_color_tags.py` (continued)

- `test_auto_seed_printed` — `--auto-palette` without `--auto-palette-seed` → stdout contains "Auto-palette seed:"
- `test_explicit_seed_no_auto_message` — `--auto-palette --auto-palette-seed 42` → no "Auto-palette seed:" message
- `test_seed_reproduces_same_tags` — same seed → same tag set on all profiles

#### Completion Criteria
- `profiles --generate` output shows ROYGBIVW tags for each profile
- `profiles --chain-preview` output shows tag-based scoring rationale
- Auto-generated seed is printed so the user can save it
- Explicit `--auto-palette-seed` suppresses the auto-seed message

---

## Feature 4: Profile Logging & Export

### Overview

During a live session, every profile switch is logged with enough information to recreate the profile later. A CLI command exports a generated profile to a YAML file, turning a discovered palette into a permanent addition to the user's rotation deck.

This closes the loop: auto-palette generates endless variety → user spots one they like → reads the log → exports it → adds it to `--profile-rotation` for future sessions.

### Prerequisites
- Deliverable 1B (tags on generated profiles)
- Read `src/dreamsync/profiles/aurora.yaml` — YAML format for hand-crafted profiles

### Deliverable 4A: Profile Switch Logging

**File to modify:** `src/dreamsync/live.py`

Enhance the `[chain]` debug output to include enough detail to recreate the profile:

```
[chain] profile: triadic_220_cool [primary:B secondary:O cool/vivid] (seed=42 index=3)
[chain] blend start → complementary_7_warm [primary:R secondary:B warm/muted] (seed=42 index=1, distance: 142, score: 0.82)
[chain] blend 50%
[chain] blend complete → complementary_7_warm
```

The `seed=42 index=3` tells the user: "this profile is the 4th profile (0-indexed) in the pool generated by seed 42." They can recreate it with:

```bash
python -m dreamsync profiles --generate 12 --seed 42   # see all 12
python -m dreamsync profile-export --seed 42 --index 3 --output my_cool_palette.yaml
```

**File to modify:** `src/dreamsync/profile_chain.py`

Add index tracking to `ProfileChain` so the chain knows each profile's position in the original pool:

```python
class ProfileChain:
    def __init__(self, pool, config, seed):
        ...
        self._pool_index: dict[str, int] = {p.name: i for i, p in enumerate(pool)}

    def pool_index_of(self, profile: ProfileConfig) -> int | None:
        """Return the profile's index in the original pool, or None."""
        return self._pool_index.get(profile.name)
```

### Deliverable 4B: Profile Export Command

**New subcommand:** `profile-export`

```bash
# Export profile #3 from seed 42's pool to a YAML file
python -m dreamsync profile-export --seed 42 --index 3 --output my_palette.yaml

# Export with a custom name
python -m dreamsync profile-export --seed 42 --index 3 --name "Evening Blue" --output evening_blue.yaml
```

**File to modify:** `src/dreamsync/cli.py`

Add the `profile-export` subcommand parser and handler.

**New function in:** `src/dreamsync/profile_generator.py`

```python
def export_profile_yaml(profile: ProfileConfig, path: Path,
                        name_override: str | None = None) -> None:
    """Write a ProfileConfig to a YAML file in the same format as hand-crafted profiles.

    The exported file is a valid profile YAML that can be loaded with
    --profile or included in --profile-rotation.
    """
```

The exported YAML looks like:

```yaml
name: "Evening Blue"
description: "Generated triadic profile at hue 220 (cool/vivid) — exported from seed 42 index 3"
author: "dreamsync-generator"
tags: ["triadic", "cool", "vivid", "generated", "primary:B", "secondary:O"]
version: 1

palettes:
  calm:
    - "#8ca0ba"
    - "#bdaa85"
    ...
  energy:
    - "#6485b4"
    ...
  intense:
    - "#416caa"
    ...

moods:
  chill:
    palettes: ["calm"]
    effects:
      - name: "wave_drift"
        weight: 3.0
      ...
    params:
      wave_rate_mult: 0.3
  groove:
    palettes: ["calm", "energy"]
    ...
  hype:
    palettes: ["energy", "intense"]
    ...
  drop:
    palettes: ["intense"]
    ...

transitions:
  - from: "chill"
    to: "groove"
    palette: "calm"
  ...

cycle_interval: 24.0
```

#### Unit Tests — `dev/tests/test_profile_export.py`

- `test_export_creates_yaml_file` — export to tmp_path → file exists
- `test_export_roundtrips` — export → load_profile(exported_path) → same palettes, moods, transitions
- `test_export_with_name_override` — `name_override="My Name"` → loaded profile.name == "My Name"
- `test_export_preserves_tags` — exported YAML contains all tags including "primary:X"
- `test_export_description_includes_seed_info` — description mentions seed and index
- `test_export_valid_yaml` — exported file parses with yaml.safe_load without error
- `test_cli_profile_export_creates_file` — `profile-export --seed 42 --index 0 --output tmp/test.yaml` → file created
- `test_cli_profile_export_index_bounds` — `--index 999 --seed 42` with count=12 → error message

#### Completion Criteria
- `profile-export` writes valid YAML that `load_profile()` can read back
- Exported profiles work with `--profile` and `--profile-rotation`
- Debug output during live sessions includes seed+index for every profile switch
- A user can go from "I liked that palette" → read console → export → reuse in 3 commands

---

## Implementation Order

| Step | Deliverable | Depends On | Effort |
|------|-------------|------------|--------|
| 1 | 1A: Hue-to-color classification | — | Low |
| 2 | 1B: Profile tag generation | 1A | Low |
| 3 | 2A: Tag scoring helpers | 1B | Medium |
| 4 | 2B: Updated profile selection | 2A | Medium |
| 5 | 3A: Enhanced profile preview | 1B | Low |
| 6 | 3B: Seed display on live session | — | Low |
| 7 | 4A: Profile switch logging | 1B | Low |
| 8 | 4B: Profile export command | 1B | Low-Medium |

Steps 1-2 and 6 can be parallelized. Steps 3-4 are sequential. Steps 7-8 can start after step 2.

**Total new files:** 0 (all additions to existing files)
**Total modified files:** 5 (`color_utils.py`, `profile_generator.py`, `profile_chain.py`, `cli.py`, `live.py`)
**Total new test files:** 3 (`test_color_tags.py`, `test_tag_chaining.py`, `test_profile_export.py`)

---

## Test Plan Summary

| Test File | Tests | Scope |
|---|---|---|
| `dev/tests/test_color_tags.py` | ~20 | Hue-to-ROYGBIVW mapping, palette classification, profile tag generation, seed display |
| `dev/tests/test_tag_chaining.py` | ~17 | Tag parsing, tag scoring, tag-aware profile selection, backward compat |
| `dev/tests/test_profile_export.py` | ~8 | YAML export, round-trip, CLI command, name override |
| **Total** | **~45** | |

All tests are pure unit tests — no hardware, no audio, no network.

---

## Overall Completion Criteria

1. `python -m pytest dev/tests/test_color_tags.py dev/tests/test_tag_chaining.py dev/tests/test_profile_export.py -v` — all ~45 tests pass
2. `python -m pytest dev/tests/test_profile_chain.py -v` — all 30 existing chain tests still pass (no regressions)
3. `python -m pytest dev/tests/test_color_utils.py dev/tests/test_profile_generator.py dev/tests/test_cli_auto_palette.py -v` — all existing tests still pass
4. `python -m dreamsync profiles --generate 12 --seed 42` — shows ROYGBIVW tags for each profile
5. `python -m dreamsync profiles --generate 12 --seed 42 --chain-preview 10` — shows tag-based scoring
6. `python -m dreamsync govee-live --device 192.168.0.99:7:primary:ptreal --duration 120 --auto-palette --debug-mood` — prints auto-generated seed, `[chain]` output shows profile switches with seed+index+tags
7. `python -m dreamsync profile-export --seed 42 --index 3 --output test_export.yaml && python -m dreamsync profile-validate test_export.yaml` — exports and validates round-trip
8. `python -m dreamsync govee-live --device 192.168.0.99:7:primary:ptreal --duration 60 --profile test_export.yaml --debug-mood` — exported profile works as a static profile
9. All existing tests still pass: `python -m pytest dev/tests/ -v`
