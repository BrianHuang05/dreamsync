"""Color profile system for DreamSync.

Profiles are YAML files that define custom palettes, per-mood effect pools,
parameter overrides, and transition rules.  They are loaded into a frozen
ProfileConfig dataclass and injected into EffectCycler at runtime.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Valid mood names (must match Mood enum values)
# ---------------------------------------------------------------------------
VALID_MOODS = frozenset({"chill", "groove", "hype", "drop"})

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# ---------------------------------------------------------------------------
# Built-in profiles directory
# ---------------------------------------------------------------------------
BUILTIN_PROFILES_DIR = Path(__file__).parent / "profiles"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MoodEffectEntry:
    """One weighted effect entry within a mood's effect pool."""
    name: str
    weight: float


@dataclass(frozen=True)
class MoodProfileConfig:
    """Per-mood configuration within a profile."""
    palettes: tuple[str, ...]
    effects: tuple[MoodEffectEntry, ...] = ()
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TransitionRule:
    """Forced palette on a specific mood transition."""
    from_mood: str
    to_mood: str
    palette: str


@dataclass(frozen=True)
class ProfileConfig:
    """Full runtime profile — immutable after load."""
    name: str
    palettes: dict[str, tuple[str, ...]]
    moods: dict[str, MoodProfileConfig]
    description: str = ""
    author: str = ""
    tags: tuple[str, ...] = ()
    version: int = 1
    source_path: Path | None = None
    transitions: tuple[TransitionRule, ...] = ()
    cycle_interval: float | None = None


# ---------------------------------------------------------------------------
# Loader / validator
# ---------------------------------------------------------------------------

class ProfileError(Exception):
    """Raised when a profile YAML is invalid."""


def _validate_hex_color(color: str, context: str) -> None:
    if not _HEX_RE.match(color):
        raise ProfileError(f"Invalid hex color {color!r} in {context}")


