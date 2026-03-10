# Dynamic Color Profile Generation & Smart Profile Chaining

Priority: P2 | Effort: Medium-High | Affects: visual variety, session-length aesthetic coherence

---

## Goals

1. **Unlimited visual variety** — procedurally generate color profiles from color theory so sessions never repeat the same palette combination, without hand-crafting YAML files.
2. **Seamless transitions** — replace the current hard-swap timer rotation with mood-aware, color-distance-informed profile chaining that cross-fades palettes.
3. **Zero-config operation** — a single `--auto-palette` flag that generates, chains, and cross-fades profiles endlessly. Manual `--profile` and `--profile-rotation` remain for explicit control.

## Non-Goals

- Replacing the existing 8 built-in YAML profiles (they remain as-is, and can participate in chaining).
- AI/ML-based palette generation (pure algorithmic color theory).
- Per-device profile assignments (all devices share the active profile).

---

## Feature 1: Procedural Profile Generator

### Overview

Generate valid `ProfileConfig` objects algorithmically from a base hue, temperature, and color harmony scheme. Each generated profile contains 3 palettes (one per energy level), mood-specific assignments, transition rules, and sensible effect params — structurally identical to hand-crafted YAML profiles.

### Prerequisites
- Read `src/dreamsync/profile.py` — `ProfileConfig`, `MoodProfileConfig`, `MoodEffectEntry`, `TransitionRule` frozen dataclasses, `validate_color_harmony()`
- Read `src/dreamsync/effects.py` — `PALETTES` (8 built-in), `MOOD_PALETTES`, `MOOD_EFFECTS`, `EFFECTS` dict, `EffectPreset`
- Read `src/dreamsync/profiles/aurora.yaml` and `neon_city.yaml` — understand mood↔palette mapping patterns in hand-crafted profiles
- Understand hex color format (`#rrggbb` strings) used everywhere in the pipeline

### Deliverable 1A: HSL Color Utilities

**New file:** `src/dreamsync/color_utils.py`

Color math utilities for procedural generation and cross-fade blending. All functions operate on hex strings (the format used by the rest of the codebase).

```python
# --- Conversions ---
def hex_to_rgb(color: str) -> tuple[int, int, int]:
    """'#ff4400' → (255, 68, 0)"""

def rgb_to_hex(r: int, g: int, b: int) -> str:
    """(255, 68, 0) → '#ff4400'"""

def hex_to_hsl(color: str) -> tuple[float, float, float]:
    """'#ff4400' → (h: 0-360, s: 0-1, l: 0-1)"""

def hsl_to_hex(h: float, s: float, l: float) -> str:
    """(16.0, 1.0, 0.5) → '#ff4400'"""

# --- Interpolation ---
def interpolate_hex(a: str, b: str, t: float) -> str:
    """Linear RGB interpolation. t=0 → a, t=1 → b."""

def interpolate_hex_hsl(a: str, b: str, t: float) -> str:
    """HSL interpolation (shortest hue arc). Better for perceptual blending."""

# --- Harmony generators ---
def complementary(base_hue: float) -> list[float]:
    """[base, base+180]"""

def triadic(base_hue: float) -> list[float]:
    """[base, base+120, base+240]"""

def analogous(base_hue: float, spread: float = 30.0) -> list[float]:
    """[base-spread, base, base+spread]"""

def split_complementary(base_hue: float) -> list[float]:
    """[base, base+150, base+210]"""

def tetradic(base_hue: float) -> list[float]:
    """[base, base+90, base+180, base+270]"""

# --- Palette generation ---
def generate_palette(anchor_hues: list[float], saturation: float, lightness: float,
                     count: int = 6, variation: float = 0.1) -> tuple[str, ...]:
    """Generate `count` hex colors by distributing around anchor hues with
    slight S/L variation. Returns tuple of hex strings (3-8 colors)."""

# --- Distance ---
def palette_distance(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    """Mean Euclidean distance in RGB space between two palettes' centroids.
    Used by smart chaining to find compatible neighbors."""

def profile_color_distance(a: 'ProfileConfig', b: 'ProfileConfig') -> float:
    """Average palette_distance across all palettes in two profiles."""
```

#### Unit Tests — `dev/tests/test_color_utils.py`

