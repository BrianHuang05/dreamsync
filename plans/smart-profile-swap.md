# Smart Profile Swapping — Design Plan

Automatically swap color profiles at song boundaries, giving each song a distinct visual identity.

**Depends on:** Song boundary detection (silence-gap + crossfade), color profile system (ProfileConfig, EffectCycler.set_profile), profile rotation (ProfileRotation).

---

## Problem

Profile rotation is currently **time-based only** (`--rotation-interval 300`). This means profile switches happen at arbitrary moments mid-song, creating jarring visual transitions that don't align with musical structure. The natural swap point is a **song boundary** — when the audio pipeline already resets BPM, mood, and effects.

## Solution

Add a `--smart-profile-swap` mode that triggers profile rotation on song boundaries instead of (or in addition to) a fixed timer.

---

## Architecture

```
Song boundary detected (silence or crossfade)
            │
            ▼
   SmartProfileSwapper.on_boundary()
            │
            ├── Pick next profile (sequential, random, or weighted)
            ├── Call effect_cycler.set_profile(new_profile)
            ├── Log switch (debug + telemetry)
            └── Return new ProfileConfig
```

The swapper sits alongside the existing `ProfileRotation` in the live loop. It reuses the same `effect_cycler.set_profile()` hot-swap path that ProfileRotation and ProfileWatcher already use — no new integration surface.

---

## Step 0 — SmartProfileSwapper class

**File:** `src/dreamsync/profile.py`

```python
class SmartProfileSwapper:
    """Swaps profiles on song boundaries instead of a fixed timer.

    Strategies:
      - "sequential": cycle through profiles in order (wraps around)
      - "random": pick a random profile (never the same as current)
      - "shuffle": play each profile once before repeating (deck shuffle)
    """

    def __init__(
        self,
        profiles: list[ProfileConfig],
        strategy: str = "sequential",  # "sequential" | "random" | "shuffle"
        *,
        min_song_seconds: float = 30.0,  # ignore boundaries < 30s (false triggers)
    ) -> None:
        if not profiles or len(profiles) < 2:
            raise ValueError("SmartProfileSwapper requires at least 2 profiles")
        self._profiles = list(profiles)
        self._strategy = strategy
        self._min_song_seconds = min_song_seconds

        # Sequential state
        self._index = 0

        # Shuffle state (deck of indices, reshuffled when exhausted)
        self._shuffle_deck: list[int] = []
        self._shuffle_pos = 0

        # Timing
        self._last_boundary_t: float | None = None
        self._swap_count = 0

    @property
    def current(self) -> ProfileConfig:
        return self._profiles[self._index]

    @property
    def swap_count(self) -> int:
        return self._swap_count

    def on_boundary(self, t: float) -> ProfileConfig | None:
        """Called when a song boundary is detected.

        Returns the new profile if a swap occurred, or None if the boundary
        was too soon after the previous one (false trigger protection).
        """
        if self._last_boundary_t is not None:
            elapsed = t - self._last_boundary_t
            if elapsed < self._min_song_seconds:
                return None  # too soon — likely a false boundary

        self._last_boundary_t = t
        old_index = self._index
        self._index = self._next_index()
        self._swap_count += 1
        return self._profiles[self._index]

    def _next_index(self) -> int:
        if self._strategy == "sequential":
            return (self._index + 1) % len(self._profiles)

        elif self._strategy == "random":
            import random
            candidates = [i for i in range(len(self._profiles)) if i != self._index]
            return random.choice(candidates)

        elif self._strategy == "shuffle":
            import random
            if not self._shuffle_deck or self._shuffle_pos >= len(self._shuffle_deck):
                self._shuffle_deck = list(range(len(self._profiles)))
                random.shuffle(self._shuffle_deck)
                # If first pick is same as current, swap with next
                if self._shuffle_deck[0] == self._index and len(self._shuffle_deck) > 1:
                    self._shuffle_deck[0], self._shuffle_deck[1] = (
                        self._shuffle_deck[1], self._shuffle_deck[0]
                    )
                self._shuffle_pos = 0
            idx = self._shuffle_deck[self._shuffle_pos]
            self._shuffle_pos += 1
            return idx

        else:
            raise ValueError(f"Unknown strategy: {self._strategy}")

    def reset(self) -> None:
        """Reset swap state (e.g., on session restart)."""
        self._last_boundary_t = None
        self._swap_count = 0
        self._shuffle_pos = 0
        self._shuffle_deck.clear()
```

**Key design decisions:**

