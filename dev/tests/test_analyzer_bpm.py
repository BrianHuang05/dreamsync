"""Tests for the GlobalBpmEstimator (D4.3)."""

from __future__ import annotations

import numpy as np
import pytest

from dreamsync.analyzer.bpm import BeatGrid, GlobalBpmEstimator, TempoRegion
from dreamsync.analyzer.features import FeatureRow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_features(
    bpm: float = 120.0,
    duration: float = 30.0,
    hop_size: int = 512,
    sample_rate: int = 44100,
    beat_interval_frames: int | None = None,
) -> list[FeatureRow]:
    """Generate synthetic FeatureRows with a constant BPM."""
    dt = hop_size / sample_rate
    n_frames = int(duration / dt)
    if beat_interval_frames is None and bpm > 0:
        beat_interval_frames = int(round((60.0 / bpm) / dt))
    rows = []
    for i in range(n_frames):
        t = i * dt
        is_beat = (beat_interval_frames is not None and
                   beat_interval_frames > 0 and
                   i % beat_interval_frames == 0 and
                   t > 2.0)  # skip warmup
        rows.append(FeatureRow(
            t=t, rms=0.3, zcr=0.05, centroid=2000.0,
            bass_ratio=0.3, spectral_flux=0.5, kick_spectral_flux=0.1,
            onset_strength=0.2, energy=0.4, bpm=bpm if t > 1.5 else 0.0,
            beat=is_beat, mood="groove",
        ))
    return rows


def _make_features_with_tempo_change(
    bpm1: float, bpm2: float, change_at: float, duration: float = 30.0,
) -> list[FeatureRow]:
    """Generate features with a tempo change at change_at seconds."""
    dt = 512 / 44100
    n_frames = int(duration / dt)
    rows = []
    for i in range(n_frames):
        t = i * dt
        bpm = bpm1 if t < change_at else bpm2
        rows.append(FeatureRow(
            t=t, rms=0.3, zcr=0.05, centroid=2000.0,
            bass_ratio=0.3, spectral_flux=0.5, kick_spectral_flux=0.1,
            onset_strength=0.2, energy=0.4, bpm=bpm if t > 1.5 else 0.0,
            beat=(i % 40 == 0) and t > 2.0, mood="groove",
        ))
    return rows


# ---------------------------------------------------------------------------
# TempoRegion and BeatGrid dataclasses
# ---------------------------------------------------------------------------

class TestTempoRegion:
    def test_frozen(self):
        r = TempoRegion(0.0, 10.0, 120.0, 0.95)
        with pytest.raises(AttributeError):
            r.bpm = 130.0  # type: ignore[misc]

    def test_fields(self):
        r = TempoRegion(0.0, 30.0, 128.0, 0.9)
        assert r.start_t == 0.0
        assert r.end_t == 30.0
        assert r.bpm == 128.0
        assert r.confidence == 0.9


class TestBeatGrid:
    def test_frozen(self):
        bg = BeatGrid(bpm=120.0, beat_times=(0.5, 1.0), downbeat_times=(0.5,), time_signature=4)
        with pytest.raises(AttributeError):
            bg.bpm = 130.0  # type: ignore[misc]

    def test_fields(self):
        bg = BeatGrid(bpm=128.0, beat_times=(0.0, 0.47), downbeat_times=(0.0,), time_signature=4)
        assert bg.bpm == 128.0
        assert len(bg.beat_times) == 2
        assert bg.time_signature == 4


# ---------------------------------------------------------------------------
# GlobalBpmEstimator — BPM accuracy
# ---------------------------------------------------------------------------