- `test_hex_rgb_roundtrip` — hex→rgb→hex is identity for a set of known colors
- `test_hex_hsl_roundtrip` — hex→hsl→hex is identity (within ±1 per channel)
- `test_interpolate_hex_endpoints` — t=0 returns `a`, t=1 returns `b`
- `test_interpolate_hex_midpoint` — `("#ff0000", "#0000ff", 0.5)` → ~`"#800080"`
- `test_interpolate_hsl_shortest_arc` — hue 350° → 10° goes through 0°, not 180°
- `test_complementary_hues` — `complementary(0)` → `[0, 180]`
- `test_triadic_hues` — `triadic(0)` → `[0, 120, 240]`
- `test_analogous_hues` — `analogous(180, 30)` → `[150, 180, 210]`
- `test_generate_palette_count` — `generate_palette(..., count=6)` returns 6 colors
- `test_generate_palette_valid_hex` — all returned strings match `#[0-9a-f]{6}`
- `test_palette_distance_identical` — same palette → distance 0
- `test_palette_distance_different` — red palette vs blue palette → distance > 200
- `test_profile_color_distance_symmetric` — `distance(a, b) == distance(b, a)`

#### Completion Criteria
- All conversions are lossless within ±1 per RGB channel
- Generated palettes pass `validate_color_harmony()` (no "too similar" warnings)
- No external dependencies (pure math on stdlib `colorsys`)

---

### Deliverable 1B: Profile Generator

**New file:** `src/dreamsync/profile_generator.py`

Generates complete `ProfileConfig` objects from a small set of parameters.

```python
@dataclass(frozen=True)
class GeneratorParams:
    base_hue: float              # 0-360, dominant hue
    temperature: str             # "warm" | "neutral" | "cool"
    saturation: str              # "muted" | "medium" | "vivid"
    harmony: str                 # "complementary" | "triadic" | "analogous" |
                                 # "split_complementary" | "tetradic"
    seed: int | None = None      # for reproducible generation

HARMONY_SCHEMES = {
    "complementary": complementary,
    "triadic": triadic,
    "analogous": analogous,
    "split_complementary": split_complementary,
    "tetradic": tetradic,
}

# Temperature shifts applied to base_hue
TEMPERATURE_BIAS = {"warm": -15.0, "neutral": 0.0, "cool": +15.0}

# Saturation ranges per level
SATURATION_RANGES = {
    "muted": (0.25, 0.45),
    "medium": (0.50, 0.70),
    "vivid": (0.80, 1.00),
}

def generate_profile(params: GeneratorParams) -> ProfileConfig:
    """Generate a complete ProfileConfig from GeneratorParams.

    Palette generation strategy:
      1. Compute anchor hues from harmony scheme + temperature bias
      2. Generate 3 palettes at different energy levels:
         - "calm"   — low saturation, mid-high lightness (for CHILL)
         - "energy" — medium saturation, mid lightness (for GROOVE)
         - "intense" — high saturation, lower lightness (for HYPE/DROP)
      3. Each palette gets 6 colors distributed across the anchor hues

    Mood mapping:
      - CHILL:  palettes=["calm"],    effects=[wave_drift(3), slow_breathe(2), gradient_flow(2)]
      - GROOVE: palettes=["calm", "energy"], effects=[beat_pulse(3), color_scroll(2)]
      - HYPE:   palettes=["energy", "intense"], effects=[fast_scroll(2), beat_pulse(1)]
      - DROP:   palettes=["intense"], effects=[drop_blast(1)]

    Transitions:
      - chill→groove: forced palette "calm" (bridge through familiar colors)
      - groove→hype:  forced palette "energy"
      - hype→drop:    forced palette "intense"

    Returns a valid, frozen ProfileConfig with:
      - name: "{harmony}_{base_hue}_{temperature}" (e.g., "triadic_220_cool")
      - 3 palettes, 4 moods, 3 transitions, cycle_interval from harmony type
    """

def generate_random_profile(rng: random.Random | None = None) -> ProfileConfig:
    """Generate a profile with randomized GeneratorParams.
    Picks random base_hue (0-360), random temperature, random saturation,
    random harmony scheme. Useful for auto-palette mode."""

def generate_profile_set(count: int, seed: int | None = None) -> list[ProfileConfig]:
    """Generate `count` profiles with maximally-spread base hues.
    Uses golden-angle spacing (137.5°) to distribute hues evenly around
    the color wheel. Alternates harmony schemes and temperatures."""
```

