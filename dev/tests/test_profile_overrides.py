from __future__ import annotations

from dreamsync.profile import MoodEffectEntry, MoodProfileConfig, ProfileConfig, TransitionRule
from dreamsync.profile_overrides import (
    SongPaletteAssignment,
    derive_profile_with_palette_assignment,
)


def _base_profile() -> ProfileConfig:
    moods = {
        "chill": MoodProfileConfig(
            palettes=("calm",),
            effects=(MoodEffectEntry(name="wave_drift", weight=1.0),),
            params={"wave_rate_mult": 0.3},
        ),
        "groove": MoodProfileConfig(
            palettes=("energy",),
            effects=(MoodEffectEntry(name="beat_pulse", weight=1.0),),
            params={},
        ),
        "hype": MoodProfileConfig(
            palettes=("energy",),
            effects=(MoodEffectEntry(name="fast_scroll", weight=1.0),),
            params={},
        ),
        "drop": MoodProfileConfig(
            palettes=("intense",),
            effects=(MoodEffectEntry(name="drop_blast", weight=1.0),),
            params={"pulse_decay": 3.5},
        ),
    }
    return ProfileConfig(
        name="Base",
        palettes={
            "calm": ("#101010", "#202020", "#303030"),
            "energy": ("#404040", "#505050", "#606060"),
            "intense": ("#707070", "#808080", "#909090"),
        },
        moods=moods,
        description="base profile",
        author="test",
        tags=("demo",),
        version=1,
        source_path=None,
        transitions=(
            TransitionRule(from_mood="groove", to_mood="hype", palette="energy"),
        ),
        cycle_interval=20.0,
    )


def test_derive_profile_with_palette_assignment_rewrites_moods_and_transitions():
    assignment = SongPaletteAssignment(
        track_key="track-1",
        palette_name="custom",
        colors=("#abcdef", "#123456", "#654321"),
        source_label="generated",
    )

    derived = derive_profile_with_palette_assignment(_base_profile(), assignment)

    assert derived is not None
    assert derived.palettes["song_assigned"] == assignment.colors
    assert derived.moods["chill"].palettes == ("song_assigned",)
    assert derived.moods["chill"].params["wave_rate_mult"] == 0.3
    assert derived.transitions[0].palette == "song_assigned"
    assert "song-assigned" in derived.tags


def test_derive_profile_with_palette_assignment_without_base_profile():
    assignment = SongPaletteAssignment(
        track_key="track-2",
        palette_name="energy",
        colors=("#0f0f0f", "#1f1f1f", "#2f2f2f"),
        source_label="standalone",
    )

    derived = derive_profile_with_palette_assignment(None, assignment)

    assert derived is not None
    assert derived.name.startswith("builtins__assigned__")
    assert derived.moods["drop"].palettes == ("song_assigned",)
    assert derived.palettes["song_assigned"] == assignment.colors