- **min_song_seconds guard (30s):** Song boundary detectors have cooldowns but can still fire on very short "songs" (DJ drops, interstitials). The swapper ignores boundaries that occur less than 30s after the last one, preventing jarring rapid-fire profile switches.
- **Three strategies:** Sequential is predictable and easy to verify. Random adds variety for long sessions. Shuffle guarantees each profile gets airtime before repeating.
- **Never picks same profile twice in a row:** Random filters out current index. Shuffle re-orders if first pick matches current.
- **Stateless boundary type:** The swapper doesn't care if the boundary was silence-gap or crossfade — both are valid swap points. This keeps the interface simple.

---

## Step 1 — CLI flags

**File:** `src/dreamsync/cli.py`

Add to both `govee-live` and `session` subcommands:

| Flag | Type | Default | Description |
|---|---|---|---|
| `--smart-profile-swap` | str (comma-sep) | None | Profile list for boundary-triggered rotation (e.g., `aurora,neon_city,midnight_rave`) |
| `--swap-strategy` | choice | `"sequential"` | Profile selection strategy: `sequential`, `random`, or `shuffle` |
| `--swap-min-song` | float | `30.0` | Minimum seconds between swaps (false trigger guard) |

```python
govee_live.add_argument(
    "--smart-profile-swap",
    type=str,
    default=None,
    metavar="NAME1,NAME2,...",
    help="Swap profiles at song boundaries (comma-separated names/paths).",
)
govee_live.add_argument(
    "--swap-strategy",
    choices=["sequential", "random", "shuffle"],
    default="sequential",
    help="Profile selection strategy for smart swap (default: sequential).",
)
govee_live.add_argument(
    "--swap-min-song",
    type=float,
    default=30.0,
    help="Minimum seconds between profile swaps (default: 30).",
)
```

**Mutual exclusivity:** `--smart-profile-swap` and `--profile-rotation` are mutually exclusive (one is boundary-triggered, the other is timer-triggered). Add a validation check:

```python
if args.smart_profile_swap and args.profile_rotation:
    print("Error: --smart-profile-swap and --profile-rotation are mutually exclusive.")
    return 1
```

A single `--profile` can coexist with `--smart-profile-swap` — it sets the initial profile for the first song.

---

## Step 2 — Live loop integration

**File:** `src/dreamsync/live.py`

### 2a — Accept new parameter

Add `smart_swapper: SmartProfileSwapper | None = None` to `run_live_to_govee()`.

### 2b — Wire into boundary detection

In the boundary handler block (around line 1180), after the existing state resets:

```python
if boundary_type is not None:
    # ... existing resets (bpm, director, mood, effect_cycler, crossfade) ...

    # Smart profile swap
    if smart_swapper is not None and effect_cycler is not None:
        new_profile = smart_swapper.on_boundary(stream_t)
        if new_profile is not None:
            effect_cycler.set_profile(new_profile)
            if debug_mood:
                print(
                    f"[smart-swap] switched to profile: {new_profile.name} "
                    f"(swap #{smart_swapper.swap_count})"
                )
            if telemetry:
                telemetry.write_row({
                    "kind": "profile_swap",
                    "t": stream_t,
                    "profile": new_profile.name,
                    "boundary_type": boundary_type,
                    "swap_count": smart_swapper.swap_count,
                })
```

**Order matters:** The profile swap happens *after* `effect_cycler.reset()` clears stale palette/effect state but *before* the next `update()` picks new colors. This guarantees the first effect of the new song uses the new profile's palettes.

### 2c — Return swap count in summary

Add to the summary dict:

```python
"profile_swaps": smart_swapper.swap_count if smart_swapper else 0,
```

---

## Step 3 — CLI wiring

**File:** `src/dreamsync/cli.py`

In the `govee-live` command handler, after profile rotation setup:

```python
smart_swapper = None
swap_names = getattr(args, "smart_profile_swap", None)
if swap_names:
    from .profile import SmartProfileSwapper, load_profile, resolve_profile_path
    names = [n.strip() for n in swap_names.split(",") if n.strip()]
    swap_profiles = [load_profile(resolve_profile_path(n)) for n in names]
    smart_swapper = SmartProfileSwapper(
        swap_profiles,
        strategy=getattr(args, "swap_strategy", "sequential"),
        min_song_seconds=getattr(args, "swap_min_song", 30.0),
    )
    # Use first profile as the starting profile
    if profile is None:
        profile = smart_swapper.current
    print(f"Smart profile swap: {len(swap_profiles)} profiles, strategy={args.swap_strategy}")
```

Pass `smart_swapper=smart_swapper` to `run_live_to_govee()`.

Same wiring for the `session` command handler.

---

## Step 4 — Session integration

**File:** `src/dreamsync/session.py`

Add `smart_swapper` parameter to `run_session()` and pass through to `run_live_to_govee()`.

