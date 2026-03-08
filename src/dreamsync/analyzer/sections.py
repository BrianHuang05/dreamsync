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
        similarity_threshold: float = 0.96,
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
        # Group weights for section vector clustering (spectral vs MFCC vs chroma)
        self.feature_group_weights = {
            "spectral": 0.4,
            "mfcc": 0.3,
            "chroma": 0.3,
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

        # 7. Assign structural IDs via clustering (use normalized feature matrix)
        section_vectors = self._section_vectors_from_matrix(
            feat_matrix, features, sections_raw,
        )
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
        """Build normalized feature matrix from FeatureRows.

        Columns: 5 spectral + 13 MFCC + 12 chroma = 30 dimensions.
        Each group is normalized independently, then weighted by group weight.
        """
        n = len(features)
        n_spectral = len(self.feature_weights)
        n_mfcc = 13
        n_chroma = 12
        n_cols = n_spectral + n_mfcc + n_chroma
        matrix = np.zeros((n, n_cols), dtype=np.float64)

        keys = list(self.feature_weights.keys())

        for i, f in enumerate(features):
            # Spectral features
            for j, key in enumerate(keys):
                matrix[i, j] = getattr(f, key, 0.0)
            # MFCC features
            for j, val in enumerate(f.mfcc[:n_mfcc]):
                matrix[i, n_spectral + j] = val
            # Chroma features
            for j, val in enumerate(f.chroma[:n_chroma]):
                matrix[i, n_spectral + n_mfcc + j] = val

        # Normalize each dimension to [0, 1]
        for j in range(n_cols):
            col = matrix[:, j]
            mn, mx = col.min(), col.max()
            span = mx - mn
            if span > 1e-10:
                matrix[:, j] = (col - mn) / span
            else:
                matrix[:, j] = 0.0

        # Apply group weights
        gw = self.feature_group_weights
        spectral_weights = np.array([self.feature_weights[k] for k in keys])
        # Normalize spectral weights to sum to spectral group weight
        sw_sum = spectral_weights.sum()
        if sw_sum > 0:
            spectral_weights *= gw["spectral"] / sw_sum
        matrix[:, :n_spectral] *= spectral_weights[np.newaxis, :]

        # MFCC: equal weight per coefficient within group
        mfcc_w = gw["mfcc"] / n_mfcc
        matrix[:, n_spectral:n_spectral + n_mfcc] *= mfcc_w

        # Chroma: equal weight per bin within group
        chroma_w = gw["chroma"] / n_chroma
        matrix[:, n_spectral + n_mfcc:] *= chroma_w

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

    def _section_vectors_from_matrix(
        self,
        feat_matrix: np.ndarray,
        features: list[FeatureRow],
        sections_raw: list[tuple[float, float, list[FeatureRow]]],
    ) -> list[np.ndarray]:
        """Compute section vectors by averaging rows of the normalized feature matrix."""
        # Build a time → matrix-row-index mapping
        # feat_matrix may have been downsampled in _self_similarity, but it was
        # built from all features so row i corresponds to features[i].
        n = len(features)
        vectors = []
        for start, end, _ in sections_raw:
            # Find frame indices in this section
            row_indices = [i for i in range(n) if start <= features[i].t < end]
            if row_indices and max(row_indices) < feat_matrix.shape[0]:
                section_rows = feat_matrix[row_indices]
                vectors.append(section_rows.mean(axis=0))
            else:
                vectors.append(np.zeros(feat_matrix.shape[1]))
        return vectors

    def _section_vector(self, features: list[FeatureRow]) -> np.ndarray:
        """Compute a representative 30-dim feature vector for a section (raw, unnormalized)."""
        keys = list(self.feature_weights.keys())
        n_spectral = len(keys)
        n_mfcc = 13
        n_chroma = 12
        values = np.zeros(n_spectral + n_mfcc + n_chroma)
        if not features:
            return values
        # Spectral means
        for j, key in enumerate(keys):
            vals = [getattr(f, key, 0.0) for f in features]
            values[j] = float(np.mean(vals))
        # MFCC means
        mfcc_stack = np.array([f.mfcc[:n_mfcc] for f in features])
        values[n_spectral:n_spectral + n_mfcc] = mfcc_stack.mean(axis=0)
        # Chroma means
        chroma_stack = np.array([f.chroma[:n_chroma] for f in features])
        values[n_spectral + n_mfcc:] = chroma_stack.mean(axis=0)
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
        """Apply labelling heuristics to assign musical names.

        Uses rank-based energy comparison so that sections are labelled
        relative to each other rather than against an absolute threshold.
        """
        if not sections:
            return sections

        n = len(sections)
        energies = [s.energy_mean for s in sections]
        max_energy = max(energies) if energies else 0.0

        labels: list[str] = ["unknown"] * n

        # --- 1. Positional labels: intro / outro ---
        if n >= 2:
            first_ratio = energies[0] / max_energy if max_energy > 0 else 0
            median_energy = sorted(energies)[n // 2]
            if first_ratio < 0.5 and energies[0] < median_energy and (sections[0].end_t - sections[0].start_t) < 30.0:
                labels[0] = "intro"

        if n >= 2 and energies[-1] <= energies[-2]:
            labels[-1] = "outro"
        elif n >= 2:
            last_ratio = energies[-1] / max_energy if max_energy > 0 else 0
            if last_ratio < 0.5:
                labels[-1] = "outro"

        # --- 2. Drop detection (EDM-specific) ---
        for i in range(n):
            if labels[i] != "unknown":
                continue
            ratio = energies[i] / max_energy if max_energy > 0 else 0
            if ratio > 0.8 and sections[i].mood == "drop":
                labels[i] = "drop"

        # --- 3. Breakdown / bridge: low energy between high-energy neighbours ---
        for i in range(1, n - 1):
            if labels[i] != "unknown":
                continue
            ratio = energies[i] / max_energy if max_energy > 0 else 0
            if ratio < 0.4:
                left_ratio = energies[i - 1] / max_energy if max_energy > 0 else 0
                right_ratio = energies[i + 1] / max_energy if max_energy > 0 else 0
                if left_ratio > 0.5 and right_ratio > 0.5:
                    labels[i] = "bridge" if ratio > 0.2 else "breakdown"

        # --- 4. Rank-based chorus / verse split for remaining body sections ---
        body_indices = [i for i in range(n) if labels[i] == "unknown"]

        if body_indices:
            # Rank body sections by energy (descending); top half → chorus
            ranked = sorted(body_indices, key=lambda i: energies[i], reverse=True)
            n_chorus = max(1, len(ranked) // 2)
            chorus_set = set(ranked[:n_chorus])

            for idx in body_indices:
                labels[idx] = "chorus" if idx in chorus_set else "verse"

        # --- 5. Structural ID consistency ---
        id_labels: dict[str, str] = {}
        for i, s in enumerate(sections):
            sid = s.section_id
            if labels[i] not in ("unknown",) and sid not in id_labels:
                id_labels[sid] = labels[i]

        for i, s in enumerate(sections):
            sid = s.section_id
            if sid in id_labels and labels[i] == "unknown":
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
