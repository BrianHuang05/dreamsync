from __future__ import annotations

import pytest

from dreamsync.director import EffectMode
from dreamsync.live import resolve_live_render_mode
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
