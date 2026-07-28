from __future__ import annotations

import pytest

from dreamsync.director import EffectMode
from dreamsync.live import (
    _live_effect_decay_seconds,
    _live_effect_diagnostic,
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