#### Key Design Decisions

1. **3 palettes per profile, not 1** — mirrors the best hand-crafted profiles (aurora has 3). Energy-tiered palettes give mood changes visual meaning.

2. **Golden-angle hue spacing** for `generate_profile_set()` — ensures consecutive profiles are visually distinct (not adjacent on the color wheel). The golden angle (360° / phi^2 ≈ 137.5°) maximizes minimum angular distance for any N.

3. **Deterministic with seed** — same seed produces same profile set, enabling reproducible sessions and cache hits.

4. **Output is standard ProfileConfig** — generated profiles are indistinguishable from loaded YAML profiles. All downstream code (EffectCycler, TreatmentSelector, ShowCache) works unchanged.

#### Unit Tests — `dev/tests/test_profile_generator.py`

- `test_generate_profile_returns_valid_config` — result is `ProfileConfig`, passes all `load_profile` validation rules
- `test_generate_profile_has_three_palettes` — exactly 3 palettes: "calm", "energy", "intense"
- `test_generate_profile_all_moods_defined` — all 4 moods present with non-empty palette lists
- `test_generate_profile_palette_colors_valid_hex` — all colors match `#[0-9a-f]{6}`
- `test_generate_profile_palettes_pass_harmony_check` — `validate_color_harmony()` returns no warnings
- `test_generate_profile_deterministic_with_seed` — same `GeneratorParams(seed=42)` → identical ProfileConfig
- `test_generate_profile_different_seeds_differ` — seed=1 vs seed=2 → different palette colors
- `test_generate_random_profile_valid` — random generation produces valid ProfileConfig
- `test_generate_profile_set_count` — `generate_profile_set(5)` returns 5 profiles
- `test_generate_profile_set_hue_spread` — consecutive profiles have base hues ≥ 90° apart
- `test_generate_profile_set_deterministic` — same seed → same set
- `test_all_harmony_schemes` — each of the 5 harmony types generates a valid profile
- `test_all_temperature_levels` — warm/neutral/cool each produce valid profiles with hue shift
- `test_all_saturation_levels` — muted/medium/vivid produce increasing saturation in palettes
- `test_generated_profile_works_with_effect_cycler` — create EffectCycler(profile=generated), call update() → returns valid EffectPreset
- `test_generated_profile_works_with_treatment_selector` — create TreatmentSelector(profile=generated), call select() → returns valid Treatment

#### Completion Criteria
- `generate_profile()` produces ProfileConfig that passes all existing validation
- Generated profiles work with EffectCycler, TreatmentSelector, and ShowCache without modification
- `generate_profile_set(20)` produces 20 visually distinct profiles (no two with base hues within 30°)
- Deterministic: same seed → same output across runs

---

## Feature 2: Smart Profile Chaining

### Overview

Replace the current `ProfileRotation` (sequential timer) with an intelligent `ProfileChain` that:
1. Picks next profiles based on color-space proximity (smooth visual transitions)
2. Only switches during CHILL or GROOVE moods (never interrupts HYPE/DROP)
3. Cross-fades palettes over a configurable blend duration instead of hard-swapping

### Prerequisites
- Deliverable 1A (color_utils.py — `profile_color_distance()`, `interpolate_hex()`)
- Deliverable 1B (profile_generator.py — for auto-generated profile pools)
- Read `src/dreamsync/profile.py` — `ProfileRotation` class (current implementation)
- Read `src/dreamsync/effects.py` — `EffectCycler.set_profile()`, `_resolve_palette_colors()`
- Read `src/dreamsync/mood.py` — `Mood` enum, `MoodClassifier`
- Read `src/dreamsync/live.py` — profile rotation usage in `run_live_to_govee()` main loop (~line 1547)
- Read `src/dreamsync/session.py` — profile rotation usage in session runner

### Deliverable 2A: Profile Distance Matrix & Neighbor Selection

**New file:** `src/dreamsync/profile_chain.py`

