"""Tests for CrossfadeBoundaryDetector — crossfade-aware song boundary detection."""

from __future__ import annotations

from dreamsync.live import CrossfadeBoundaryDetector, CrossfadeConfig


class TestCrossfadeBoundaryDetector:
    """Unit tests for the weighted-voting crossfade boundary detector."""

    def _make(self, **kwargs) -> CrossfadeBoundaryDetector:
        """Create a detector with short timings for fast tests."""
        cfg_kwargs: dict = dict(
            window_seconds=2.0,
            recent_seconds=0.5,
            min_song_seconds=0.0,  # no cooldown by default
            confirm_frames=1,      # fire immediately when threshold met
        )
        cfg_kwargs.update(kwargs)
        config = CrossfadeConfig(**cfg_kwargs)
        return CrossfadeBoundaryDetector(
            config=config, hop_size=512, sample_rate=44100,
        )

    def _feed_stable(
        self,
        det: CrossfadeBoundaryDetector,
        n: int,
        bpm: float = 128.0,
        centroid: float = 3000.0,
        bass_ratio: float = 0.4,
        energy: float = 0.5,
        beat_interval: int = 10,
    ) -> float:
        """Feed *n* stable frames and return the final time value."""
        dt = 512 / 44100
        t = 0.0
        for i in range(n):
            t = i * dt
            det.update(
                bpm=bpm,
                centroid=centroid,
                bass_ratio=bass_ratio,
                energy=energy,
                onset_strength=0.1,
                beat=(i % beat_interval == 0),
                t=t,
            )
        return t

    # ------------------------------------------------------------------
    # Individual signal tests
    # ------------------------------------------------------------------

    def test_bpm_jump_triggers_vote(self):
        """A sudden BPM shift should fire the BPM signal vote."""
        det = self._make()
        # Fill window with stable BPM
        t = self._feed_stable(det, 200, bpm=128.0)
        dt = 512 / 44100

        # Now shift BPM dramatically
        fired = False
        for i in range(100):
            t += dt
            if det.update(bpm=95.0, centroid=3000.0, bass_ratio=0.4,
                          energy=0.5, onset_strength=0.1, beat=False, t=t):
                fired = True
                break
        # BPM alone (0.35) is below threshold (0.50), so should NOT fire
        assert not fired, "BPM signal alone should not trigger boundary"

    def test_centroid_shift_triggers_vote(self):
        """A centroid shift alone should not fire (0.25 < 0.50 threshold)."""
        det = self._make()
        t = self._feed_stable(det, 200, centroid=3000.0)
        dt = 512 / 44100

        fired = False
        for i in range(100):
            t += dt
            if det.update(bpm=128.0, centroid=6000.0, bass_ratio=0.4,
                          energy=0.5, onset_strength=0.1, beat=False, t=t):
                fired = True
                break
        assert not fired, "Centroid signal alone should not trigger boundary"

    # ------------------------------------------------------------------
    # Threshold tests
    # ------------------------------------------------------------------

    def test_single_signal_below_threshold(self):
        """No single signal should cross the 0.50 trigger threshold."""
        det = self._make()
        t = self._feed_stable(det, 200)
        dt = 512 / 44100

        # Shift only bass ratio (weight=0.15)
        fired = False
        for i in range(100):
            t += dt
            if det.update(bpm=128.0, centroid=3000.0, bass_ratio=0.9,
                          energy=0.5, onset_strength=0.1, beat=False, t=t):
                fired = True
                break
        assert not fired

    def test_two_signals_trigger_boundary(self):
        """BPM (0.35) + centroid (0.25) = 0.60 >= 0.50 → boundary fires."""
        det = self._make()
        t = self._feed_stable(det, 200, bpm=128.0, centroid=3000.0)
        dt = 512 / 44100

        fired = False
        for i in range(100):
            t += dt
            if det.update(bpm=95.0, centroid=6000.0, bass_ratio=0.4,
                          energy=0.5, onset_strength=0.1, beat=False, t=t):
                fired = True
                break
        assert fired, "BPM + centroid should trigger boundary"

    def test_three_weak_signals_trigger_boundary(self):
        """centroid (0.25) + bass (0.15) + energy (0.10) = 0.50 → fires."""
        det = self._make()
        t = self._feed_stable(det, 200, centroid=3000.0, bass_ratio=0.3, energy=0.5)
        dt = 512 / 44100

        fired = False
        for i in range(100):
            t += dt
            # Shift centroid, bass, and energy — but NOT BPM
            if det.update(bpm=128.0, centroid=6000.0, bass_ratio=0.8,
                          energy=0.9, onset_strength=0.1, beat=False, t=t):
                fired = True
                break
        assert fired, "Three weak signals together should trigger boundary"

    # ------------------------------------------------------------------
    # Timing / cooldown tests
    # ------------------------------------------------------------------

    def test_min_song_seconds_respected(self):
        """Boundary within cooldown period is suppressed."""
        det = self._make(min_song_seconds=10.0)
        t = self._feed_stable(det, 200, bpm=128.0, centroid=3000.0)
        dt = 512 / 44100

        # Shift BPM + centroid but we're within min_song_seconds
        fired = False
        for i in range(50):  # only ~0.6 seconds
            t += dt
            if det.update(bpm=95.0, centroid=6000.0, bass_ratio=0.4,
                          energy=0.5, onset_strength=0.1, beat=False, t=t):
                fired = True
                break
        assert not fired, "Should not fire during min_song_seconds cooldown"

    def test_confirm_frames_required(self):
        """A transient 1-frame vote spike should not fire when confirm_frames > 1."""
        det = self._make(confirm_frames=5)
        t = self._feed_stable(det, 200, bpm=128.0, centroid=3000.0)
        dt = 512 / 44100

        # One frame with shifted features, then back to normal
        t += dt
        result = det.update(bpm=95.0, centroid=6000.0, bass_ratio=0.4,
                            energy=0.5, onset_strength=0.1, beat=False, t=t)
        assert not result, "Single shifted frame should not fire with confirm_frames=5"

        # Immediately go back to stable — confirm counter should reset
        for i in range(10):
            t += dt
            result = det.update(bpm=128.0, centroid=3000.0, bass_ratio=0.4,
                                energy=0.5, onset_strength=0.1, beat=False, t=t)
            assert not result

    # ------------------------------------------------------------------
    # Reset tests
    # ------------------------------------------------------------------

    def test_reset_clears_state(self):
        """After reset(), detector starts fresh with empty buffers."""
        det = self._make()
        self._feed_stable(det, 200)
        det.reset()

        assert len(det._bpm_buf) == 0
        assert len(det._centroid_buf) == 0
        assert len(det._bass_buf) == 0
        assert len(det._energy_buf) == 0
        assert len(det._onset_times) == 0
        assert det._confirm_count == 0
        assert det._frames_since_reset == 0

    def test_silence_and_crossfade_share_cooldown(self):
        """If we reset (simulating silence detector fire), crossfade detector
        should not fire immediately — it has no data in its buffers."""
        det = self._make(min_song_seconds=0.0)
        t = self._feed_stable(det, 200, bpm=128.0, centroid=3000.0)
        dt = 512 / 44100

        # Simulate silence detector firing — reset crossfade detector
        det.reset()

        # Immediately feed shifted features
        fired = False
        for i in range(20):
            t += dt
            if det.update(bpm=95.0, centroid=6000.0, bass_ratio=0.4,
                          energy=0.5, onset_strength=0.1, beat=False, t=t):
                fired = True
                break
        # Should not fire — buffers were just reset, no prior baseline to compare
        assert not fired, "Should not fire immediately after reset"

    # ------------------------------------------------------------------
    # False positive tests
    # ------------------------------------------------------------------

    def test_gradual_drift_no_false_positive(self):
        """Slow BPM drift (1 BPM per 50 frames) should not trigger boundary."""
        det = self._make()
        dt = 512 / 44100
        t = 0.0
        bpm = 128.0

        fired = False
        for i in range(500):
            t = i * dt
            # Drift BPM slowly upward
            bpm = 128.0 + (i * 0.02)  # +0.02 BPM per frame ≈ ~1.7 BPM/sec
            if det.update(bpm=bpm, centroid=3000.0, bass_ratio=0.4,
                          energy=0.5, onset_strength=0.1, beat=(i % 10 == 0), t=t):
                fired = True
                break
        assert not fired, "Gradual BPM drift should not trigger false positive"

    def test_half_time_with_no_corroboration(self):
        """BPM halving alone (verse→breakdown) should not trigger without
        corroborating centroid/bass shift."""
        det = self._make()
        t = self._feed_stable(det, 200, bpm=128.0, centroid=3000.0, bass_ratio=0.4)
        dt = 512 / 44100

        # Halve BPM (as if breakdown) but keep everything else stable
        fired = False
        for i in range(100):
            t += dt
            if det.update(bpm=64.0, centroid=3000.0, bass_ratio=0.4,
                          energy=0.5, onset_strength=0.1, beat=False, t=t):
                fired = True
                break
        assert not fired, "BPM halving alone should not fire (weight=0.35 < threshold=0.50)"

    # ------------------------------------------------------------------
    # Config / property tests
    # ------------------------------------------------------------------

    def test_boundary_count_increments(self):
        """Boundary count increments on each detection."""
        det = self._make()
        assert det.boundary_count == 0

        # Trigger two boundaries
        for _ in range(2):
            t = self._feed_stable(det, 200, bpm=128.0, centroid=3000.0)
            dt = 512 / 44100
            for i in range(100):
                t += dt
                if det.update(bpm=95.0, centroid=6000.0, bass_ratio=0.4,
                              energy=0.5, onset_strength=0.1, beat=False, t=t):
                    break
            det.reset()

        assert det.boundary_count == 2

    def test_default_config_values(self):
        """Verify default CrossfadeConfig values match the plan."""
        cfg = CrossfadeConfig()
        assert cfg.w_bpm == 0.35
        assert cfg.w_centroid == 0.25
        assert cfg.w_bass == 0.15
        assert cfg.w_energy == 0.10
        assert cfg.w_onset == 0.15
        assert cfg.trigger_threshold == 0.50
        assert cfg.bpm_jump_threshold == 12.0
        assert cfg.min_song_seconds == 60.0
        assert cfg.confirm_frames == 5
