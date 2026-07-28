from __future__ import annotations

import pytest

from dreamsync.director import Director, EffectMode, LightingIntent
from dreamsync.effects import EffectCycler
from dreamsync.live import refresh_frame_intent_palette
from dreamsync.output.govee_lan import MultiGoveeLanAdapter
from dreamsync.output.null_adapter import (
    PreviewMirrorAdapter,
    SimulationMultiAdapter,
    _PreviewDeviceAdapter,
)
from dreamsync.output.roles import DeviceRole
from dreamsync.prediction.models import CueProposal
from dreamsync.prediction.runtime import LivePredictiveRuntime, PredictiveRuntimeConfig
from dreamsync.prediction.structure_models import StructuralCueTarget
from dreamsync.prediction.visual_actuator import (
    StructuralActuatorConfig,
    StructuralVisualActuator,
)
from dreamsync.render import (
    OPTICAL_RGB_PEAK_FLOOR,
    PULSE_MIN_BRIGHTNESS_FLOOR,
    RenderMode,
    SegmentRenderer,
)


def _intent(*, color: str = "#ef240c", intensity: float = 0.8) -> LightingIntent:
    return LightingIntent(
        mode=EffectMode.PULSE,
        intensity=intensity,
        speed=0.7,
        bpm=120.0,
        color=color,
    )


@pytest.mark.parametrize("mode", tuple(RenderMode))
def test_base_render_modes_hold_palette_contract_for_sixty_seconds(
    mode: RenderMode,
) -> None:
    renderer = SegmentRenderer(segments=9, mode=mode)
    params = {
        "beat_accent": 0.55,
        "gradient_colors": ("#ef240c", "#1436e8"),
    }
    frames = []
    for frame_index in range(60 * 30):
        t = frame_index / 30.0
        frames.append(
            renderer.render(
                t,
                _intent(),
                beat=frame_index % 15 == 0,
                params=params,
            )
        )

    assert all(any(max(pixel) > 0 for pixel in frame) for frame in frames)
    assert all(
        pixel not in {(255, 255, 255), (254, 254, 254)}
        for frame in frames
        for pixel in frame
    )


def test_pulse_has_hue_preserving_floor_and_distinct_beat_targets() -> None:
    renderer = SegmentRenderer(segments=1, mode=RenderMode.PULSE)
    ordinary = renderer.render(
        1.0,
        _intent(color="#ff2000", intensity=1.0),
        beat=True,
        params={"beat_accent": 0.45, "pulse_floor": 0.0},
    )[0]
    decayed = renderer.render(
        3.0,
        _intent(color="#ff2000", intensity=1.0),
        beat=False,
        params={"pulse_floor": 0.0},
    )[0]
    downbeat = renderer.render(
        3.1,
        _intent(color="#ff2000", intensity=1.0),
        beat=True,
        params={"beat_accent": 1.0},
    )[0]

    assert downbeat[0] > ordinary[0] > decayed[0] > 0
    assert decayed[0] >= int(255 * PULSE_MIN_BRIGHTNESS_FLOOR) - 1
    assert ordinary[0] > ordinary[1] > ordinary[2]
    assert decayed[0] > decayed[1] >= decayed[2]


def test_low_master_brightness_breathe_stays_visible_and_chromatic() -> None:
    renderer = SegmentRenderer(segments=15, mode=RenderMode.BREATHE)
    low_live_intent = LightingIntent(
        mode=EffectMode.AMBIENT,
        intensity=0.15 * 0.20,
        speed=0.22,
        bpm=120.0,
        color="#ffaacc",
    )

    frames = [
        renderer.render(1.0 + (frame_index / 30.0), low_live_intent)[0]
        for frame_index in range(30 * 10)
    ]

    assert all(max(pixel) >= OPTICAL_RGB_PEAK_FLOOR for pixel in frames)
    assert all(max(pixel) - min(pixel) > 1 for pixel in frames)


def test_structure_controlled_director_still_cycles_on_downbeats() -> None:
    director = Director()
    director.set_colors(("#ff0000", "#00ff00", "#0000ff"))

    colors = []
    for beat_index in range(5):
        colors.append(
            director.update(
                {
                    "t": beat_index * 0.5,
                    "rms": 0.1,
                    "zcr": 0.05,
                    "bpm": 120.0,
                    "beat": True,
                    "downbeat": beat_index in {0, 4},
                    "structure_controlled": True,
                    "structure_event": "",
                }
            ).color
        )

    assert colors == [
        "#00ff00",
        "#00ff00",
        "#00ff00",
        "#00ff00",
        "#0000ff",
    ]


def test_frame_trace_is_opt_in_sampled_and_bounded() -> None:
    device = _PreviewDeviceAdapter("trace", name="Trace", segments=1)
    adapter = MultiGoveeLanAdapter(
        [(device, SegmentRenderer(1, RenderMode.SOLID), DeviceRole.PRIMARY, 1.0, None)]
    )
    adapter.configure_frame_trace(enabled=True, max_frames=3, sample_every=2)
    for index in range(10):
        adapter.send_frame(
            index / 30.0,
            _intent(),
            beat=index == 2,
            params={
                "_render_mode": "solid",
                "_frame_provenance": {"stream_t": index / 30.0},
            },
        )

    trace = adapter.frame_trace_snapshot()
    assert len(trace) == 3
    assert [row["stream_t"] for row in trace] == pytest.approx([5 / 30, 7 / 30, 9 / 30])
    assert trace[-1]["devices"][0]["post_spatial_rgb"] == ((191, 28, 9),)


