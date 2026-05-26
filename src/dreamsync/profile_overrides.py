"""Helpers for song-scoped palette assignments and derived profiles."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from dreamsync.playlist import content_hash_track_id
from dreamsync.profile import MoodProfileConfig, ProfileConfig, TransitionRule, VALID_MOODS


@dataclass(frozen=True)
class SongPaletteAssignment:
    """Persisted palette assignment for one track."""

    track_key: str
    palette_name: str
    colors: tuple[str, ...]
    source_label: str = ""
    source_profile_path: str = ""
    generated_seed: int | None = None

    @property
    def summary_label(self) -> str:
        source = self.source_label.strip() or "custom"
        palette = self.palette_name.strip() or "palette"
        return f"{source} / {palette}"

    def to_data(self) -> dict[str, object]:
        return {
            "track_key": self.track_key,
            "palette_name": self.palette_name,
            "colors": list(self.colors),
            "source_label": self.source_label,
            "source_profile_path": self.source_profile_path,
            "generated_seed": self.generated_seed,
        }

    @classmethod
    def from_data(cls, raw: dict[str, object]) -> "SongPaletteAssignment":
        return cls(
            track_key=str(raw.get("track_key", "")),
            palette_name=str(raw.get("palette_name", "")),
            colors=tuple(str(color) for color in raw.get("colors", ())),
            source_label=str(raw.get("source_label", "")),
            source_profile_path=str(raw.get("source_profile_path", "")),
            generated_seed=(
                int(raw["generated_seed"])
                if raw.get("generated_seed") is not None
                else None
            ),
        )


def track_key_for_path(track_path: Path | str) -> str:
    """Return a stable queue/assignment key for a local track path."""

    path = Path(track_path)
    resolved = path.expanduser().resolve()
    try:
        if resolved.exists() and resolved.is_file():
            path_hash = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:8]
            return f"{content_hash_track_id(resolved)}_{path_hash}"
    except OSError:
        pass
    return f"path:{resolved}".lower()


def derive_profile_with_palette_assignment(
    base_profile: ProfileConfig | None,
    assignment: SongPaletteAssignment | None,
) -> ProfileConfig | None:
    """Return a deterministic per-song profile override for a palette assignment."""

    if assignment is None or not assignment.colors:
        return base_profile

    override_palette_name = "song_assigned"
    color_hash = hashlib.sha256(",".join(assignment.colors).encode("utf-8")).hexdigest()[:8]
    base_name = base_profile.name if base_profile is not None else "builtins"

    if base_profile is None:
        moods = {
            mood: MoodProfileConfig(
                palettes=(override_palette_name,),
                effects=(),
                params={},
            )
            for mood in VALID_MOODS
        }
        return ProfileConfig(
            name=f"{base_name}__assigned__{color_hash}",
            palettes={override_palette_name: tuple(assignment.colors)},
            moods=moods,
            description=f"Song palette override: {assignment.summary_label}",
            author="dreamsync-gui",
            tags=("song-assigned",),
            version=1,
            source_path=None,
            transitions=(),
            cycle_interval=None,
        )

    moods: dict[str, MoodProfileConfig] = {}
    for mood in VALID_MOODS:
        mood_cfg = base_profile.moods.get(mood)
        if mood_cfg is None:
            moods[mood] = MoodProfileConfig(
                palettes=(override_palette_name,),
                effects=(),
                params={},
            )
            continue
        moods[mood] = MoodProfileConfig(
            palettes=(override_palette_name,),
            effects=mood_cfg.effects,
            params=dict(mood_cfg.params),
        )

    transitions = tuple(
        TransitionRule(
            from_mood=rule.from_mood,
            to_mood=rule.to_mood,
            palette=override_palette_name,
        )
        for rule in base_profile.transitions
    )
    tags = tuple(dict.fromkeys((*base_profile.tags, "song-assigned")))

    return ProfileConfig(
        name=f"{base_profile.name}__assigned__{color_hash}",
        palettes={**base_profile.palettes, override_palette_name: tuple(assignment.colors)},
        moods=moods,
        description=base_profile.description,
        author=base_profile.author,
        tags=tags,
        version=base_profile.version,
        source_path=None,
        transitions=transitions,
        cycle_interval=base_profile.cycle_interval,
    )
