from __future__ import annotations

from dreamsync.dsp.harmonic import LiveHarmonicState
from dreamsync.dsp.meter import LiveMeterState
from dreamsync.prediction.runtime import (
    LivePredictiveRuntime,
    PredictiveRuntimeConfig,
)


def test_shadow_runtime_publishes_bounded_diagnostics_without_visual_output() -> None:
    runtime = LivePredictiveRuntime(
        PredictiveRuntimeConfig(
            analysis_enabled=True,
            diagnostics_enabled=True,
            shadow_mode=True,
            cues_enabled=False,
            max_log_events=12,
        )
    )
    for index, chord in enumerate(("Am", "F", "G", "C") * 3):
        root = {"Am": 9, "F": 5, "G": 7, "C": 0}[chord]
        chroma = [0.0] * 12
        chroma[root] = 1.0
        runtime.observe_committed(
            t=index * 0.5,
            meter_state=LiveMeterState(
                t=index * 0.5,
                beat=True,
                downbeat=index % 4 == 0,
                bar_phase=index % 4,
                phase_confidence=0.9,
                meter_confident=True,
            ),
            harmonic_state=LiveHarmonicState(
                t=index * 0.5,
                chroma=tuple(chroma),
                tonal_confidence=0.9,
                chord=chord,
                chord_confidence=0.9,
                harmonic_change=True,
            ),
            absolute_chord=chord,
            chord_change=True,
            energy=0.3 + (index * 0.01),
            onset_density=0.5,
            spectral_centroid=1200.0,
            bpm=120.0,
        )
    diagnostics = runtime.diagnostics(now_t=6.0)
    assert diagnostics["predictive_shadow_mode"] is True
    assert diagnostics["predictive_key_hypotheses"]
    assert len(runtime.log.snapshot()) <= 12
    assert diagnostics["predictive_cycle"] == {
        "available": True,
        "current_bar": 3,
        "current_beat": 12,
        "cycle_bars": 4,
        "cycle_beats": 16,
        "bars_to_next_cycle": 1,
        "beats_to_next_cycle": 4,
        "beats_since_cycle_start": 11,
        "time_signature": (4, 4),
        "confidence": diagnostics["predictive_cycle"]["confidence"],
    }
    assert not hasattr(runtime, "renderer")
    assert not hasattr(runtime, "adapter")
