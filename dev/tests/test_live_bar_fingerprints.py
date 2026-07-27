from __future__ import annotations

import dataclasses

import pytest

from dreamsync.prediction.bar_features import LiveBarFingerprintBuilder
from dreamsync.prediction.structure_models import LiveBeatStructureObservation


def _beat(
    beat_index: int,
    *,
    bar: int,
    position: int,
    chroma_root: int = 0,
    energy: float = 0.4,
    meter_confidence: float = 0.9,
    chord_label: str | None = None,
) -> LiveBeatStructureObservation:
    chroma = [0.0] * 12
    chroma[chroma_root] = 1.0
    return LiveBeatStructureObservation(
        t=beat_index * 0.5,
        beat_index=beat_index,
        bar_index=bar,
        beat_in_bar=position,
        meter=(4, 4) if meter_confidence >= 0.22 else None,
        meter_confidence=meter_confidence,
        downbeat=position == 0,
        beat_period=0.5,
        chroma=tuple(chroma),
        chroma_confidence=0.8,
        harmonic_novelty=0.0,
        mfcc=tuple(float(index + chroma_root) for index in range(13)),
        band_ratios=(0.2, 0.3, 0.5),
        band_fluxes=(0.1 + position, 0.2, 0.3),
        bass_ratio=0.2 + (0.1 * position),
        energy=energy,
        onset_strength=0.2 + (0.1 * position),
        spectral_centroid=1000.0 + (position * 100.0),
        chord_label=chord_label,
    )


def _close_bar(
    builder: LiveBarFingerprintBuilder,
    roots: tuple[int, ...],
    *,
    energy_scale: float = 1.0,
):
    for position, root in enumerate(roots):
        assert (
            builder.observe(
                _beat(
                    position,
                    bar=0,
                    position=position,
                    chroma_root=root,
                    energy=energy_scale * (position + 1),
                )
            )
            is None
        )
    return builder.observe(_beat(4, bar=1, position=0))


def test_downbeat_closes_exactly_one_immutable_bar() -> None:
    builder = LiveBarFingerprintBuilder()
    fingerprint = _close_bar(builder, (0, 2, 4, 5))
    assert fingerprint is not None
    assert fingerprint.bar_index == 0
    assert fingerprint.completeness == pytest.approx(1.0)
    assert len(fingerprint.beat_chroma) == 4
    with pytest.raises(dataclasses.FrozenInstanceError):
        fingerprint.bar_index = 2  # type: ignore[misc]
    assert builder.observe(_beat(4, bar=1, position=0)) is None


def test_incomplete_bar_is_reliability_gated() -> None:
    builder = LiveBarFingerprintBuilder()
    builder.observe(_beat(0, bar=0, position=0))
    builder.observe(_beat(2, bar=0, position=2))
    fingerprint = builder.observe(_beat(4, bar=1, position=0))
    assert fingerprint is not None
    assert fingerprint.completeness == pytest.approx(0.5)
    assert fingerprint.reliability < 0.75


def test_relative_energy_shape_is_loudness_invariant() -> None:
    quiet = _close_bar(LiveBarFingerprintBuilder(), (0, 2, 4, 5), energy_scale=0.1)
    loud = _close_bar(LiveBarFingerprintBuilder(), (0, 2, 4, 5), energy_scale=1.0)
    assert quiet is not None and loud is not None
    assert quiet.energy_shape == pytest.approx(loud.energy_shape, abs=1e-6)


def test_harmonic_motion_and_no_label_are_preserved() -> None:
    forward = _close_bar(LiveBarFingerprintBuilder(), (0, 2, 4, 5))
    reverse = _close_bar(LiveBarFingerprintBuilder(), (5, 4, 2, 0))
    assert forward is not None and reverse is not None
    assert forward.chroma_profile == reverse.chroma_profile
    assert forward.beat_chroma != reverse.beat_chroma
    assert forward.tonal_label is None
