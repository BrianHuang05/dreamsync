"""Global BPM Estimation — consensus BPM, tempo regions, and beat grid from offline features."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from dreamsync.analyzer.features import FeatureRow


@dataclass(frozen=True)
class TempoRegion:
    start_t: float
    end_t: float
    bpm: float
    confidence: float   # 0.0–1.0


@dataclass(frozen=True)
class BeatGrid:
    bpm: float
    beat_times: tuple[float, ...]       # absolute time of each beat
    downbeat_times: tuple[float, ...]   # bar boundaries (every Nth beat)
    time_signature: int                 # beats per bar (4 for 4/4, 3 for 3/4)


class GlobalBpmEstimator:
    """Compute global BPM, tempo regions, and a refined beat grid from offline features."""

    def __init__(
        self,
        histogram_bin_width: float = 0.5,
        warmup_skip_seconds: float = 2.0,
        tempo_change_threshold: float = 8.0,
        tempo_region_min_seconds: float = 8.0,
        preferred_bpm_range: tuple[float, float] = (80.0, 160.0),
    ) -> None:
        self.histogram_bin_width = histogram_bin_width
        self.warmup_skip_seconds = warmup_skip_seconds
        self.tempo_change_threshold = tempo_change_threshold
        self.tempo_region_min_seconds = tempo_region_min_seconds
        self.preferred_bpm_range = preferred_bpm_range

    def estimate(
        self,
        features: list[FeatureRow],
        sample_rate: int = 44100,
        hop_size: int = 512,
    ) -> tuple[float, list[TempoRegion], BeatGrid]:
        """Compute global BPM, tempo regions, and a refined beat grid."""
        if not features:
            return 0.0, [], BeatGrid(bpm=0.0, beat_times=(), downbeat_times=(), time_signature=4)

        # 1. Collect valid BPM values (skip warmup, skip zero)
        bpm_values = [
            f.bpm for f in features
            if f.bpm > 0 and f.t > self.warmup_skip_seconds
        ]
        if not bpm_values:
            duration = features[-1].t if features else 0.0
            return 0.0, [TempoRegion(0.0, duration, 0.0, 0.0)], BeatGrid(
                bpm=0.0, beat_times=(), downbeat_times=(), time_signature=4,
            )

        # 2. Build BPM histogram
        global_bpm = self._histogram_peak(bpm_values)

        # 3. Resolve harmonic aliasing
        global_bpm = self._resolve_harmonic_alias(global_bpm, bpm_values)

        # 4. Detect tempo regions
        tempo_regions = self._detect_tempo_regions(features, global_bpm)

        # 5. Build beat grid
        beat_grid = self._build_beat_grid(features, global_bpm)

        return global_bpm, tempo_regions, beat_grid

    def _histogram_peak(self, bpm_values: list[float]) -> float:
        """Find the dominant BPM from a histogram of frame-level estimates."""
        arr = np.array(bpm_values)
        bin_width = self.histogram_bin_width
        lo = max(40.0, arr.min() - 5)
        hi = min(220.0, arr.max() + 5)
        n_bins = max(1, int((hi - lo) / bin_width))
        counts, edges = np.histogram(arr, bins=n_bins, range=(lo, hi))

        # Smooth the histogram
        if len(counts) >= 5:
            kernel = np.array([1, 2, 4, 2, 1], dtype=float)
            kernel /= kernel.sum()
            counts = np.convolve(counts, kernel, mode="same")

        peak_idx = int(np.argmax(counts))
        peak_bpm = float((edges[peak_idx] + edges[peak_idx + 1]) / 2)
        return peak_bpm

    def _resolve_harmonic_alias(self, peak_bpm: float, bpm_values: list[float]) -> float:
        """Check x0.5 and x2.0 of the peak; prefer the musically correct range."""
        lo, hi = self.preferred_bpm_range

        # If already in preferred range, keep it
        if lo <= peak_bpm <= hi:
            return peak_bpm

        # Try doubling or halving to land in preferred range
        candidates = []
        half = peak_bpm * 0.5
        double = peak_bpm * 2.0

        if lo <= double <= hi and double <= 220:
            candidates.append(double)
        if lo <= half <= hi and half >= 40:
            candidates.append(half)

        # If any candidate lands in the preferred range, use it
        if candidates:
            return candidates[0]

        # Fallback: original peak
        return peak_bpm

    def _detect_tempo_regions(
        self, features: list[FeatureRow], global_bpm: float,
    ) -> list[TempoRegion]:
        """Scan frame-level BPM in windows and detect tempo changes."""
        if not features:
            return []

        duration = features[-1].t
        if duration <= 0:
            return [TempoRegion(0.0, 0.0, global_bpm, 1.0)]

        # Collect (time, bpm) pairs
        timed_bpms = [
            (f.t, f.bpm) for f in features
            if f.bpm > 0 and f.t > self.warmup_skip_seconds
        ]
        if not timed_bpms:
            return [TempoRegion(0.0, duration, global_bpm, 1.0)]

        # Scan with sliding windows
        window_seconds = self.tempo_region_min_seconds
        threshold = self.tempo_change_threshold

        regions: list[TempoRegion] = []
        region_start = 0.0
        region_bpms: list[float] = []

        for t, bpm in timed_bpms:
            region_bpms.append(bpm)
            if t - region_start >= window_seconds:
                median_bpm = float(np.median(region_bpms))
                if regions and abs(median_bpm - regions[-1].bpm) < threshold:
                    # Extend previous region
                    regions[-1] = TempoRegion(
                        regions[-1].start_t, t, regions[-1].bpm,
                        regions[-1].confidence,
                    )
                else:
                    # Confidence: fraction of BPMs within ±5 of median
                    within = sum(1 for b in region_bpms if abs(b - median_bpm) <= 5)
                    confidence = within / len(region_bpms) if region_bpms else 0.0
                    regions.append(TempoRegion(region_start, t, median_bpm, confidence))
                region_start = t
                region_bpms = []

        # Flush remaining
        if region_bpms:
            median_bpm = float(np.median(region_bpms))
            end_t = features[-1].t
            if regions and abs(median_bpm - regions[-1].bpm) < threshold:
                regions[-1] = TempoRegion(
                    regions[-1].start_t, end_t, regions[-1].bpm,
                    regions[-1].confidence,
                )
            else:
                within = sum(1 for b in region_bpms if abs(b - median_bpm) <= 5)
                confidence = within / len(region_bpms) if region_bpms else 0.0
                regions.append(TempoRegion(region_start, end_t, median_bpm, confidence))

        # If no regions were created, make one covering the full song
        if not regions:
            regions.append(TempoRegion(0.0, duration, global_bpm, 1.0))

        # Ensure first region starts at 0
        if regions[0].start_t > 0:
            regions[0] = TempoRegion(0.0, regions[0].end_t, regions[0].bpm, regions[0].confidence)

        # Ensure last region extends to end
        if regions[-1].end_t < duration:
            regions[-1] = TempoRegion(
                regions[-1].start_t, duration, regions[-1].bpm, regions[-1].confidence,
            )

        return regions

    def _build_beat_grid(
        self, features: list[FeatureRow], bpm: float,
    ) -> BeatGrid:
        """Build a phase-aligned beat grid at the global BPM."""
        if bpm <= 0 or not features:
            return BeatGrid(bpm=0.0, beat_times=(), downbeat_times=(), time_signature=4)

        duration = features[-1].t
        beat_period = 60.0 / bpm

        # Collect detected beat times from features
        detected_beats = [f.t for f in features if f.beat and f.t > self.warmup_skip_seconds]

        # Find optimal phase offset
        best_offset = 0.0
        best_score = -1.0
        n_candidates = 100
        for i in range(n_candidates):
            offset = i * beat_period / n_candidates
            score = self._beat_alignment_score(offset, beat_period, detected_beats, duration)
            if score > best_score:
                best_score = score
                best_offset = offset

        # Generate evenly-spaced beats
        beat_times: list[float] = []
        t = best_offset
        while t <= duration:
            beat_times.append(round(t, 4))
            t += beat_period

        # Estimate time signature
        time_sig = self._estimate_time_signature(detected_beats, beat_times)

        # Downbeats: every Nth beat
        downbeat_times = [beat_times[i] for i in range(0, len(beat_times), time_sig)]

        return BeatGrid(
            bpm=round(bpm, 2),
            beat_times=tuple(beat_times),
            downbeat_times=tuple(downbeat_times),
            time_signature=time_sig,
        )

    def _beat_alignment_score(
        self, offset: float, period: float, detected_beats: list[float], duration: float,
    ) -> float:
        """Score how well grid beats at (offset + n*period) align with detected beats."""
        if not detected_beats:
            return 0.0

        tolerance = period * 0.15  # 15% of beat period
        score = 0.0
        for db in detected_beats:
            # Distance from db to nearest grid beat
            n = round((db - offset) / period)
            grid_beat = offset + n * period
            dist = abs(db - grid_beat)
            if dist <= tolerance:
                score += 1.0 - (dist / tolerance)
        return score

    def _estimate_time_signature(
        self, detected_beats: list[float], grid_beats: list[float],
    ) -> int:
        """Estimate 4/4 vs 3/4 from detected beat patterns."""
        if len(grid_beats) < 8 or len(detected_beats) < 4:
            return 4  # default

        # Count how many detected beats fall on positions 0, 1, 2, 3 (mod 4)
        # and positions 0, 1, 2 (mod 3)
        period = grid_beats[1] - grid_beats[0] if len(grid_beats) > 1 else 0.5
        tolerance = period * 0.15

        # For each detected beat, find its nearest grid position
        positions: list[int] = []
        for db in detected_beats:
            best_idx = -1
            best_dist = float("inf")
            for i, gb in enumerate(grid_beats):
                d = abs(db - gb)
                if d < best_dist:
                    best_dist = d
                    best_idx = i
            if best_dist <= tolerance:
                positions.append(best_idx)

        if not positions:
            return 4

        # Score 4/4: strong beats on positions mod 4 == 0
        score_4 = sum(1 for p in positions if p % 4 == 0)
        # Score 3/4: strong beats on positions mod 3 == 0
        score_3 = sum(1 for p in positions if p % 3 == 0)

        # Normalize by expected frequency
        score_4_norm = score_4 * 4 / max(1, len(positions))
        score_3_norm = score_3 * 3 / max(1, len(positions))

        return 3 if score_3_norm > score_4_norm * 1.2 else 4
