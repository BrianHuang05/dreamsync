from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from dreamsync.dsp.harmonic import LiveHarmonicState
from dreamsync.dsp.meter import LiveMeterState
from dreamsync.dsp.spectral_features import mfcc_from_magnitude
from dreamsync.prediction.runtime import LivePredictiveRuntime, PredictiveRuntimeConfig
from dreamsync.prediction.structure_models import LiveBeatStructureObservation


def _observation(**changes: object) -> LiveBeatStructureObservation:
    values = {
        "t": 1.0,
        "beat_index": 4,
        "bar_index": 1,
        "beat_in_bar": 0,
        "meter": (4, 4),
        "meter_confidence": 0.8,
        "downbeat": True,
        "beat_period": 0.5,
        "chroma": (1.0,) + (0.0,) * 11,
        "chroma_confidence": 0.7,
        "harmonic_novelty": 0.2,
        "mfcc": tuple(float(index) for index in range(13)),
        "band_ratios": (0.3, 0.7),
        "band_fluxes": (0.1, 0.2),
        "bass_ratio": 0.3,
        "energy": 0.4,
        "onset_strength": 0.5,
        "spectral_centroid": 1200.0,
        "chord_label": "C",
        "chord_confidence": 0.9,
        "key_label": "C major",
        "key_confidence": 0.8,
    }
    values.update(changes)
    return LiveBeatStructureObservation(**values)


def test_tonal_labels_are_optional_sidecars() -> None:
    labelled = _observation()
    cleared = labelled.without_tonal_labels()
    structural_fields = {
        field.name
        for field in dataclasses.fields(labelled)
        if field.name not in {"chord_label", "chord_confidence", "key_label", "key_confidence"}
    }
    assert all(getattr(labelled, name) == getattr(cleared, name) for name in structural_fields)
    assert cleared.chord_label is None
    assert cleared.key_label is None


def test_fixed_vectors_and_non_finite_values_are_rejected() -> None:
    with pytest.raises(ValueError, match="chroma"):
        _observation(chroma=(1.0,) * 11)
    with pytest.raises(ValueError, match="mfcc"):
        _observation(mfcc=(0.0,) * 12)
    with pytest.raises(ValueError, match="finite"):
        _observation(energy=float("nan"))


def test_mfcc_uses_existing_magnitude_spectrum() -> None:
    magnitude = np.linspace(0.0, 1.0, 1025, dtype=np.float64)
    first = mfcc_from_magnitude(magnitude, sample_rate=44100, n_fft=2048)
    second = mfcc_from_magnitude(magnitude, sample_rate=44100, n_fft=2048)
    assert len(first) == 13
    assert np.isfinite(first).all()
    assert first == second


def test_live_runtime_populates_structure_observation_without_label_dependency() -> None:
    runtime = LivePredictiveRuntime(
        PredictiveRuntimeConfig(
            structure_similarity_enabled=True,
            sample_rate=44100,
            spectral_fft_size=2048,
        )
    )
    runtime.observe_committed(
        t=0.0,
        meter_state=LiveMeterState(
            t=0.0,
            beat=True,
            downbeat=True,
            bar_phase=0,
            phase_confidence=0.9,
            meter_confident=True,
        ),
        harmonic_state=LiveHarmonicState(
            t=0.0,
            chroma=(1.0,) + (0.0,) * 11,
            tonal_confidence=0.8,
            chord=None,
            chord_confidence=0.0,
            novelty=0.3,
        ),
        absolute_chord=None,
        chord_change=False,
        energy=0.4,
        onset_density=0.2,
        spectral_centroid=900.0,
        bpm=120.0,
        magnitude=np.ones(1025),
        band_ratios=(0.2, 0.8),
        band_fluxes=(0.1, 0.4),
        bass_ratio=0.3,
    )
    observation = runtime.structure_observation
    assert observation is not None
    assert observation.chord_label is None
    assert observation.key_label is None
    assert len(observation.mfcc) == 13
    assert observation.band_ratios == (0.2, 0.8)


def test_live_runtime_rebuilds_meter_dependent_structure_state():
    runtime = LivePredictiveRuntime(
        PredictiveRuntimeConfig(
            structure_similarity_enabled=True,
            beats_per_bar=3,
        )
    )

    runtime.set_beats_per_bar(4)

    assert runtime.config.beats_per_bar == 4
    assert runtime.structure_bar_builder.beats_per_bar == 4
    assert runtime.structure_engine.beats_per_bar == 4
    assert runtime.diagnostics(now_t=0.0)["structure_configured_meter"] == (
        4,
        4,
    )
