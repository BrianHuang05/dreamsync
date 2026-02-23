"""Tests for SongBoundaryDetector and reset() methods on all stateful classes."""

from __future__ import annotations

from collections import deque

from dreamsync.director import Director, EffectMode
from dreamsync.effects import EffectCycler
from dreamsync.live import LiveBpmEstimator, SongBoundaryDetector
from dreamsync.mood import Mood, MoodClassifier


# ---------------------------------------------------------------------------
# SongBoundaryDetector tests
# ---------------------------------------------------------------------------


class TestSongBoundaryDetector:
    def _make(self, **kwargs) -> SongBoundaryDetector:
        defaults = dict(
            silence_threshold_rms=0.005,
            min_silence_seconds=0.8,
            min_song_seconds=30.0,
            hop_size=512,
            sample_rate=44100,
        )
        defaults.update(kwargs)
        return SongBoundaryDetector(**defaults)

    def test_no_boundary_during_music(self):
        det = self._make()
        for _ in range(5000):
            assert det.update(0.1) is False
        assert det.boundary_count == 0

    def test_no_boundary_short_silence(self):
        """Silence shorter than min_silence_seconds doesn't trigger."""
        det = self._make(min_song_seconds=0.0)  # disable song-length guard
        # Feed a few silent frames (fewer than min_silence_frames)
        short = det.min_silence_frames - 1
        for _ in range(short):
            det.update(0.0)
        # Resume music
        assert det.update(0.1) is False
        assert det.boundary_count == 0

    def test_boundary_after_silence_gap(self):
        """Silence >= threshold then audio resumes → boundary detected."""
        det = self._make(min_song_seconds=0.0)  # disable song-length guard
        # Feed enough silent frames
        for _ in range(det.min_silence_frames + 5):
            assert det.update(0.0) is False
        # First non-silent frame triggers boundary
        assert det.update(0.1) is True
        assert det.boundary_count == 1

    def test_no_boundary_too_early(self):
        """Boundary won't fire before min_song_frames even with enough silence."""
        det = self._make(min_song_seconds=30.0)
        # Feed a small number of music frames (well under min_song_frames)
        for _ in range(10):
            det.update(0.1)
        # Now silence
        for _ in range(det.min_silence_frames + 5):
            det.update(0.0)
        # Resume — should NOT trigger because not enough frames since reset
        assert det.update(0.1) is False
        assert det.boundary_count == 0

    def test_boundary_count_increments(self):
        """Multiple boundaries increment counter."""
        det = self._make(min_song_seconds=0.0)
        for _ in range(3):
            # Silence gap
            for _ in range(det.min_silence_frames + 5):
                det.update(0.0)
            # Resume
            assert det.update(0.1) is True
        assert det.boundary_count == 3

    def test_boundary_resets_frame_counter(self):
        """After detection, _frames_since_reset is 0."""
        det = self._make(min_song_seconds=0.0)
        # Feed enough music + silence + resume
        for _ in range(100):
            det.update(0.1)
        for _ in range(det.min_silence_frames + 5):
            det.update(0.0)
        det.update(0.1)  # triggers boundary
        assert det._frames_since_reset == 0


# ---------------------------------------------------------------------------
# Reset method tests
# ---------------------------------------------------------------------------


class TestBpmEstimatorReset:
    def test_reset_clears_state(self):
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512)
        # Mutate state
        est.onset_env.extend([1.0, 2.0, 3.0])
        est.prev_rms = 0.5
        est.last_bpm = 128.0
        est.last_beat_idx = 10
        est._beat_phase = 0.7
        est._candidate_bpm = 130.0
        est._candidate_hits = 3
        est._bass_activity = 0.5
        est._kick_activity = 0.5
        est._hybrid_source = "kick"

        est.reset()

        assert len(est.onset_env) == 0
        assert est.prev_rms == 0.0
        assert est.last_bpm == 0.0
        assert est.last_update_t == -1e9
        assert est.last_beat_idx == -1
        assert est.last_onset_mean == 0.0
        assert est.last_onset_std == 0.0
        assert est.last_thresh == 0.0
        assert est._beat_phase == 0.0
        assert est._candidate_bpm == 0.0
        assert est._candidate_hits == 0
        assert est._last_onset_beat_t == -1e9
        assert est._prev_onset == 0.0
        assert est._bass_activity == 0.0
        assert est._kick_activity == 0.0
        assert est._hybrid_source == "bass"

    def test_reset_preserves_config(self):
        est = LiveBpmEstimator(
            sample_rate=22050, hop_size=256, min_bpm=90.0, max_bpm=180.0,
        )
        est.onset_env.extend([1.0, 2.0])
        est.last_bpm = 120.0
        est.reset()
        assert est.sample_rate == 22050
        assert est.hop_size == 256
        assert est.min_bpm == 90.0
        assert est.max_bpm == 180.0


