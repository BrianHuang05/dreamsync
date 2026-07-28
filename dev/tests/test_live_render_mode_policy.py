from __future__ import annotations

import random

import pytest

from dreamsync.director import EffectMode
from dreamsync.live import (
    _live_auto_effect_origin,
    _live_auto_effect_speed_beats,
    _live_auto_render_mode,
    _live_effect_decay_seconds,
    _live_effect_diagnostic,
    _apply_live_effect_presentation,
    _resolve_live_effect_presentation_choices,
    resolve_live_render_mode,
)
from dreamsync.render import RenderMode


@pytest.mark.parametrize(
    ("intent_mode", "expected"),
    (
        (EffectMode.AMBIENT, "solid"),
        (EffectMode.PULSE, "pulse"),
        (EffectMode.MOTION, "wave"),
        (EffectMode.RIPPLE, "ripple"),
    ),
)
def test_adaptive_live_render_mode_follows_director(
    intent_mode: EffectMode,
    expected: str,
) -> None:
    assert resolve_live_render_mode(intent_mode) == expected


def test_adaptive_live_render_mode_prefers_effect_preset() -> None:
    assert (
        resolve_live_render_mode(
            EffectMode.PULSE,
            preset_mode=RenderMode.BREATHE,
        )
        == "breathe"
    )


@pytest.mark.parametrize("intent_mode", tuple(EffectMode))
def test_fixed_live_render_mode_ignores_director_and_preset(
    intent_mode: EffectMode,
) -> None:
    assert (
        resolve_live_render_mode(
            intent_mode,
            policy="fixed",
            configured_mode="scroll",
            preset_mode=RenderMode.PULSE,
        )
        == "scroll"
    )


def test_structural_preset_overrides_fixed_live_render_mode() -> None:
    assert (
        resolve_live_render_mode(
            EffectMode.MOTION,
            policy="fixed",
            configured_mode="wave",
            preset_mode=RenderMode.WAVE,
            structural_mode=RenderMode.BREATHE,
        )
        == "breathe"
    )


def test_fixed_live_render_mode_rejects_invalid_mode() -> None:
    with pytest.raises(ValueError):
        resolve_live_render_mode(
            EffectMode.AMBIENT,
            policy="fixed",
            configured_mode="not-a-renderer",
        )


def test_live_render_mode_rejects_unknown_policy() -> None:
    with pytest.raises(ValueError, match="render_mode_policy"):
        resolve_live_render_mode(EffectMode.AMBIENT, policy="surprise")


def test_live_pulse_diagnostic_reports_exponential_decay_lifetime() -> None:
    decay = _live_effect_decay_seconds("pulse", {"pulse_decay": 4.0})

    assert decay == pytest.approx(0.7489, abs=0.0001)
    diagnostic = _live_effect_diagnostic(
        effect_name="drop_blast",
        render_mode="pulse",
        source="structural action",
        trigger_t=10.0,
        now_t=10.25,
        params={"pulse_decay": 4.0},
    )
    assert diagnostic["effect"] == "drop_blast"
    assert diagnostic["render_mode"] == "pulse"
    assert diagnostic["decay_seconds"] == pytest.approx(0.7489, abs=0.0001)
    assert diagnostic["remaining_seconds"] == pytest.approx(0.4989, abs=0.0001)
    assert diagnostic["continuous"] is False


def test_live_continuous_effect_diagnostic_does_not_invent_decay() -> None:
    diagnostic = _live_effect_diagnostic(
        effect_name="wave_drift",
        render_mode="wave",
        source="effect bank",
        trigger_t=3.0,
        now_t=8.0,
        params={"wave_rate_mult": 0.5},
    )

    assert diagnostic["decay_seconds"] is None
    assert diagnostic["remaining_seconds"] is None
    assert diagnostic["continuous"] is True


def test_live_effect_presentation_randomization_is_resolved_once() -> None:
    speed, origin = _resolve_live_effect_presentation_choices(
        "random",
        "random",
        rng=random.Random(7),
    )

    assert speed in {1, 2, 4, 8}
    assert origin in {
        "left",
        "center",
        "outer",
        "right",
        "top",
        "bottom",
        "back",
        "front",
    }


@pytest.mark.parametrize(
    ("energy", "stability", "expected"),
    (
        (0.10, 1.0, 8),
        (0.30, 1.0, 4),
        (0.55, 1.0, 2),
        (0.90, 1.0, 1),
        (0.90, 0.10, 2),
    ),
)
def test_live_auto_effect_speed_tracks_energy_and_beat_stability(
    energy: float,
    stability: float,
    expected: int,
) -> None:
    assert _live_auto_effect_speed_beats(energy, stability) == expected


def test_live_auto_origin_uses_effect_family_and_section() -> None:
    assert _live_auto_effect_origin("ripple", section_index=0) == "center"
    assert _live_auto_effect_origin("ripple", section_index=1) == "outer"
    assert _live_auto_effect_origin("wave", section_index=0) == "top"
    assert _live_auto_effect_origin("wave", section_index=1) == "left"
    assert _live_auto_effect_origin("wave", section_index=2) == "right"


def test_live_auto_renderer_alternates_wave_and_ripple_by_section() -> None:
    enabled = ("wave", "ripple", "pulse")

    assert _live_auto_render_mode("wave", enabled, section_index=0) == "ripple"
    assert _live_auto_render_mode("wave", enabled, section_index=1) == "wave"
    assert (
        _live_auto_render_mode("wave", ("ripple",), section_index=1)
        == "ripple"
    )
    assert _live_auto_render_mode("pulse", enabled, section_index=0) == "pulse"


def test_live_effect_auto_presentation_resolves_informed_values() -> None:
    speed, origin = _resolve_live_effect_presentation_choices(
        "auto",
        "auto",
        rng=random.Random(7),
        effect_mode="wave",
        energy=0.8,
        stability=0.9,
        section_index=1,
    )

    assert speed == 1
    assert origin == "left"


def test_live_effect_presentation_overrides_all_spatial_layers() -> None:
    params = _apply_live_effect_presentation(
        {
            "spatial_preset": "wave_front_to_back",
            "scene_layers": [
                {
                    "band": "bass",
                    "spatial_preset": "flash_floor_only",
                    "spatial_origin": {"x": 1.0, "y": 0.0, "z": 0.0},
                },
            ],
        },
        speed_beats=4,
        origin="center",
        bpm=120.0,
    )

    assert params["_effect_speed_beats"] == 4
    assert params["_effect_speed_bpm"] == 120.0
    assert params["spatial_mode"] == "emanation"
    assert params["spatial_origin"] == {"x": 0.0, "y": 0.0, "z": 0.0}
    assert params["layer_category"] == "expand"
    assert "spatial_preset" not in params
    assert params["scene_layers"][0]["spatial_mode"] == "emanation"
    assert params["scene_layers"][0]["spatial_origin"] == {
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
    }
    assert "spatial_preset" not in params["scene_layers"][0]


def test_live_pulse_decay_uses_selected_beat_length() -> None:
    assert _live_effect_decay_seconds(
        "pulse",
        {
            "_effect_speed_beats": 4,
            "_effect_speed_bpm": 120.0,
            "pulse_decay": 99.0,
        },
    ) == 2.0
