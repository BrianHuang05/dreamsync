from __future__ import annotations

import numpy as np

from dreamsync.dsp.harmonic import LiveHarmonicState
from dreamsync.dsp.meter import LiveMeterState
from dreamsync.effects import EffectCycler
from dreamsync.prediction.runtime import (
    LivePredictiveRuntime,
    PredictiveRuntimeConfig,
)


def _feed_beat(
    runtime: LivePredictiveRuntime,
    beat_index: int,
    *,
    timbre: str,
) -> None:
    position = beat_index % 4
    if timbre == "A":
        magnitude = np.linspace(1.0, 0.0, 1025)
        bands = (0.2, 0.3, 0.5)
        onset = (0.2, 0.8, 0.2, 0.8)[position]
    else:
        magnitude = np.linspace(0.0, 1.0, 1025)
        bands = (0.75, 0.15, 0.10)
        onset = (1.0, 0.0, 1.0, 0.0)[position]
    t = beat_index * 0.5
    runtime.observe_committed(
        t=t,
        meter_state=LiveMeterState(
            t=t,
            beat=True,
            downbeat=position == 0,
            bar_phase=position,
            phase_confidence=0.95,
            meter_confident=True,
        ),
        harmonic_state=LiveHarmonicState(
            t=t,
            chroma=(1.0,) + (0.0,) * 11,
            tonal_confidence=0.9,
            chord=None,
            chord_confidence=0.0,
            novelty=0.0,
        ),
        absolute_chord=None,
        chord_change=False,
        energy=0.4,
        onset_density=onset,
        spectral_centroid=900.0 if timbre == "A" else 4200.0,
        bpm=120.0,
        enabled_effects=(
            "gradient_flow",
            "fast_scroll",
            "wave_drift",
            "color_scroll",
        ),
        magnitude=magnitude,
        band_ratios=bands,
        band_fluxes=tuple(value * onset for value in bands),
        bass_ratio=0.3,
    )


def test_real_similarity_prediction_applies_effect_only_on_target_downbeat() -> None:
    runtime = LivePredictiveRuntime(
        PredictiveRuntimeConfig(
            structure_similarity_enabled=True,
            structure_similarity_shadow_mode=False,
            cues_enabled=True,
            high_impact_cues_enabled=True,
            cue_prepare_threshold=0.30,
            cue_schedule_threshold=0.45,
            cue_high_impact_threshold=0.45,
            cue_cooldown_seconds=0.0,
            allowed_cue_classes=(
                "bar_marker",
                "phrase_reset",
                "section_recall",
                "section_transition",
            ),
            structure_section_actions_enabled=True,
            structure_section_threshold=0.50,
        )
    )
    # A A | B B | A, followed by the remaining bar before the learned A -> B
    # boundary.  Every harmonic label is deliberately absent.
    section_by_bar = ("A", "A", "B", "B", "A", "A")
    for beat_index in range(24):
        _feed_beat(
            runtime,
            beat_index,
            timbre=section_by_bar[beat_index // 4],
        )
    scheduled = tuple(
        cue
        for cue in runtime.cue_policy.active
        if cue.cue_class == "section_transition"
    )
    assert scheduled
    cue = scheduled[0]
    assert cue.target is not None
    assert cue.target.target_beat_index == 24
    assert runtime.commit_due_cues(
        now_t=cue.target.target_t,
        beat_index=23,
        bar_index=5,
        downbeat=False,
        meter_confident=True,
    ) == ()

    _feed_beat(runtime, 24, timbre="B")
    committed = runtime.commit_due_cues(
        now_t=12.0,
        beat_index=24,
        bar_index=6,
        downbeat=True,
        meter_confident=True,
    )
    section_cue = next(
        item for item in committed if item.cue_class == "section_transition"
    )
    cycler = EffectCycler(seed=12)
    result = runtime.structural_actuator.apply(
        section_cue,
        now_t=12.0,
        beat_index=24,
        bar_index=6,
        downbeat=True,
        meter_confident=True,
        effect_cycler=cycler,
        enabled_effects=("gradient_flow", "fast_scroll"),
    )
    assert result.outcome == "applied"
    assert result.requested_effect == result.applied_effect
    assert result.applied_effect == cycler.current_effect
    assert runtime.structure_observation is not None
    assert runtime.structure_observation.chord_label is None
