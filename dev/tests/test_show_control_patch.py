from __future__ import annotations

from dreamsync.show.control_patch import (
    CueMatch,
    CueOverrideRule,
    ShowControlPatch,
    apply_show_control_patch,
)
from dreamsync.show.models import ShowCue, ShowTimeline


def _cue(t: float, **kwargs) -> ShowCue:
    defaults = dict(
        render_mode="scroll",
        color_palette=("#101010", "#202020", "#303030"),
        intensity=0.5,
        speed=0.4,
        params={},
        transition="cut",
        transition_beats=0,
    )
    defaults.update(kwargs)
    return ShowCue(t=t, **defaults)


def _timeline() -> ShowTimeline:
    cues = (
        _cue(
            0.0,
            params={
                "eq_routes": [{"band": "bass", "when": "dominant"}],
                "instrument_routes": [{"instrument": "vocals", "when": "dominant"}],
                "scene_layers": [
                    {"band": "bass", "layer_weight": 0.7},
                    {"instrument": "vocals", "layer_weight": 0.8},
                ],
            },
        ),
        _cue(8.0, render_mode="pulse", transition="fade", transition_beats=2),
    )
    return ShowTimeline(
        song_path="song.mp3",
        duration=16.0,
        bpm=120.0,
        time_signature=4,
        beat_times=(0.0, 0.5, 1.0, 1.5),
        downbeat_times=(0.0,),
        cues=cues,
        metadata={"track_name": "Test"},
    )


def test_apply_show_control_patch_overrides_targeted_cue():
    timeline = _timeline()
    patch = ShowControlPatch(
        name="song-tune",
        rules=(
            CueOverrideRule(
                match=CueMatch(cue_index=1),
                palette=("#abcdef", "#123456", "#654321"),
                render_mode="gradient",
                intensity_mult=1.2,
                intensity_offset=0.1,
                speed_mult=0.5,
                params_update={"spatial_preset": "blend_left_to_right"},
                transition="fade",
                transition_beats=4,
            ),
        ),
    )

    result = apply_show_control_patch(timeline, patch)
    cue = result.cues[1]
    assert cue.render_mode == "gradient"
    assert cue.color_palette[0] == "#abcdef"
    assert cue.intensity == 0.7
    assert cue.speed == 0.2
    assert cue.params["spatial_preset"] == "blend_left_to_right"
    assert cue.transition_beats == 4
    assert result.metadata["control_patch_name"] == "song-tune"


def test_apply_show_control_patch_can_disable_route_groups_and_filter_layers():
    timeline = _timeline()
    patch = ShowControlPatch(
        name="mute-bass",
        rules=(
            CueOverrideRule(
                match=CueMatch(has_eq_band="bass"),
                disable_bands=("bass",),
                disable_instrument_routes=True,
                color_bias="#ffeeaa",
            ),
        ),
    )

    result = apply_show_control_patch(timeline, patch)
    cue = result.cues[0]
    assert cue.color_palette[0] == "#ffeeaa"
    assert cue.params["eq_routes"] == []
    assert cue.params["instrument_routes"] == []
    assert cue.params["active_instrument_routes"] == []
    assert cue.params["scene_layers"] == [{"instrument": "vocals", "layer_weight": 0.8}]
