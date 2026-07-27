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


def test_effect_speed_multiplier_is_relative_to_current_cycle_intent():
    cycle_intent = LightingIntent(
        mode=EffectMode.PULSE,
        intensity=0.5,
        speed=0.8,
        bpm=240.0,
        color="#112233",
    )

    updated, _params = apply_runtime_control_to_intent_params(
        cycle_intent,
        {},
        RuntimeControlState(speed_multiplier=0.5),
    )

    assert updated.bpm == 240.0
    assert updated.speed == pytest.approx(0.4)


def test_reactive_effect_bank_tracks_intent_and_explicit_effect_wins():
    motion = LightingIntent(
        mode=EffectMode.MOTION,
        intensity=0.5,
        speed=0.4,
        bpm=120.0,
        color="#112233",
    )
    state = RuntimeControlState(
        effect_bank=("pulse", "wave", "ripple"),
    )

    _next_intent, params = apply_runtime_control_to_intent_params(
        motion,
        {},
        state,
    )
    assert params["_render_mode"] == "wave"
    assert params["runtime_control"]["effect_bank"] == (
        "pulse",
        "wave",
        "ripple",
    )

    explicit = RuntimeControlState(
        render_mode="ripple",
        effect_bank=("pulse", "wave"),
    )
    _next_intent, params = apply_runtime_control_to_intent_params(
        motion,
        {},
        explicit,
    )
    assert params["_render_mode"] == "ripple"
    assert params["spatial_preset"] == "ripple_from_center"


def test_reactive_palette_override_maps_live_director_color():
    intent = LightingIntent(
        mode=EffectMode.AMBIENT,
        intensity=0.5,
        speed=0.4,
        bpm=100.0,
        color="#112233",
    )
    state = RuntimeControlState(
        palette_override=("#ff0000", "#00ff00", "#0000ff"),
    )

    next_intent, _params = apply_runtime_control_to_intent_params(
        intent,
        {},
        state,
    )

    assert next_intent.color in state.palette_override


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
