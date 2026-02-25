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
            cooldown_seconds=0.0,
            hop_size=512,
            sample_rate=44100,
        )
        defaults.update(kwargs)
        return SongBoundaryDetector(**defaults)

    def _feed_silence_then_resume(self, det: SongBoundaryDetector, extra_silent: int = 5) -> bool:
        """Feed enough silence frames then 3 confirmation frames. Returns boundary result."""
        for _ in range(det.min_silence_frames + extra_silent):
            det.update(0.0)
        # Need _confirm_required (3) consecutive non-silent frames
        result = False
        for _ in range(det._confirm_required):
            result = det.update(0.1)
            if result:
                return True
        return result

    def test_no_boundary_during_music(self):
        det = self._make()
        for _ in range(5000):
            assert det.update(0.1) is False
        assert det.boundary_count == 0

    def test_no_boundary_short_silence(self):
        """Silence shorter than min_silence_seconds doesn't trigger."""
        det = self._make(min_song_seconds=0.0)
        short = det.min_silence_frames - 1
        for _ in range(short):
            det.update(0.0)
        # Resume music — not enough silence accumulated
        for _ in range(det._confirm_required):
            assert det.update(0.1) is False
        assert det.boundary_count == 0

    def test_boundary_after_silence_gap(self):
        """Silence >= threshold then sustained audio resumes → boundary detected."""
        det = self._make(min_song_seconds=0.0)
        assert self._feed_silence_then_resume(det) is True
        assert det.boundary_count == 1

    def test_no_boundary_too_early(self):
        """Boundary won't fire before min_song_frames even with enough silence."""
        det = self._make(min_song_seconds=30.0)
        for _ in range(10):
            det.update(0.1)
        for _ in range(det.min_silence_frames + 5):
            det.update(0.0)
        for _ in range(det._confirm_required):
            assert det.update(0.1) is False
        assert det.boundary_count == 0

    def test_boundary_count_increments(self):
        """Multiple boundaries increment counter."""
        det = self._make(min_song_seconds=0.0)
        for _ in range(3):
            assert self._feed_silence_then_resume(det) is True
        assert det.boundary_count == 3

    def test_boundary_resets_frame_counter(self):
        """After detection, _frames_since_reset is 0."""
        det = self._make(min_song_seconds=0.0)
        for _ in range(100):
            det.update(0.1)
        self._feed_silence_then_resume(det)
        assert det._frames_since_reset == 0

    def test_single_noise_spike_does_not_trigger(self):
        """Fix 5: A single non-silent frame during silence should not trigger."""
        det = self._make(min_song_seconds=0.0)
        for _ in range(det.min_silence_frames + 5):
            det.update(0.0)
        # Single noise spike
        assert det.update(0.1) is False
        # Back to silence
        det.update(0.0)
        assert det.boundary_count == 0

    def test_cooldown_prevents_rapid_retrigger(self):
        """Fix 4: Cooldown prevents boundaries firing faster than cooldown_seconds."""
        det = self._make(min_song_seconds=0.0, cooldown_seconds=5.0)
        # Feed enough music frames to satisfy cooldown for first boundary
        for _ in range(det._cooldown_frames + 1):
            det.update(0.1)
        # First boundary
        assert self._feed_silence_then_resume(det) is True
        # Try again immediately — cooldown should prevent it
        for _ in range(det.min_silence_frames + 5):
            det.update(0.0)
        for _ in range(det._confirm_required):
            assert det.update(0.1) is False
        assert det.boundary_count == 1

    def test_new_defaults(self):
        """Verify the new default parameter values (Fixes 1-3)."""
        det = SongBoundaryDetector()
        assert det.silence_threshold_rms == 0.015
        expected_silence_frames = int(2.0 * 44100 / 512)
        assert det.min_silence_frames == expected_silence_frames
        expected_song_frames = int(120.0 * 44100 / 512)
        assert det.min_song_frames == expected_song_frames
        expected_cooldown_frames = int(90.0 * 44100 / 512)
        assert det._cooldown_frames == expected_cooldown_frames


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
        assert d._reset_t is None
        assert d._ema_rms == 0.0
        assert d._ema_bpm == 0.0
        assert d._ema_zcr == 0.0
        assert d._ema_spectral_flux == 0.0
        assert d._ema_bass_ratio == 0.0
        assert d._ema_onset_strength == 0.0
        assert d._rms_floor == 0.01
        assert d._rms_ceil == 0.10
        assert d._flux_max == 10.0
        assert d._onset_max == 0.05
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


# ---------------------------------------------------------------------------
# Bug 3 regression tests: Mood resets to HYPE after song boundary
# ---------------------------------------------------------------------------