```python
def build_distance_matrix(profiles: list[ProfileConfig]) -> list[list[float]]:
    """NxN symmetric matrix of profile_color_distance() between all pairs.
    Used to find nearest neighbors for smooth transitions."""

def pick_next_profile(current: ProfileConfig,
                      pool: list[ProfileConfig],
                      history: list[str],
                      rng: random.Random,
                      max_distance: float | None = None,
                      min_distance: float | None = None) -> ProfileConfig:
    """Select next profile from pool based on color distance to current.

    Strategy:
      1. Compute distance from current to all candidates in pool
      2. Exclude profiles in recent history (last 3 names)
      3. Filter to candidates within [min_distance, max_distance] range
         - min_distance prevents picking something too similar (boring)
         - max_distance prevents jarring jumps
         - Defaults: min_distance=40, max_distance=180 (RGB space)
      4. Weight candidates by inverse distance (closer = more likely)
      5. Weighted random choice → return selected profile

    If no candidates in range (small pool), relax constraints and pick closest
    non-history profile.
    """
```

#### Unit Tests — `dev/tests/test_profile_chain.py`

- `test_distance_matrix_symmetric` — `matrix[i][j] == matrix[j][i]`
- `test_distance_matrix_diagonal_zero` — `matrix[i][i] == 0`
- `test_distance_matrix_size` — N profiles → NxN matrix
- `test_pick_next_avoids_history` — history=["a","b","c"], pool has a,b,c,d → picks d
- `test_pick_next_prefers_close_profiles` — over 100 picks, nearest neighbor is picked most often
- `test_pick_next_respects_max_distance` — with tight max_distance, only picks nearby profiles
- `test_pick_next_respects_min_distance` — identical profile in pool is never picked (distance=0 < min)
- `test_pick_next_relaxes_on_small_pool` — pool of 2 profiles, history excludes 1 → picks the other
- `test_pick_next_deterministic_with_seed` — same rng seed → same sequence of picks

---

### Deliverable 2B: Palette Cross-Fade (Blended Profile)

Add the ability to create a temporary "blended" `ProfileConfig` that interpolates between two profiles' palettes.

**Addition to `src/dreamsync/profile_chain.py`:**

```python
def blend_profiles(outgoing: ProfileConfig,
                   incoming: ProfileConfig,
                   t: float) -> ProfileConfig:
    """Create a transient ProfileConfig by interpolating palette colors.

    t=0.0 → outgoing's colors, t=1.0 → incoming's colors.

    Strategy:
      1. Find matching palette names between outgoing and incoming
         (e.g., both have "calm", "energy", "intense" if generated)
      2. For matched palettes: interpolate each color pair via interpolate_hex_hsl()
      3. For unmatched palettes: include as-is from the appropriate side
         (outgoing's unmatched for t<0.5, incoming's for t>=0.5)
      4. Mood config comes from the incoming profile (it's taking over)
      5. Name: f"blend_{outgoing.name}_to_{incoming.name}"

    Returns a new frozen ProfileConfig that can be passed to EffectCycler.set_profile().
    """

def make_blendable_pair(outgoing: ProfileConfig,
                        incoming: ProfileConfig) -> tuple[ProfileConfig, ProfileConfig]:
    """Normalize two profiles so their palettes can be blended.

    If profiles have different palette names (e.g., "aurora_green" vs "calm"),
    create renamed copies with canonical names ("palette_0", "palette_1", "palette_2")
    mapped by energy level (sorted by average lightness).

    This enables smooth blending between hand-crafted and generated profiles.
    """
```

#### Unit Tests

- `test_blend_at_zero_equals_outgoing` — `blend_profiles(a, b, 0.0)` has same palette colors as `a`
- `test_blend_at_one_equals_incoming` — `blend_profiles(a, b, 1.0)` has same palette colors as `b`
- `test_blend_midpoint_interpolates` — `blend_profiles(a, b, 0.5)` palette colors are midpoints
- `test_blend_returns_valid_profile` — blended profile passes validation, has all 4 moods
- `test_blend_uses_incoming_mood_config` — effects/params come from incoming profile
- `test_make_blendable_pair_normalizes_names` — different palette names → canonical names
- `test_blend_generated_to_builtin` — blend a generated profile with "aurora" → valid profile
- `test_blend_two_builtins` — blend "aurora" with "neon_city" → valid profile

---

### Deliverable 2C: Smart Chain Controller

**New class in `src/dreamsync/profile_chain.py`:**