For session mode, the `SmartProfileSwapper` is created in `cli.py` (same as govee-live) and passed into `run_session()`. The session's `ProfileWatcher` and `SmartProfileSwapper` are independent — the watcher handles manual YAML edits to the *current* profile, while the swapper changes *which* profile is active.

---

## Step 5 — Telemetry

**File:** `src/dreamsync/telemetry.py`

The `profile_swap` telemetry row (written in Step 2b) is a new row kind. Add it to the song summary:

```python
# In song summary generation:
"profile_swaps_in_song": count of profile_swap rows in this song's data
```

The `inspect_telemetry.py` script should be updated to parse and display `profile_swap` rows.

---

## Step 6 — Tests

**File:** `tests/test_smart_profile_swap.py`

| Test | What it validates |
|---|---|
| `test_sequential_cycles_in_order` | Profiles cycle 0→1→2→0→1→... |
| `test_random_never_repeats` | Random strategy never picks same profile twice in a row |
| `test_shuffle_covers_all` | Shuffle hits every profile before repeating |
| `test_shuffle_no_immediate_repeat` | Shuffle deck reorder avoids repeating current at wrap |
| `test_min_song_guard` | Boundaries < 30s apart are ignored (returns None) |
| `test_min_song_guard_resets` | After guard period, next boundary triggers normally |
| `test_requires_two_profiles` | Constructor raises ValueError with < 2 profiles |
| `test_reset_clears_state` | `.reset()` zeroes swap count and timing |
| `test_swap_count_increments` | Each accepted boundary increments swap_count |
| `test_current_property` | `.current` returns the active profile |
| `test_integration_with_effect_cycler` | Calling `on_boundary()` + `set_profile()` works end-to-end |

---

## Interaction with Existing Systems

| System | Interaction |
|---|---|
| **Song boundary detection** | Smart swap consumes boundary events. Both silence-gap and crossfade boundaries trigger swaps. |
| **EffectCycler** | `set_profile()` is the only touch point. This is the same path used by ProfileRotation and ProfileWatcher — proven thread-safe and stale-state-free. |
| **ProfileRotation** | Mutually exclusive with smart swap. Timer-based rotation ignores musical structure; smart swap aligns with it. |
| **ProfileWatcher** | Independent. Watcher hot-reloads the *current* profile's YAML. Smart swap changes *which* profile is current. Both can run simultaneously (e.g., smart swap picks neon_city, and the user edits neon_city.yaml mid-song). |
| **Mood classifier** | Mood resets on boundary (existing behavior). The new profile's mood-specific palettes take effect immediately. |
| **Telemetry** | New `profile_swap` row kind. Song summaries include swap count. |

---

## Usage Examples

```bash
# Sequential swap: aurora → neon_city → midnight_rave → aurora → ...
python -m dreamsync govee-live \
    --device 10.126.166.180:7:primary:ptreal \
    --duration 600 --debug-mood \
    --smart-profile-swap aurora,neon_city,midnight_rave

# Random swap with crossfade detection
python -m dreamsync govee-live \
    --device 10.126.166.180:7:primary:ptreal \
    --duration 600 --debug-mood --crossfade-detect \
    --smart-profile-swap aurora,neon_city,midnight_rave,ocean_deep,candy \
    --swap-strategy random

# Shuffle swap with shorter guard (20s) in session mode
python -m dreamsync session \
    --config devices.yaml --debug-mood \
    --smart-profile-swap aurora,neon_city,midnight_rave,warm_sunset \
    --swap-strategy shuffle --swap-min-song 20 \
    --health-monitor --health-interval 15

# All 8 built-in profiles in shuffle mode
python -m dreamsync govee-live \
    --device 10.126.166.180:7:primary:ptreal \
    --duration 3600 --debug-mood --crossfade-detect \
    --smart-profile-swap aurora,neon_city,midnight_rave,ocean_deep,candy,warm_sunset,forest_canopy,monochrome \
    --swap-strategy shuffle
```

---

## Implementation Order

| Phase | What | Files | Tests |
|---|---|---|---|
| 1 | `SmartProfileSwapper` class | `profile.py` | `test_smart_profile_swap.py` (11 tests) |
| 2 | CLI flags + validation | `cli.py` | — |
| 3 | Live loop integration (boundary hook + telemetry row) | `live.py` | integration test in `test_smart_profile_swap.py` |
| 4 | Session wiring | `session.py`, `cli.py` | — |
| 5 | Telemetry summary + inspect script update | `telemetry.py`, `scripts/inspect_telemetry.py` | — |

Estimated scope: ~150 lines of new code + ~100 lines of tests. No existing files need structural changes — just new parameters threaded through existing call sites.