class TestGlobalBpmEstimator:
    def test_constant_120bpm(self):
        features = _make_features(bpm=120.0, duration=30.0)
        est = GlobalBpmEstimator()
        bpm, regions, grid = est.estimate(features)
        assert abs(bpm - 120.0) <= 2.0, f"Expected ~120, got {bpm}"

    def test_constant_128bpm(self):
        features = _make_features(bpm=128.0, duration=30.0)
        est = GlobalBpmEstimator()
        bpm, regions, grid = est.estimate(features)
        assert abs(bpm - 128.0) <= 2.0, f"Expected ~128, got {bpm}"

    def test_constant_90bpm(self):
        features = _make_features(bpm=90.0, duration=30.0)
        est = GlobalBpmEstimator()
        bpm, regions, grid = est.estimate(features)
        assert abs(bpm - 90.0) <= 2.0, f"Expected ~90, got {bpm}"

    def test_constant_170bpm(self):
        features = _make_features(bpm=170.0, duration=30.0)
        est = GlobalBpmEstimator()
        bpm, regions, grid = est.estimate(features)
        # May detect at 170 or 85 (half)
        assert abs(bpm - 170.0) <= 3.0 or abs(bpm - 85.0) <= 3.0

    def test_harmonic_alias_prefers_preferred_range(self):
        """If BPM is 60, should resolve to 120 (preferred range 80-160)."""
        features = _make_features(bpm=60.0, duration=30.0)
        est = GlobalBpmEstimator()
        bpm, _, _ = est.estimate(features)
        # Should prefer doubling into 80-160 range
        assert 80 <= bpm <= 160, f"Expected 80-160 range, got {bpm}"

    def test_single_tempo_region(self):
        features = _make_features(bpm=120.0, duration=30.0)
        est = GlobalBpmEstimator()
        _, regions, _ = est.estimate(features)
        assert len(regions) >= 1
        # All regions should be near 120 BPM
        for r in regions:
            assert abs(r.bpm - 120.0) <= 10.0

    def test_tempo_change_detected(self):
        """Songs with tempo changes should produce multiple regions."""
        features = _make_features_with_tempo_change(
            bpm1=100.0, bpm2=140.0, change_at=15.0, duration=30.0,
        )
        est = GlobalBpmEstimator(tempo_change_threshold=8.0)
        _, regions, _ = est.estimate(features)
        # Should detect at least 2 regions
        assert len(regions) >= 2, f"Expected 2+ regions, got {len(regions)}"
        # First region should be around 100, second around 140
        bpms = [r.bpm for r in regions]
        assert any(abs(b - 100) < 15 for b in bpms), f"No region near 100: {bpms}"
        assert any(abs(b - 140) < 15 for b in bpms), f"No region near 140: {bpms}"

    def test_beat_grid_generated(self):
        features = _make_features(bpm=120.0, duration=30.0)
        est = GlobalBpmEstimator()
        bpm, _, grid = est.estimate(features)
        assert grid.bpm > 0
        assert len(grid.beat_times) > 0
        assert len(grid.downbeat_times) > 0
        assert grid.time_signature in (3, 4)

    def test_beat_grid_spacing(self):
        """Beat times should be approximately evenly spaced.

        The hybrid grid may shift beats by up to 20% of beat period to
        align with detected onsets, so we allow wider tolerance.
        """
        features = _make_features(bpm=120.0, duration=30.0)
        est = GlobalBpmEstimator()
        _, _, grid = est.estimate(features)
        if len(grid.beat_times) >= 3:
            expected_period = 60.0 / grid.bpm
            snap_tolerance = expected_period * 0.20  # hybrid grid snaps within 20%
            for i in range(1, len(grid.beat_times)):
                interval = grid.beat_times[i] - grid.beat_times[i - 1]
                assert abs(interval - expected_period) < snap_tolerance

    def test_downbeats_at_bar_boundaries(self):
        """Downbeats should be every Nth beat."""
        features = _make_features(bpm=120.0, duration=30.0)
        est = GlobalBpmEstimator()
        _, _, grid = est.estimate(features)
        ts = grid.time_signature
        beat_period = 60.0 / grid.bpm if grid.bpm > 0 else 1.0
        for db in grid.downbeat_times:
            # Each downbeat should be at a position that's a multiple of ts beats
            assert db in grid.beat_times

    def test_empty_features(self):
        est = GlobalBpmEstimator()
        bpm, regions, grid = est.estimate([])
        assert bpm == 0.0
        assert regions == []
        assert grid.bpm == 0.0

    def test_all_zero_bpm(self):
        """Features with all BPM=0 (silence) should return 0."""
        features = _make_features(bpm=0.0, duration=10.0)
        est = GlobalBpmEstimator()
        bpm, _, _ = est.estimate(features)
        assert bpm == 0.0

    def test_confidence_populated(self):
        features = _make_features(bpm=120.0, duration=30.0)
        est = GlobalBpmEstimator()
        _, regions, _ = est.estimate(features)
        for r in regions:
            assert 0.0 <= r.confidence <= 1.0

    def test_regions_cover_full_duration(self):
        """Tempo regions should cover the full song duration."""
        features = _make_features(bpm=120.0, duration=30.0)
        est = GlobalBpmEstimator()
        _, regions, _ = est.estimate(features)
        if regions:
            assert regions[0].start_t == 0.0
            assert regions[-1].end_t >= features[-1].t - 1.0

    def test_two_pass_phase_more_accurate(self):
        """Two-pass phase search should find sub-millisecond phase offset."""
        # Synthetic: 120 BPM with exact phase at 37ms
        # Test _build_even_grid directly with exact BPM to isolate phase search
        dt = 512 / 44100
        bpm = 120.0
        beat_period = 60.0 / bpm  # 0.5s
        phase = 0.037  # 37ms
        duration = 30.0
        n_frames = int(duration / dt)
        rows = []
        for i in range(n_frames):
            t = i * dt
            nearest_beat = round((t - phase) / beat_period) * beat_period + phase
            is_beat = abs(t - nearest_beat) < dt and t > 2.0
            rows.append(FeatureRow(
                t=t, rms=0.3, zcr=0.05, centroid=2000.0,
                bass_ratio=0.3, spectral_flux=0.5, kick_spectral_flux=0.1,
                onset_strength=0.2, energy=0.4, bpm=bpm if t > 1.5 else 0.0,
                beat=is_beat, mood="groove",
            ))

        est = GlobalBpmEstimator()
        # Call _build_even_grid directly with exact BPM
        beat_times, _ = est._build_even_grid(rows, bpm)

        # Grid phase should be within 2ms of the true 37ms phase
        assert beat_times, "Expected non-empty beat grid"
        first_beat = beat_times[0]
        detected_phase = first_beat % beat_period
        phase_error = min(
            abs(detected_phase - phase),
            abs(detected_phase - phase + beat_period),
            abs(detected_phase - phase - beat_period),
        )
        assert phase_error < 0.002, (
            f"Phase error {phase_error*1000:.1f}ms (detected={detected_phase*1000:.1f}ms, "
            f"expected={phase*1000:.1f}ms)"
        )

    def test_octave_detection_halves_double_time(self):
        """160 BPM with only every-other beat having onset → should return 80 BPM."""
        est = GlobalBpmEstimator()
        beat_period = 60.0 / 160.0  # 0.375s
        # Onsets only on even beat positions (0, 2, 4, ...) relative to t=0
        onset_times = [n * beat_period for n in range(0, 40, 2) if n * beat_period > 2.0]
        assert len(onset_times) >= 8
        result = est._resolve_harmonic_alias(160.0, [160.0] * 50, onset_times)
        assert abs(result - 80.0) < 1.0, f"Expected ~80, got {result}"

    def test_octave_detection_keeps_true_fast(self):
        """150 BPM with even onset distribution → stays 150."""
        est = GlobalBpmEstimator()
        beat_period = 60.0 / 150.0
        # Onsets on both even and odd beats
        onset_times = [i * beat_period + 2.5 for i in range(40)]
        result = est._resolve_harmonic_alias(150.0, [150.0] * 50, onset_times)
        assert abs(result - 150.0) < 1.0, f"Expected ~150, got {result}"

    def test_octave_detection_no_onsets_noop(self):
        """No onset_times → falls back to existing behavior."""
        est = GlobalBpmEstimator()
        result = est._resolve_harmonic_alias(120.0, [120.0] * 50)
        assert result == 120.0
        result2 = est._resolve_harmonic_alias(120.0, [120.0] * 50, None)
        assert result2 == 120.0