```python
@dataclass(frozen=True)
class ChainConfig:
    min_profile_duration: float = 60.0    # seconds before allowing switch
    max_profile_duration: float = 180.0   # seconds before forcing switch
    blend_duration: float = 8.0           # seconds to cross-fade between profiles
    switch_on_moods: frozenset[str] = frozenset({"chill", "groove"})  # only switch during these
    min_color_distance: float = 40.0      # avoid too-similar consecutive profiles
    max_color_distance: float = 180.0     # avoid jarring jumps
    history_size: int = 3                 # don't revisit recent profiles

class ProfileChain:
    """Intelligent profile sequencer with mood-aware switching and cross-fade.

    Replaces ProfileRotation for --auto-palette and --smart-rotation modes.
    """

    def __init__(self,
                 pool: list[ProfileConfig],
                 config: ChainConfig | None = None,
                 seed: int | None = None) -> None:
        """
        pool: available profiles (generated, built-in, or mixed)
        config: chaining behavior parameters
        seed: for reproducible sequencing
        """
        self._pool: list[ProfileConfig]
        self._config: ChainConfig
        self._rng: random.Random
        self._current: ProfileConfig          # active profile
        self._current_start_t: float          # when current profile was set
        self._next: ProfileConfig | None      # pre-selected next profile
        self._blend_start_t: float | None     # when cross-fade started (None = not blending)
        self._history: list[str]              # recent profile names
        self._distance_matrix: list[list[float]]  # precomputed distances

    @property
    def current(self) -> ProfileConfig:
        """The profile that should be active right now.
        During a blend, returns the interpolated profile."""

    @property
    def is_blending(self) -> bool:
        """True if currently cross-fading between profiles."""

    def update(self, t: float, mood: str) -> ProfileConfig | None:
        """Called every frame from the main loop.

        Returns a new ProfileConfig if the active profile changed (including
        blend intermediate profiles), or None if no change.

        State machine:
          IDLE:
            - t - current_start_t < min_profile_duration → None
            - t - current_start_t >= min_profile_duration AND mood in switch_on_moods:
                → pre-select next profile via pick_next_profile()
                → transition to BLENDING
            - t - current_start_t >= max_profile_duration:
                → force transition regardless of mood
                → transition to BLENDING

          BLENDING:
            - elapsed = t - blend_start_t
            - progress = elapsed / blend_duration
            - if progress < 1.0:
                → return blend_profiles(current, next, progress)
            - if progress >= 1.0:
                → set current = next, clear blend state
                → transition to IDLE
                → return next (the final incoming profile)
        """

    def force_switch(self, t: float) -> ProfileConfig:
        """Immediately switch to next profile (no blend). For manual override."""

    def reset(self, t: float) -> None:
        """Reset state for new session. Keeps pool and config."""
```

#### Integration Points

The `ProfileChain.update()` method replaces the current `ProfileRotation.update()` call in:

1. **`src/dreamsync/live.py`** — `run_live_to_govee()` main loop:
   ```python
   # BEFORE (current):
   if profile_rotation is not None:
       new_profile = profile_rotation.update(stream_t)
       if new_profile is not None:
           effect_cycler.set_profile(new_profile)

   # AFTER (with smart chaining):
   if profile_chain is not None:
       new_profile = profile_chain.update(stream_t, mood_classifier.mood.value)
       if new_profile is not None:
           effect_cycler.set_profile(new_profile)
   ```

2. **`src/dreamsync/session.py`** — `run_session()` equivalent location.

3. **Show compiler** — `compile_show()` in `src/dreamsync/compiler/compile.py`:
   For compiled shows, chaining doesn't apply (single profile per show). No changes needed.

#### Unit Tests — `dev/tests/test_profile_chain.py` (continued)