class TestDirectorReset:
    def test_reset_clears_state(self):
        d = Director()
        # Mutate state
        d.mode = EffectMode.MOTION
        d._ema_rms = 0.5
        d._ema_bpm = 130.0
        d._history.append((1.0, 0.5, 0.1, 130.0))
        d._last_intensity = 0.8
        d._energy = 0.6
        d._color_idx = 3
        d.last_beat_event = True

        d.reset()

        assert d.mode == EffectMode.AMBIENT
        assert d._last_switch_time == -1e9
        assert d._last_t is None
        assert d._ema_rms == 0.0
        assert d._ema_bpm == 0.0
        assert d._ema_zcr == 0.0
        assert d._ema_spectral_flux == 0.0
        assert d._ema_bass_ratio == 0.0
        assert d._ema_onset_strength == 0.0
        assert d._rms_floor == 0.0
        assert d._rms_ceil == 0.001
        assert d._flux_max == 1e-6
        assert d._onset_max == 1e-6
        assert d._energy == 0.0
        assert len(d._history) == 0
        assert d._last_intensity == d.config.intensity_floor
        assert d._last_speed == d.config.ambient_speed
        assert d._last_stability == 0.0
        assert d._last_effective_bpm == 0.0
        assert d._color_idx == 0
        assert d.last_beat_event is False

    def test_reset_preserves_config_and_colors(self):
        d = Director()
        original_config = d.config
        d.set_colors(("#ff0000", "#00ff00"))
        d._ema_rms = 0.5
        d.reset()
        assert d.config is original_config
        assert d._colors == ("#ff0000", "#00ff00")


class TestMoodClassifierReset:
    def test_reset_clears_state(self):
        mc = MoodClassifier()
        # Mutate state
        mc.mood = Mood.HYPE
        mc._mood_entered_at = 100.0
        mc._drop_entered_at = 90.0
        mc._last_drop_at = 85.0
        mc._dip_seen = True
        mc._dip_at = 80.0
        mc._prev_energy = 0.8

        mc.reset()

        assert mc.mood == Mood.CHILL
        assert mc._mood_entered_at == -1e9
        assert mc._drop_entered_at == -1e9
        assert mc._last_drop_at == -1e9
        assert mc._dip_seen is False
        assert mc._dip_at == -1e9
        assert mc._prev_energy == 0.0

    def test_reset_preserves_config(self):
        mc = MoodClassifier()
        original_config = mc.config
        mc.mood = Mood.HYPE
        mc.reset()
        assert mc.config is original_config


class TestEffectCyclerReset:
    def test_reset_clears_state(self):
        ec = EffectCycler(seed=42)
        # Mutate state
        ec._current_effect = "beat_pulse"
        ec._current_mood = Mood.GROOVE
        ec._effect_start_t = 50.0
        ec._palette_name = "neon"
        ec._in_drop = True
        ec._drop_start_t = 45.0

        ec.reset()

        assert ec._current_effect is None
        assert ec._current_mood is None
        assert ec._effect_start_t == -1e9
        assert ec._palette_name is None
        assert ec._in_drop is False
        assert ec._drop_start_t == -1e9

    def test_reset_preserves_config_and_rng(self):
        ec = EffectCycler(seed=42)
        original_config = ec.config
        ec._current_effect = "beat_pulse"
        ec.reset()
        assert ec.config is original_config
        # RNG should still be usable after reset
        assert ec._rng is not None