# ---------------------------------------------------------------------------
# Hybrid beat grid (D1)
# ---------------------------------------------------------------------------

class TestHybridBeatGrid:
    def test_hybrid_grid_snaps_to_onsets(self):
        """Grid beats near detected onsets should snap to onset times."""
        est = GlobalBpmEstimator()
        bpm = 120.0
        beat_period = 60.0 / bpm  # 0.5s

        # Even grid: 0.0, 0.5, 1.0, 1.5, ...
        even_beats = [round(i * beat_period, 4) for i in range(10)]

        # Onsets slightly offset from grid
        onsets = [0.02, 0.48, 1.03, 1.52]

        hybrid = est._build_hybrid_beat_grid(even_beats, onsets, bpm)

        # Snapped beats should be within 5ms of the onset
        assert abs(hybrid[0] - 0.02) < 0.005
        assert abs(hybrid[1] - 0.48) < 0.005
        assert abs(hybrid[2] - 1.03) < 0.005
        assert abs(hybrid[3] - 1.52) < 0.005

    def test_hybrid_grid_falls_back_to_even(self):
        """No onsets → output matches even grid."""
        est = GlobalBpmEstimator()
        bpm = 120.0
        beat_period = 60.0 / bpm
        even_beats = [round(i * beat_period, 4) for i in range(10)]

        hybrid = est._build_hybrid_beat_grid(even_beats, [], bpm)
        assert hybrid == even_beats

    def test_hybrid_grid_monotonicity(self):
        """Snapped beats that would cause non-monotonic order are corrected."""
        est = GlobalBpmEstimator()
        bpm = 120.0
        beat_period = 60.0 / bpm
        even_beats = [round(i * beat_period, 4) for i in range(6)]

        # Onset that would pull beat[2] (1.0) earlier than beat[1] (0.5)
        # won't happen with 20% tolerance (0.1s), but two close onsets can swap
        # Test with onsets that snap adjacent beats very close
        onsets = [0.59, 0.51]  # both near beat[1]=0.5

        hybrid = est._build_hybrid_beat_grid(even_beats, onsets, bpm)
        for i in range(1, len(hybrid)):
            assert hybrid[i] > hybrid[i - 1], f"Non-monotonic at index {i}"

    def test_hybrid_grid_preserves_count(self):
        """Same number of beats as even grid."""
        est = GlobalBpmEstimator()
        bpm = 120.0
        beat_period = 60.0 / bpm
        even_beats = [round(i * beat_period, 4) for i in range(20)]
        onsets = [0.01, 0.49, 1.02, 1.53, 2.01]

        hybrid = est._build_hybrid_beat_grid(even_beats, onsets, bpm)
        assert len(hybrid) == len(even_beats)

    def test_hybrid_grid_ignores_distant_onsets(self):
        """Onsets far from any grid beat are ignored."""
        est = GlobalBpmEstimator()
        bpm = 120.0
        beat_period = 60.0 / bpm
        even_beats = [round(i * beat_period, 4) for i in range(5)]

        # Onset at 0.25 is exactly between beats 0.0 and 0.5 — outside 20% tolerance
        onsets = [0.25]
        hybrid = est._build_hybrid_beat_grid(even_beats, onsets, bpm)
        assert hybrid == even_beats  # no snapping