- `test_chain_no_switch_before_min_duration` — update() returns None for first 60s
- `test_chain_switches_on_chill` — after min_duration, mood="chill" → returns new profile
- `test_chain_switches_on_groove` — after min_duration, mood="groove" → returns new profile
- `test_chain_blocks_on_hype` — after min_duration, mood="hype" → returns None (waits)
- `test_chain_blocks_on_drop` — after min_duration, mood="drop" → returns None (waits)
- `test_chain_forces_at_max_duration` — after max_duration, any mood → returns new profile
- `test_chain_blend_returns_interpolated` — during blend period, returns blended ProfileConfig
- `test_chain_blend_completes` — after blend_duration, returns final incoming profile
- `test_chain_blend_progress_monotonic` — blend palette colors move continuously from outgoing → incoming
- `test_chain_history_prevents_revisit` — with history_size=3, last 3 profiles never re-selected
- `test_chain_respects_distance_bounds` — selected profiles within [min, max] color distance
- `test_chain_deterministic_with_seed` — same seed + same pool → same sequence over 10 updates
- `test_chain_force_switch_immediate` — force_switch() skips blend, immediately sets new profile
- `test_chain_reset_clears_state` — after reset(), next update() starts fresh
- `test_chain_small_pool_still_works` — pool of 2 profiles → alternates between them
- `test_chain_single_profile_pool` — pool of 1 → never switches, always returns None
- `test_chain_mixed_pool_generated_and_builtin` — pool of generated + loaded YAML profiles → chains correctly

#### Completion Criteria
- Profile switches only happen during CHILL/GROOVE moods (configurable)
- Cross-fade is perceptually smooth (no color pops at blend boundaries)
- Never revisits the same profile within `history_size` switches
- Consecutive profiles are visually related but not identical (distance bounds enforced)
- Falls back gracefully with small pools (2 profiles = alternation, 1 = static)

---

## Feature 3: CLI Integration & Wiring

### Prerequisites
- Deliverable 1B (profile_generator.py)
- Deliverable 2C (profile_chain.py — ProfileChain)
- Read `src/dreamsync/cli.py` — `build_parser()`, `_resolve_profile_from_args()`, profile flag wiring
- Read `src/dreamsync/live.py` — `run_live_to_govee()` signature and profile rotation usage
- Read `src/dreamsync/session.py` — `run_session()` profile handling

### Deliverable 3A: CLI Flags

Add new flags to the `govee-live`, `session`, and `play` subcommand parsers:

| Flag | Default | Description |
|------|---------|-------------|
| `--auto-palette` | off | Enable procedural profile generation + smart chaining |
| `--auto-palette-seed` | None | Seed for reproducible profile generation |
| `--auto-palette-count` | 12 | Number of profiles to generate for the pool |
| `--chain-blend` | 8.0 | Cross-fade duration in seconds between profiles |
| `--chain-interval` | 60-180 | Min-max seconds per profile (e.g., `60` or `60-180`) |
| `--smart-rotation` | off | Use smart chaining with `--profile-rotation` profiles (no generation) |

**Flag interactions:**

| Flags | Behavior |
|---|---|
| `--profile aurora` | Single static profile (existing behavior, unchanged) |
| `--profile-rotation aurora,neon_city` | Timer rotation (existing behavior, unchanged) |
| `--smart-rotation --profile-rotation aurora,neon_city,ocean_deep` | Smart chaining through specified profiles |
| `--auto-palette` | Generate 12 profiles + smart chaining (fully automatic) |
| `--auto-palette --auto-palette-count 20` | Generate 20 profiles + smart chaining |
| `--auto-palette --profile aurora` | Error: mutually exclusive |
| (no profile flags) | No profile, built-in defaults (existing behavior, unchanged) |

**Modification to `_resolve_profile_from_args(args)`:**

```python
def _resolve_profile_and_chain_from_args(args) -> tuple[ProfileConfig | None,
                                                         ProfileChain | None]:
    """Resolve profile flags into initial profile + optional chain controller.

    Returns:
      (profile, None)        — static single profile
      (profile, chain)       — smart chaining (auto-palette or smart-rotation)
      (None, None)           — no profile flags, use built-in defaults
    """
```

### Deliverable 3B: Live Loop & Session Wiring

**Files to modify:**
- `src/dreamsync/cli.py` — `build_parser()`, flag parsing in `main()`
- `src/dreamsync/live.py` — `run_live_to_govee()` signature, replace `profile_rotation` with `profile_chain`
- `src/dreamsync/session.py` — `run_session()`, same replacement

**Changes to `run_live_to_govee()`:**

