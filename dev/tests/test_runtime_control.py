from __future__ import annotations

import pytest

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.show.models import ShowCue
from dreamsync.show.runtime_control import (
    RuntimeControlBus,
    RuntimeControlState,
    apply_runtime_control_to_cue,
    apply_runtime_control_to_intent_params,
)


def _cue() -> ShowCue:
    return ShowCue(
        t=0.0,
        render_mode="scroll",
        color_palette=("#111111", "#222222", "#333333"),
        intensity=0.5,
        speed=0.4,
        params={
            "eq_routes": [{"band": "bass", "when": "dominant"}],
            "instrument_routes": [{"instrument": "vocals", "when": "dominant"}],
            "scene_layers": [
                {"band": "bass", "layer_weight": 0.7},
                {"instrument": "vocals", "layer_weight": 0.8},
            ],
        },
        transition="cut",
        transition_beats=0,
    )


def test_apply_runtime_control_to_cue_updates_palette_trim_and_routes():
    cue = _cue()
    state = RuntimeControlState(
        palette_override=("#abcdef", "#123456", "#654321"),
        color_bias="#ffeeaa",
        render_mode="gradient",
        intensity_multiplier=1.2,
        intensity_offset=0.1,
        speed_multiplier=0.5,
        spatial_preset="blend_left_to_right",
        disable_instrument_routes=True,
        muted_bands=("bass",),
    )

    updated = apply_runtime_control_to_cue(cue, state)
    assert updated.render_mode == "gradient"
    assert updated.color_palette[0] == "#ffeeaa"
    assert updated.intensity == 0.7
    assert updated.speed == 0.2
    assert updated.params["spatial_preset"] == "blend_left_to_right"
    assert updated.params["eq_routes"] == []
    assert updated.params["instrument_routes"] == []
    assert updated.params["scene_layers"] == []


def test_apply_runtime_control_to_intent_params_tracks_runtime_state():
    intent = LightingIntent(
        mode=EffectMode.PULSE,
        intensity=0.5,
        speed=0.4,
        bpm=120.0,
        color="#112233",
    )
    state = RuntimeControlState(
        color_bias="#abcdef",
        render_mode="wave",
        intensity_multiplier=1.1,
        speed_offset=0.2,
    )

    next_intent, params = apply_runtime_control_to_intent_params(intent, {"scene_layers": []}, state)
    assert next_intent.color == "#abcdef"
    assert next_intent.intensity == 0.55
    assert next_intent.speed == pytest.approx(0.6)
    assert params["_render_mode"] == "wave"
    assert params["runtime_control"]["active"] is True


def test_runtime_control_bus_update_and_clear_increment_revision():
    bus = RuntimeControlBus()
    initial = bus.snapshot()
    assert initial.revision == 0

    updated = bus.set_render_mode("gradient")
    assert updated.render_mode == "gradient"
    assert updated.revision == 1

    cleared = bus.clear()
    assert cleared.render_mode == ""
    assert cleared.revision == 2