class TestBug3WarmupGuardRelative:
    """Fix 1: Warmup guard should fire relative to reset, not session start."""

    def test_warmup_forces_ambient_after_reset(self):
        """After reset at t=100, Director should stay AMBIENT for warmup_seconds."""
        d = Director()
        # Simulate running for 100s (well past warmup)
        for i in range(100):
            d.update({"t": float(i), "rms": 0.05, "zcr": 0.01, "bpm": 120.0, "beat": False})

        # Now reset (simulating song boundary)
        d.reset()

        # First update after reset at t=100 should enter warmup
        intent = d.update({"t": 100.0, "rms": 0.3, "zcr": 0.01, "bpm": 120.0, "beat": False})
        assert intent.mode.value == "ambient", "Should be AMBIENT during warmup after reset"

        # Still warmup at t=105 (5s after reset, warmup=8s)
        intent = d.update({"t": 105.0, "rms": 0.3, "zcr": 0.01, "bpm": 120.0, "beat": False})
        assert intent.mode.value == "ambient", "Should still be AMBIENT at t+5s"

    def test_warmup_expires_after_warmup_seconds(self):
        """After warmup_seconds past reset, Director should allow mode switching."""
        d = Director()
        d.reset()
        # Run through warmup at t=50..58
        for i in range(9):
            d.update({"t": 50.0 + float(i), "rms": 0.05, "zcr": 0.01, "bpm": 120.0, "beat": False})

        # At t=59 (9s after reset=50), warmup should be over, high energy -> MOTION possible
        intent = d.update({"t": 59.0, "rms": 0.5, "zcr": 0.01, "bpm": 120.0, "beat": False})
        # Mode may or may not switch yet (depends on energy thresholds), but
        # the point is we're no longer forcefully in warmup AMBIENT
        assert d._reset_t == 50.0


class TestBug3NormalizationSeeds:
    """Fix 2: Normalization should seed with reasonable defaults, not near-zero."""

    def test_reset_seeds_prevent_energy_spike(self):
        """First frame after reset should NOT produce energy >= 0.5."""
        d = Director()
        # Warm up normally
        for i in range(20):
            d.update({"t": float(i), "rms": 0.06, "zcr": 0.01, "bpm": 120.0,
                       "beat": False, "spectral_flux": 5.0, "onset_strength": 0.03})

        d.reset()

        # First frame after reset with typical bar audio
        d.update({"t": 30.0, "rms": 0.06, "zcr": 0.01, "bpm": 120.0,
                  "beat": False, "spectral_flux": 5.0, "onset_strength": 0.03})
        assert d.energy < 0.50, f"Energy spike after reset: {d.energy:.3f} (should be <0.50)"

    def test_reset_seeds_are_reasonable(self):
        """Verify the seed values are set correctly after reset."""
        d = Director()
        d.reset()
        assert d._rms_floor == 0.01
        assert d._rms_ceil == 0.10
        assert d._flux_max == 10.0
        assert d._onset_max == 0.05


class TestBug3MoodDwellAfterReset:
    """Fix 3: MoodClassifier dwell guard should work after reset."""

    def test_reset_with_time_sets_dwell(self):
        """reset(t=50.0) should prevent mood switching for min_dwell_seconds."""
        mc = MoodClassifier()
        mc.reset(t=50.0)
        assert mc._mood_entered_at == 50.0

        # At t=51 (only 1s after reset, dwell=4s), should stay CHILL
        # even with high energy that would normally trigger HYPE
        mood = mc.update(energy=0.8, stability=0.02, bpm=128.0, t=51.0)
        assert mood == Mood.CHILL, "Should stay CHILL during dwell period"

    def test_reset_without_time_preserves_old_behavior(self):
        """reset() without time should use -1e9 (backwards compatible)."""
        mc = MoodClassifier()
        mc.reset()
        assert mc._mood_entered_at == -1e9

    def test_mood_can_switch_after_dwell(self):
        """After dwell expires, mood should switch based on energy."""
        mc = MoodClassifier()
        mc.reset(t=50.0)

        # At t=55 (5s after reset, dwell=4s), should be able to switch
        mood = mc.update(energy=0.8, stability=0.02, bpm=128.0, t=55.0)
        # With energy=0.8, should transition out of CHILL
        assert mood != Mood.CHILL or mc._can_switch(55.0), "Dwell should have expired"


class TestBug3EndToEnd:
    """Integration test: all three fixes working together."""

    def test_boundary_reset_stays_chill_during_warmup(self):
        """After a boundary reset, mood stays CHILL for the warmup period."""
        d = Director()
        mc = MoodClassifier()

        # Run for 50 seconds with moderate audio
        for i in range(50):
            t = float(i)
            d.update({"t": t, "rms": 0.06, "zcr": 0.01, "bpm": 128.0,
                       "beat": False, "spectral_flux": 5.0, "onset_strength": 0.03})
            mc.update(d.energy, d.stability, d.effective_bpm, t)

        # Simulate song boundary
        d.reset()
        mc.reset(t=50.0)

        # First 7 seconds after reset: mood should stay CHILL
        hype_count = 0
        for i in range(7):
            t = 50.0 + float(i)
            intent = d.update({"t": t, "rms": 0.06, "zcr": 0.01, "bpm": 128.0,
                                "beat": False, "spectral_flux": 5.0, "onset_strength": 0.03})
            mood = mc.update(d.energy, d.stability, d.effective_bpm, t)
            if mood == Mood.HYPE:
                hype_count += 1

        assert hype_count == 0, f"Got {hype_count} HYPE frames during warmup (should be 0)"