def test_preview_retains_last_valid_color_when_device_frame_is_missing() -> None:
    device = _PreviewDeviceAdapter("preview", name="Preview", segments=1)
    adapter = SimulationMultiAdapter(
        [(device, SegmentRenderer(1), DeviceRole.PRIMARY, 1.0, None)],
        node_keys={"preview": ["preview"]},
    )
    device.last_colors = [(10, 20, 30)]
    assert adapter.preview_snapshot()["node_colors"]["preview"] == "#0a141e"
    device.last_colors = []
    assert adapter.preview_snapshot()["node_colors"]["preview"] == "#0a141e"


def test_hardware_mirror_uses_exact_final_rgb_instead_of_rerendering() -> None:
    hardware_device = _PreviewDeviceAdapter("same", name="Hardware", segments=1)
    hardware = MultiGoveeLanAdapter([
        (
            hardware_device,
            SegmentRenderer(1, RenderMode.SOLID),
            DeviceRole.ACCENT,
            0.5,
            None,
        )
    ])
    preview_device = _PreviewDeviceAdapter("same", name="Preview", segments=1)
    preview = SimulationMultiAdapter(
        [
            (
                preview_device,
                SegmentRenderer(1, RenderMode.WAVE),
                DeviceRole.PRIMARY,
                1.0,
                None,
            )
        ],
        node_keys={"same": ["same"]},
    )
    mirror = PreviewMirrorAdapter(hardware, preview)

    assert mirror.send_frame(1.0, _intent(), params={"_render_mode": "solid"})
    hardware_color = hardware.final_frame_snapshot()["node_colors"]["same"]
    snapshot = mirror.preview_snapshot()
    assert snapshot["node_colors"]["same"] == hardware_color
    assert snapshot["hardware_mirror_parity"]["matches"] is True


def test_structural_palette_commit_refreshes_stale_frame_intent() -> None:
    director = Director()
    stale = _intent(color="#ef240c")
    director.set_colors(("#1436e8", "#ef240c"))

    refreshed = refresh_frame_intent_palette(stale, director)

    assert refreshed.color == "#1436e8"
    assert refreshed.intensity == stale.intensity
    assert refreshed.speed == stale.speed
    assert refreshed.bpm == stale.bpm


def _committed_cue(cue_class: str) -> CueProposal:
    target = StructuralCueTarget(
        target_beat_index=16,
        target_bar_index=4,
        target_t=8.0,
        requires_downbeat=True,
        timing_sigma=0.05,
    )
    return CueProposal(
        cue_id=f"cue-{cue_class}",
        prediction_id=f"prediction-{cue_class}",
        created_t=6.0,
        execute_t=8.0,
        expires_t=8.1,
        cue_class=cue_class,
        effect_candidates=("gradient_flow",),
        color_action=None,
        intensity=0.4,
        confidence=0.9,
        state="committed",
        explanation="deterministic action matrix",
        target=target,
        requested_effect="gradient_flow",
    )


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [
        ((False, False, False), set()),
        ((True, False, False), {"bar_marker"}),
        ((True, True, False), {"bar_marker", "phrase_reset"}),
        (
            (True, True, True),
            {"bar_marker", "phrase_reset", "section_transition"},
        ),
    ],
)
def test_structure_action_tier_matrix(
    enabled: tuple[bool, bool, bool],
    expected: set[str],
) -> None:
    actuator = StructuralVisualActuator(
        StructuralActuatorConfig(
            bar_actions_enabled=enabled[0],
            phrase_actions_enabled=enabled[1],
            section_actions_enabled=enabled[2],
        )
    )
    applied = set()
    for cue_class in ("bar_marker", "phrase_reset", "section_transition"):
        result = actuator.apply(
            _committed_cue(cue_class),
            now_t=8.0,
            beat_index=16,
            bar_index=4,
            downbeat=True,
            meter_confident=True,
            effect_cycler=EffectCycler(seed=4),
            enabled_effects=("gradient_flow",),
        )
        if result.outcome == "applied":
            applied.add(cue_class)
        assert result.cue_class == cue_class
        assert result.target_beat == 16
        assert result.commit_t == 8.0
        assert result.commit_bar == 4
        assert result.downbeat is True
        assert result.meter_confident is True
    assert applied == expected


def test_shadow_and_all_disabled_cancel_without_mutating_continuous_state() -> None:
    runtime = LivePredictiveRuntime(
        PredictiveRuntimeConfig(
            structure_similarity_enabled=True,
            structure_similarity_shadow_mode=False,
            cues_enabled=True,
            structure_bar_actions_enabled=True,
        )
    )
    cycler = EffectCycler(seed=8)
    before = (cycler.current_effect, cycler.current_palette)

    runtime.set_structure_action_controls(
        shadow_mode=True,
        bar_actions=True,
        phrase_actions=True,
        section_actions=True,
    )
    assert not runtime.cue_policy.active
    assert (cycler.current_effect, cycler.current_palette) == before

    runtime.set_structure_action_controls(
        shadow_mode=False,
        bar_actions=False,
        phrase_actions=False,
        section_actions=False,
    )
    assert not runtime.cue_policy.active
    assert (cycler.current_effect, cycler.current_palette) == before
