from dreamsync.prediction.models import LiveMusicalObservation
from dreamsync.prediction.momentum import MomentumTrajectoryPredictor


def _observation(index: int, energy: float) -> LiveMusicalObservation:
    return LiveMusicalObservation(
        t=float(index),
        beat_index=index,
        bar_index=index // 4,
        beat_in_bar=index % 4,
        meter=(4, 4),
        meter_confidence=0.9,
        downbeat=index % 4 == 0,
        absolute_chord="C",
        chord_confidence=0.9,
        chord_change=index % 4 == 0,
        chord_duration_beats=4.0,
        chroma=(1.0,) + (0.0,) * 11,
        tonal_confidence=0.9,
        energy=energy,
        energy_delta=0.1,
        onset_density=energy,
        onset_density_delta=0.1,
        spectral_centroid=1000.0 + (energy * 1000.0),
        centroid_delta=100.0,
        harmonic_rhythm=0.25,
        beat_period=0.5,
    )


def test_momentum_predicts_interpretable_rising_axes() -> None:
    predictor = MomentumTrajectoryPredictor()
    result = ()
    for index in range(8):
        result = predictor.observe(
            _observation(index, 0.1 + (index * 0.1)),
            function="I",
            key_stability=0.8,
        )
    axes = {(item.axis, item.direction) for item in result}
    assert ("energy", "rising") in axes
    assert ("brightness", "rising") in axes
    assert all(item.axis != "emotion" for item in result)
