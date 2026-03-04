"""Section Segmenter — detect structural sections via self-similarity and novelty curve."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from dreamsync.analyzer.bpm import BeatGrid, TempoRegion
from dreamsync.analyzer.features import FeatureRow


@dataclass(frozen=True)
class Section:
    start_t: float
    end_t: float
    label: str              # "intro" | "verse" | "chorus" | "bridge" | "drop" | "outro" | "breakdown"
    energy_mean: float      # average Director energy in this section
    mood: str               # dominant mood: "chill" | "groove" | "hype" | "drop"
    bpm: float              # BPM in this section (from TempoRegion)
    section_id: str         # structural label: "A", "B", "C"... (repeated sections share ID)


class SectionSegmenter:
    """Detect section boundaries and label them using self-similarity + novelty curve."""

    def __init__(
        self,
        kernel_size: int = 64,
        min_section_seconds: float = 8.0,
        peak_threshold: float = 0.3,
        similarity_threshold: float = 0.7,
        feature_weights: dict[str, float] | None = None,
    ) -> None:
        self.kernel_size = kernel_size
        self.min_section_seconds = min_section_seconds
        self.peak_threshold = peak_threshold
        self.similarity_threshold = similarity_threshold
        self.feature_weights = feature_weights or {
            "centroid": 0.3,
            "bass_ratio": 0.25,
            "spectral_flux": 0.25,
            "rms": 0.1,
            "onset_strength": 0.1,
        }

    def segment(
        self,
        features: list[FeatureRow],
        beat_grid: BeatGrid,
        tempo_regions: list[TempoRegion],
    ) -> list[Section]:
        """Detect section boundaries and label them."""
        if not features:
            return []

        duration = features[-1].t
        if duration <= 0:
            return [self._make_section(features, 0.0, 0.0, "A", tempo_regions)]

        # 1. Build feature matrix
        feat_matrix = self._build_feature_matrix(features)

        # 2. Compute self-similarity matrix
        sim_matrix = self._self_similarity(feat_matrix)

        # 3. Compute novelty curve via checkerboard kernel
        novelty = self._novelty_curve(sim_matrix)

        # 4. Peak-pick boundaries
        boundaries = self._pick_peaks(novelty, features)

        # 5. Snap to downbeats
        boundaries = self._snap_to_downbeats(boundaries, beat_grid)

        # Ensure 0 and duration are in boundaries
        if not boundaries or boundaries[0] > 0.01:
            boundaries.insert(0, 0.0)
        if boundaries[-1] < duration - 0.01:
            boundaries.append(duration)

        # 6. Build sections from boundary pairs
        sections_raw: list[tuple[float, float, list[FeatureRow]]] = []
        for i in range(len(boundaries) - 1):
            start = boundaries[i]
            end = boundaries[i + 1]
            section_features = [f for f in features if start <= f.t < end]
            if section_features:
                sections_raw.append((start, end, section_features))

        if not sections_raw:
            return [self._make_section(features, 0.0, duration, "A", tempo_regions)]

        # 7. Assign structural IDs via clustering
        section_vectors = [self._section_vector(sf) for _, _, sf in sections_raw]
        ids = self._assign_structural_ids(section_vectors)

        # 8. Label sections
        sections: list[Section] = []
        for i, ((start, end, sf), sid) in enumerate(zip(sections_raw, ids)):
            sections.append(self._make_section(
                sf, start, end, sid, tempo_regions,
            ))

        # Apply labels based on heuristics
        sections = self._label_sections(sections)

        return sections

    def _build_feature_matrix(self, features: list[FeatureRow]) -> np.ndarray:
        """Build normalized feature matrix from FeatureRows."""
        n = len(features)
        keys = list(self.feature_weights.keys())
        matrix = np.zeros((n, len(keys)), dtype=np.float64)

        for i, f in enumerate(features):
            for j, key in enumerate(keys):
                matrix[i, j] = getattr(f, key, 0.0)

        # Normalize each dimension to [0, 1]
        for j in range(matrix.shape[1]):
            col = matrix[:, j]
            mn, mx = col.min(), col.max()
            span = mx - mn
            if span > 1e-10:
                matrix[:, j] = (col - mn) / span
            else:
                matrix[:, j] = 0.0

        # Apply weights
        weights = np.array([self.feature_weights[k] for k in keys])
        matrix *= weights[np.newaxis, :]

        return matrix

    def _self_similarity(self, matrix: np.ndarray) -> np.ndarray:
        """Compute cosine self-similarity matrix. Optionally downsample for efficiency."""
        n = matrix.shape[0]

        # Downsample if too many frames
        step = 1
        if n > 2000:
            step = max(1, n // 1000)
            matrix = matrix[::step]
            n = matrix.shape[0]

        # Normalize rows
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-10)
        normalized = matrix / norms

        # Cosine similarity
        sim = normalized @ normalized.T

        return sim

    def _novelty_curve(self, sim: np.ndarray) -> np.ndarray:
        """Compute novelty curve by convolving the diagonal with a checkerboard kernel."""
        n = sim.shape[0]
        k = min(self.kernel_size, n // 4)
        if k < 2:
            return np.zeros(n)

        # Build checkerboard kernel
        kernel = np.ones((k, k))
        half = k // 2
        kernel[:half, :half] = 1.0
        kernel[half:, half:] = 1.0
        kernel[:half, half:] = -1.0
        kernel[half:, :half] = -1.0

        # Convolve along diagonal
        novelty = np.zeros(n)
        for i in range(k // 2, n - k // 2):
            lo = i - k // 2
            hi = lo + k
            if hi > n:
                break
            patch = sim[lo:hi, lo:hi]
            novelty[i] = float(np.sum(patch * kernel))

        # Normalize
        mx = novelty.max()
        if mx > 0:
            novelty /= mx

        return novelty

    def _pick_peaks(
        self, novelty: np.ndarray, features: list[FeatureRow],
    ) -> list[float]:
        """Pick peaks from novelty curve that exceed threshold."""
        threshold = self.peak_threshold * novelty.max() if novelty.max() > 0 else 0

        # Map novelty indices to time
        n_features = len(features)
        n_novelty = len(novelty)

        # Account for potential downsampling
        step = max(1, n_features // n_novelty) if n_novelty > 0 else 1

        boundaries: list[float] = []
        for i in range(1, len(novelty) - 1):
            if (
                novelty[i] > threshold
                and novelty[i] >= novelty[i - 1]
                and novelty[i] >= novelty[i + 1]
            ):
                feat_idx = min(i * step, n_features - 1)
                t = features[feat_idx].t
                # Enforce minimum section length
                if not boundaries or (t - boundaries[-1]) >= self.min_section_seconds:
                    boundaries.append(t)

        return boundaries

    def _snap_to_downbeats(
        self, boundaries: list[float], beat_grid: BeatGrid,
    ) -> list[float]:
        """Move each boundary to the nearest downbeat."""
        if not beat_grid.downbeat_times or not boundaries:
            return boundaries

        downbeats = list(beat_grid.downbeat_times)
        snapped: list[float] = []
        for b in boundaries:
            # Find nearest downbeat
            best = min(downbeats, key=lambda d: abs(d - b))
            if abs(best - b) < self.min_section_seconds / 2:
                snapped.append(best)
            else:
                snapped.append(b)

        # Remove duplicates and sort
        snapped = sorted(set(snapped))
        return snapped

    def _section_vector(self, features: list[FeatureRow]) -> np.ndarray:
        """Compute a representative feature vector for a section."""
        keys = list(self.feature_weights.keys())
        values = np.zeros(len(keys))
        if not features:
            return values
        for j, key in enumerate(keys):
            vals = [getattr(f, key, 0.0) for f in features]
            values[j] = float(np.mean(vals))
        return values

    def _assign_structural_ids(self, vectors: list[np.ndarray]) -> list[str]:
        """Assign structural IDs (A, B, C...) via simple agglomerative clustering."""
        n = len(vectors)
        if n == 0:
            return []
        if n == 1:
            return ["A"]

        # Compute pairwise distances
        labels = list(range(n))
        letter_map: dict[int, str] = {}
        next_letter = 0

        # Simple greedy clustering
        for i in range(n):
            if i == 0:
                letter_map[i] = chr(ord("A") + next_letter)
                next_letter += 1
                continue

            # Compare to all previous sections
            best_match = -1
            best_sim = -1.0
            for j in range(i):
                sim = self._cosine_sim(vectors[i], vectors[j])
                if sim > best_sim:
                    best_sim = sim
                    best_match = j

            if best_sim >= self.similarity_threshold:
                letter_map[i] = letter_map[best_match]
            else:
                letter_map[i] = chr(ord("A") + min(next_letter, 25))
                next_letter += 1

        return [letter_map[i] for i in range(n)]

    @staticmethod
    def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a < 1e-10 or norm_b < 1e-10:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    def _make_section(
        self,
        features: list[FeatureRow],
        start_t: float,
        end_t: float,
        section_id: str,
        tempo_regions: list[TempoRegion],
    ) -> Section:
        """Create a Section from features and metadata."""
        if features:
            energy_mean = float(np.mean([f.energy for f in features]))
            # Dominant mood: mode
            mood_counts: dict[str, int] = {}
            for f in features:
                mood_counts[f.mood] = mood_counts.get(f.mood, 0) + 1
            mood = max(mood_counts, key=mood_counts.get)  # type: ignore[arg-type]
        else:
            energy_mean = 0.0
            mood = "chill"

        # BPM from tempo region that overlaps this section
        bpm = 0.0
        mid_t = (start_t + end_t) / 2
        for region in tempo_regions:
            if region.start_t <= mid_t <= region.end_t:
                bpm = region.bpm
                break
        if bpm == 0.0 and tempo_regions:
            bpm = tempo_regions[0].bpm

        return Section(
            start_t=round(start_t, 4),
            end_t=round(end_t, 4),
            label="unknown",  # labelled in _label_sections
            energy_mean=round(energy_mean, 4),
            mood=mood,
            bpm=round(bpm, 2),
            section_id=section_id,
        )

    def _label_sections(self, sections: list[Section]) -> list[Section]:
        """Apply labelling heuristics to assign musical names."""
        if not sections:
            return sections

        n = len(sections)
        energies = [s.energy_mean for s in sections]
        max_energy = max(energies) if energies else 0.0

        # First pass: identify highest-energy sections
        labels: list[str] = ["unknown"] * n
        id_labels: dict[str, str] = {}  # section_id → label

        for i, s in enumerate(sections):
            energy_ratio = s.energy_mean / max_energy if max_energy > 0 else 0

            # Intro: first section, low energy, short
            if i == 0 and energy_ratio < 0.5 and (s.end_t - s.start_t) < 30.0:
                labels[i] = "intro"
                continue

            # Outro: last section, energy declining
            if i == n - 1:
                if n > 1 and s.energy_mean <= sections[i - 1].energy_mean:
                    labels[i] = "outro"
                    continue

            # Drop: high energy spike + BPM > 120
            if energy_ratio > 0.8 and s.mood == "drop":
                labels[i] = "drop"
                continue

            # Chorus: highest energy sections
            if energy_ratio > 0.7:
                labels[i] = "chorus"
                continue

            # Breakdown: low energy between high-energy sections
            if i > 0 and i < n - 1 and energy_ratio < 0.4:
                left_energy = energies[i - 1] / max_energy if max_energy > 0 else 0
                right_energy = energies[i + 1] / max_energy if max_energy > 0 else 0
                if left_energy > 0.5 and right_energy > 0.5:
                    labels[i] = "bridge" if energy_ratio > 0.2 else "breakdown"
                    continue

            # Verse: moderate energy
            if energy_ratio >= 0.3:
                labels[i] = "verse"
            else:
                labels[i] = "verse"

        # Second pass: use structural IDs for consistency
        for i, s in enumerate(sections):
            sid = s.section_id
            if sid in id_labels and labels[i] == "unknown":
                labels[i] = id_labels[sid]
            elif labels[i] != "unknown":
                if sid not in id_labels:
                    id_labels[sid] = labels[i]

        # Apply consistent labels for same structural IDs
        for i, s in enumerate(sections):
            sid = s.section_id
            if sid in id_labels and labels[i] in ("unknown", "verse"):
                # Only override generic labels, not specific ones
                if labels[i] == "unknown":
                    labels[i] = id_labels[sid]

        # Build final sections with labels
        result = []
        for i, s in enumerate(sections):
            result.append(Section(
                start_t=s.start_t,
                end_t=s.end_t,
                label=labels[i],
                energy_mean=s.energy_mean,
                mood=s.mood,
                bpm=s.bpm,
                section_id=s.section_id,
            ))

        return result