```python
# Current signature includes:
#   profile_rotation: ProfileRotation | None = None

# New signature adds:
#   profile_chain: ProfileChain | None = None

# In the main loop:
# Replace profile_rotation.update(stream_t) with:
if profile_chain is not None and effect_cycler is not None:
    new_profile = profile_chain.update(stream_t, mood_classifier.mood.value)
    if new_profile is not None:
        effect_cycler.set_profile(new_profile)
        if debug_mood:
            blending = " (blending)" if profile_chain.is_blending else ""
            print(f"[chain] profile: {new_profile.name}{blending}")

# Keep profile_rotation as-is for backward compat (non-smart rotation)
elif profile_rotation is not None and effect_cycler is not None:
    new_profile = profile_rotation.update(stream_t)
    if new_profile is not None:
        effect_cycler.set_profile(new_profile)
```

**Changes to `cli.py:main()`:**

```python
# In govee-live / session command handling:
if args.auto_palette:
    from dreamsync.profile_generator import generate_profile_set
    from dreamsync.profile_chain import ProfileChain, ChainConfig

    pool = generate_profile_set(
        count=args.auto_palette_count,
        seed=args.auto_palette_seed,
    )
    chain_config = ChainConfig(
        min_profile_duration=chain_min,
        max_profile_duration=chain_max,
        blend_duration=args.chain_blend,
    )
    profile_chain = ProfileChain(pool, chain_config, seed=args.auto_palette_seed)
    initial_profile = profile_chain.current

elif args.smart_rotation and args.profile_rotation:
    # Load named profiles, chain them smartly
    profiles = [load_profile(resolve_profile_path(n.strip()))
                for n in args.profile_rotation.split(",")]
    chain_config = ChainConfig(
        min_profile_duration=chain_min,
        max_profile_duration=chain_max,
        blend_duration=args.chain_blend,
    )
    profile_chain = ProfileChain(profiles, chain_config)
    initial_profile = profile_chain.current

else:
    # Existing behavior: single profile or dumb rotation
    ...
```

### Deliverable 3C: Debug Output & `profiles` Subcommand Enhancement

**Extend `profiles` subcommand:**

```bash
# List built-in profiles (existing)
python -m dreamsync profiles --verbose

# Preview generated profiles
python -m dreamsync profiles --generate 12 --seed 42

# Preview a chain sequence (dry-run)
python -m dreamsync profiles --generate 12 --seed 42 --chain-preview 10
```

**`--chain-preview N`** simulates N profile switches and prints the sequence:
```
Chain sequence (seed=42, 12 profiles):
  1. triadic_137_cool     → blend → triadic_275_warm     (distance: 142)
  2. triadic_275_warm     → blend → analogous_52_neutral  (distance: 98)
  3. analogous_52_neutral → blend → complementary_190_cool (distance: 156)
  ...
```

**Debug output during live session (`--debug-mood`):**

```
[chain] profile: triadic_137_cool
[chain] blend start → triadic_275_warm (distance: 142, 8.0s fade)
[chain] blend 50% — triadic_137_cool → triadic_275_warm
[chain] blend complete → triadic_275_warm
```

#### Unit Tests — `dev/tests/test_cli_auto_palette.py`

- `test_auto_palette_flag_parsed` — `--auto-palette` sets `args.auto_palette = True`
- `test_auto_palette_seed_flag` — `--auto-palette-seed 42` sets `args.auto_palette_seed = 42`
- `test_auto_palette_count_flag` — `--auto-palette-count 20` sets correctly
- `test_auto_palette_mutually_exclusive_with_profile` — `--auto-palette --profile aurora` → parser error
- `test_smart_rotation_flag` — `--smart-rotation` with `--profile-rotation` → both set
- `test_smart_rotation_requires_profile_rotation` — `--smart-rotation` alone → parser error
- `test_chain_blend_flag_default` — default is 8.0
- `test_chain_interval_parsing` — `--chain-interval 60-180` parsed to min=60, max=180
- `test_chain_interval_single_value` — `--chain-interval 90` parsed to min=90, max=90*3
- `test_resolve_profile_and_chain_auto` — `--auto-palette` → returns (profile, chain) with generated pool
- `test_resolve_profile_and_chain_smart_rotation` — returns chain with loaded profiles
- `test_resolve_profile_and_chain_static` — `--profile aurora` → returns (profile, None)
- `test_profiles_generate_subcommand` — `profiles --generate 5` prints 5 profile summaries
- `test_profiles_chain_preview` — `profiles --chain-preview 5` prints 5-step sequence