def load_profile(path: Path) -> ProfileConfig:
    """Parse a YAML profile file, validate, and return a frozen ProfileConfig."""
    import yaml

    path = Path(path)
    if not path.exists():
        raise ProfileError(f"Profile file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ProfileError(f"Profile must be a YAML mapping, got {type(raw).__name__}")

    # --- name ---
    name = raw.get("name")
    if not name or not isinstance(name, str):
        raise ProfileError("Profile 'name' is required and must be a non-empty string")

    # --- version ---
    version = raw.get("version", 1)
    if version != 1:
        raise ProfileError(f"Unsupported profile version: {version} (expected 1)")

    # --- optional metadata ---
    description = raw.get("description", "")
    author = raw.get("author", "")
    tags_raw = raw.get("tags", [])
    if not isinstance(tags_raw, list):
        raise ProfileError("'tags' must be a list of strings")
    tags = tuple(str(t) for t in tags_raw)

    # --- palettes ---
    palettes_raw = raw.get("palettes", {})
    if not isinstance(palettes_raw, dict):
        raise ProfileError("'palettes' must be a mapping of palette_name -> [hex colors]")

    palettes: dict[str, tuple[str, ...]] = {}
    for pal_name, colors in palettes_raw.items():
        if not isinstance(colors, list) or not (3 <= len(colors) <= 8):
            raise ProfileError(
                f"Palette '{pal_name}' must have 3-8 colors, got {len(colors) if isinstance(colors, list) else type(colors).__name__}"
            )
        for c in colors:
            _validate_hex_color(c, f"palette '{pal_name}'")
        palettes[pal_name] = tuple(colors)

    # --- moods ---
    moods_raw = raw.get("moods", {})
    if not isinstance(moods_raw, dict):
        raise ProfileError("'moods' must be a mapping")

    # Lazy import to avoid circular dependency at module level
    from dreamsync.effects import EFFECTS, PALETTES

    moods: dict[str, MoodProfileConfig] = {}
    for mood_key, mood_val in moods_raw.items():
        if mood_key not in VALID_MOODS:
            raise ProfileError(
                f"Invalid mood '{mood_key}', must be one of {sorted(VALID_MOODS)}"
            )
        if not isinstance(mood_val, dict):
            raise ProfileError(f"Mood '{mood_key}' config must be a mapping")

        # palettes (required per mood)
        mood_palettes = mood_val.get("palettes", [])
        if not mood_palettes:
            raise ProfileError(f"Mood '{mood_key}' must have at least one palette")
        for pref in mood_palettes:
            if pref not in palettes and pref not in PALETTES:
                raise ProfileError(
                    f"Mood '{mood_key}' references unknown palette '{pref}'"
                )

        # effects (optional)
        effects_raw = mood_val.get("effects", [])
        effects: list[MoodEffectEntry] = []
        for e in effects_raw:
            if not isinstance(e, dict) or "name" not in e:
                raise ProfileError(
                    f"Each effect in mood '{mood_key}' must have a 'name' key"
                )
            ename = e["name"]
            if ename not in EFFECTS:
                raise ProfileError(
                    f"Mood '{mood_key}' references unknown effect '{ename}'"
                )
            weight = float(e.get("weight", 1.0))
            effects.append(MoodEffectEntry(name=ename, weight=weight))

        # params (optional)
        params = mood_val.get("params", {})
        if not isinstance(params, dict):
            raise ProfileError(f"Mood '{mood_key}' params must be a mapping")

        moods[mood_key] = MoodProfileConfig(
            palettes=tuple(mood_palettes),
            effects=tuple(effects),
            params=dict(params),
        )

    # Ensure all 4 moods are defined
    missing = VALID_MOODS - set(moods.keys())
    if missing:
        raise ProfileError(f"Profile must define all moods; missing: {sorted(missing)}")

    # --- transitions (optional) ---
    transitions_raw = raw.get("transitions", [])
    transitions: list[TransitionRule] = []
    for tr in transitions_raw:
        if not isinstance(tr, dict):
            raise ProfileError("Each transition must be a mapping with 'from', 'to', 'palette'")
        from_mood = tr.get("from", "")
        to_mood = tr.get("to", "")
        pal = tr.get("palette", "")
        if from_mood not in VALID_MOODS:
            raise ProfileError(f"Transition 'from' mood '{from_mood}' is invalid")
        if to_mood not in VALID_MOODS:
            raise ProfileError(f"Transition 'to' mood '{to_mood}' is invalid")
        if pal not in palettes and pal not in PALETTES:
            raise ProfileError(f"Transition palette '{pal}' not found")
        transitions.append(TransitionRule(from_mood=from_mood, to_mood=to_mood, palette=pal))

    # --- cycle_interval (optional) ---
    cycle_interval = raw.get("cycle_interval", None)
    if cycle_interval is not None:
        cycle_interval = float(cycle_interval)
        if cycle_interval < 1.0:
            raise ProfileError("cycle_interval must be >= 1.0")

    return ProfileConfig(
        name=name,
        description=description,
        author=author,
        tags=tags,
        version=version,
        source_path=path,
        palettes=palettes,
        moods=moods,
        transitions=tuple(transitions),
        cycle_interval=cycle_interval,
    )


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def resolve_profile_path(name_or_path: str) -> Path:
    """Resolve a profile name or path to an actual file.

    Resolution order:
    1. Explicit path (if file exists)
    2. CWD with .yaml extension
    3. Built-in profiles directory
    """
    p = Path(name_or_path)
    if p.exists():
        return p
    # Try CWD with .yaml
    cwd_yaml = Path.cwd() / f"{name_or_path}.yaml"
    if cwd_yaml.exists():
        return cwd_yaml
    # Try built-in
    builtin = BUILTIN_PROFILES_DIR / f"{name_or_path}.yaml"
    if builtin.exists():
        return builtin
    raise ProfileError(
        f"Could not resolve profile '{name_or_path}'. "
        f"Searched: {p}, {cwd_yaml}, {builtin}"
    )


def list_available_profiles() -> list[dict[str, Any]]:
    """Scan built-in profiles directory and return metadata for each."""
    import yaml

    results: list[dict[str, Any]] = []
    if not BUILTIN_PROFILES_DIR.is_dir():
        return results

    for f in sorted(BUILTIN_PROFILES_DIR.glob("*.yaml")):
        if f.name.startswith("_"):
            continue
        try:
            with open(f, "r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh)
            results.append({
                "name": raw.get("name", f.stem),
                "file": f.name,
                "description": raw.get("description", ""),
                "tags": raw.get("tags", []),
            })
        except Exception:
            _logger.warning("Could not read profile %s", f)
    return results


# ---------------------------------------------------------------------------
# Smart features
# ---------------------------------------------------------------------------

def suggest_profile(hour: int | None = None) -> str:
    """Suggest a built-in profile based on time of day.

    Returns the profile name (stem) suitable for resolve_profile_path().
    """
    if hour is None:
        from datetime import datetime
        hour = datetime.now().hour

    if 6 <= hour < 10:
        return "warm_sunset"
    elif 10 <= hour < 16:
        return "ocean_deep"
    elif 16 <= hour < 20:
        return "forest_canopy"
    elif 20 <= hour < 23:
        return "midnight_rave"
    else:
        return "neon_city"


def validate_color_harmony(colors: list[str] | tuple[str, ...]) -> list[str]:
    """Check a palette for potential harmony issues.

    Returns a list of warning strings (empty = no issues).
    """
    warnings: list[str] = []
    if len(colors) < 2:
        return warnings

    # Parse to RGB
    rgbs = []
    for c in colors:
        h = c.lstrip("#")
        rgbs.append((int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)))

    # Check adjacent colors too similar (Euclidean RGB distance < 30)
    for i in range(len(rgbs) - 1):
        r1, g1, b1 = rgbs[i]
        r2, g2, b2 = rgbs[i + 1]
        dist = ((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) ** 0.5
        if dist < 30:
            warnings.append(
                f"Colors {colors[i]} and {colors[i+1]} are very similar (distance={dist:.1f})"
            )

    # Check all colors same perceived brightness (ITU-R BT.601)
    lumas = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in rgbs]
    if len(lumas) >= 2:
        luma_range = max(lumas) - min(lumas)
        if luma_range < 20:
            warnings.append(
                f"All colors have similar brightness (range={luma_range:.1f}), may lack contrast"
            )

    return warnings


# ---------------------------------------------------------------------------
# ProfileRotation
# ---------------------------------------------------------------------------

class ProfileRotation:
    """Rotates through a list of profiles on a timer."""

    def __init__(
        self,
        profiles: list[ProfileConfig],
        interval_seconds: float = 300.0,
    ) -> None:
        if not profiles:
            raise ValueError("ProfileRotation requires at least one profile")
        self._profiles = profiles
        self._interval = interval_seconds
        self._index = 0
        self._last_switch_t: float | None = None

    @property
    def current(self) -> ProfileConfig:
        return self._profiles[self._index]

    def update(self, t: float) -> ProfileConfig | None:
        """Check if it's time to rotate. Returns new profile or None."""
        if self._last_switch_t is None:
            self._last_switch_t = t
            return None

        if (t - self._last_switch_t) >= self._interval:
            self._index = (self._index + 1) % len(self._profiles)
            self._last_switch_t = t
            return self._profiles[self._index]

        return None


# ---------------------------------------------------------------------------
# ProfileWatcher
# ---------------------------------------------------------------------------

class ProfileWatcher:
    """Watches a profile YAML for changes and hot-reloads it.

    Uses mtime-polling on a daemon thread (same pattern as ConfigWatcher).
    Thread-safe because set_profile() is a single reference assignment under GIL.
    """

    def __init__(
        self,
        profile_path: Path,
        on_change_callback: Any,
        *,
        poll_interval: float = 2.0,
    ) -> None:
        self._path = Path(profile_path)
        self._callback = on_change_callback
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_mtime: float = 0.0

        try:
            self._last_mtime = self._path.stat().st_mtime
        except OSError:
            pass

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._watch_loop, name="profile-watcher", daemon=True
        )
        self._thread.start()
        _logger.info("Profile watcher started for %s", self._path)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        _logger.info("Profile watcher stopped")

    def _watch_loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(self._poll_interval)
            if self._stop_event.is_set():
                break
            try:
                mtime = self._path.stat().st_mtime
            except OSError:
                continue

            if mtime <= self._last_mtime:
                continue

            self._last_mtime = mtime
            _logger.info("Profile change detected, reloading %s", self._path.name)

            try:
                new_profile = load_profile(self._path)
                self._callback(new_profile)
            except Exception as exc:
                _logger.warning("Profile reload failed: %s", exc)