#### Completion Criteria
- `--auto-palette` works end-to-end: generates profiles, chains them, cross-fades during playback
- `--smart-rotation` upgrades existing `--profile-rotation` with intelligent chaining
- All existing flags (`--profile`, `--profile-rotation`, `--rotation-interval`) continue to work unchanged
- Debug output via `--debug-mood` shows chain state transitions
- `profiles --generate` and `--chain-preview` work for offline inspection

---

## Test Plan Summary

| Test File | Tests | Scope |
|---|---|---|
| `dev/tests/test_color_utils.py` | ~13 | HSL conversions, interpolation, harmony generators, palette distance |
| `dev/tests/test_profile_generator.py` | ~15 | Profile generation, validation, determinism, integration with cycler/selector |
| `dev/tests/test_profile_chain.py` | ~26 | Distance matrix, neighbor selection, cross-fade blending, chain controller state machine |
| `dev/tests/test_cli_auto_palette.py` | ~14 | CLI flag parsing, mutual exclusivity, end-to-end wiring |
| **Total** | **~68** | |

All tests are pure unit tests — no hardware, no audio, no network. They use the same pytest patterns as existing `dev/tests/`.

### Integration Testing (Manual)

After all unit tests pass, validate with live audio:

```bash
# Auto-palette dry-run (no devices)
python -m dreamsync govee-live \
    --device 192.168.0.99:7:primary:ptreal \
    --duration 300 --auto-palette --debug-mood

# Auto-palette with devices
python -m dreamsync session --config devices.yaml \
    --auto-palette --auto-palette-seed 42 --debug-mood

# Smart rotation with built-in profiles
python -m dreamsync govee-live \
    --device 192.168.0.99:7:primary:ptreal \
    --duration 300 \
    --smart-rotation --profile-rotation aurora,neon_city,ocean_deep,warm_sunset \
    --chain-blend 10 --debug-mood

# Preview chain sequence offline
python -m dreamsync profiles --generate 12 --seed 42 --chain-preview 10 --verbose
```

Pass criteria:
- Profile switches visible in `--debug-mood` output
- Switches only happen during CHILL/GROOVE moods
- Cross-fade produces smooth color transitions (no pops)
- `Ctrl+C` shuts down cleanly (no orphan threads from ProfileChain)

---

## Implementation Order

| Step | Deliverable | Depends On | Effort |
|------|-------------|------------|--------|
| 1 | 1A: Color utilities | — | Low |
| 2 | 1B: Profile generator | 1A | Medium |
| 3 | 2A: Distance matrix & neighbor selection | 1A | Low |
| 4 | 2B: Palette cross-fade | 1A | Low-Medium |
| 5 | 2C: Smart chain controller | 2A, 2B | Medium |
| 6 | 3A: CLI flags | — | Low |
| 7 | 3B: Live loop & session wiring | 1B, 2C, 3A | Medium |
| 8 | 3C: Debug output & profiles subcommand | 3A, 3B | Low |

Steps 1-2 and 3-4 can be parallelized. Step 6 can start any time (just parser changes).

**Total new files:** 3 (`color_utils.py`, `profile_generator.py`, `profile_chain.py`)
**Total modified files:** 4 (`cli.py`, `live.py`, `session.py`, `profile.py`)
**Total new test files:** 4 (`test_color_utils.py`, `test_profile_generator.py`, `test_profile_chain.py`, `test_cli_auto_palette.py`)

---

## Overall Completion Criteria

1. `python -m pytest dev/tests/test_color_utils.py dev/tests/test_profile_generator.py dev/tests/test_profile_chain.py dev/tests/test_cli_auto_palette.py -v` — all ~68 tests pass
2. `python -m dreamsync profiles --generate 12 --verbose` — prints 12 valid profile summaries
3. `python -m dreamsync profiles --generate 12 --chain-preview 10` — prints a 10-step chain sequence with distances
4. `python -m dreamsync govee-live --device 192.168.0.99:7:primary:ptreal --duration 120 --auto-palette --debug-mood` — runs without errors, shows chain transitions in output
5. All existing tests still pass: `python -m pytest tests/ dev/tests/ -v`
6. `--profile aurora` and `--profile-rotation` behavior completely unchanged (backward compat)
