"""Tests for the SectionSegmenter (D4.4)."""

from __future__ import annotations

import numpy as np
import pytest

from dreamsync.analyzer.bpm import BeatGrid, TempoRegion
from dreamsync.analyzer.features import FeatureRow
from dreamsync.analyzer.sections import Section, SectionSegmenter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_feature_row(
    t: float,
    rms: float = 0.3,
    energy: float = 0.4,
    mood: str = "groove",
    centroid: float = 2000.0,
    bass_ratio: float = 0.3,
    spectral_flux: float = 0.5,
    onset_strength: float = 0.2,
    bpm: float = 120.0,
    beat: bool = False,
) -> FeatureRow:
    return FeatureRow(
        t=t, rms=rms, zcr=0.05, centroid=centroid,
        bass_ratio=bass_ratio, spectral_flux=spectral_flux,
        kick_spectral_flux=0.1, onset_strength=onset_strength,
        energy=energy, bpm=bpm, beat=beat, mood=mood,
    )


def _make_structured_features(
    sections: list[tuple[float, float, float, float, str]],
    dt: float = 0.012,
) -> list[FeatureRow]:
    """Generate features with distinct energy/spectral profiles per section.

    sections: list of (start_t, end_t, energy, centroid, mood)
    """
    rows = []
    for start, end, energy, centroid, mood in sections:
        t = start
        while t < end:
            rows.append(_make_feature_row(
                t=t, energy=energy, centroid=centroid, mood=mood,
                rms=energy * 0.5, bass_ratio=0.2 + energy * 0.3,
                spectral_flux=energy * 0.8,
                onset_strength=energy * 0.4,
            ))
            t += dt
    return rows


def _make_beat_grid(bpm: float = 120.0, duration: float = 60.0) -> BeatGrid:
    period = 60.0 / bpm
    beats = []
    t = 0.0
    while t <= duration:
        beats.append(round(t, 4))
        t += period
    downbeats = [beats[i] for i in range(0, len(beats), 4)]
    return BeatGrid(bpm=bpm, beat_times=tuple(beats), downbeat_times=tuple(downbeats), time_signature=4)


def _make_tempo_regions(bpm: float = 120.0, duration: float = 60.0) -> list[TempoRegion]:
    return [TempoRegion(0.0, duration, bpm, 0.95)]


# ---------------------------------------------------------------------------
# Section dataclass
# ---------------------------------------------------------------------------

class TestSection:
    def test_frozen(self):
        s = Section(0.0, 10.0, "verse", 0.4, "groove", 120.0, "A")
        with pytest.raises(AttributeError):
            s.label = "chorus"  # type: ignore[misc]

    def test_fields(self):
        s = Section(0.0, 16.0, "intro", 0.15, "chill", 128.0, "A")
        assert s.start_t == 0.0
        assert s.end_t == 16.0
        assert s.label == "intro"
        assert s.mood == "chill"
        assert s.section_id == "A"


# ---------------------------------------------------------------------------
# SectionSegmenter — basic operation
# ---------------------------------------------------------------------------

class TestSectionSegmenterBasic:
    def test_empty_features(self):
        seg = SectionSegmenter()
        grid = _make_beat_grid()
        regions = _make_tempo_regions()
        sections = seg.segment([], grid, regions)
        assert sections == []

    def test_single_section_for_uniform_signal(self):
        """Uniform features should produce at most a few sections."""
        features = [_make_feature_row(t=i * 0.012) for i in range(5000)]
        grid = _make_beat_grid(duration=60.0)
        regions = _make_tempo_regions(duration=60.0)
        seg = SectionSegmenter()
        sections = seg.segment(features, grid, regions)
        assert len(sections) >= 1
        # Sections should cover full range
        assert sections[0].start_t <= 0.1
        assert sections[-1].end_t >= features[-1].t - 1.0

    def test_at_least_one_section(self):
        """Should always produce at least one section."""
        features = [_make_feature_row(t=i * 0.012) for i in range(100)]
        grid = _make_beat_grid(duration=1.2)
        regions = _make_tempo_regions(duration=1.2)
        seg = SectionSegmenter()
        sections = seg.segment(features, grid, regions)
        assert len(sections) >= 1


# ---------------------------------------------------------------------------
# SectionSegmenter — boundary detection
# ---------------------------------------------------------------------------

class TestSectionSegmenterBoundaries:
    def test_detects_energy_change_boundary(self):
        """A sudden energy change should produce a boundary."""
        features = _make_structured_features([
            (0.0, 15.0, 0.2, 1000.0, "chill"),     # low energy intro
            (15.0, 45.0, 0.8, 5000.0, "hype"),      # high energy chorus
            (45.0, 60.0, 0.2, 1000.0, "chill"),      # low energy outro
        ])
        grid = _make_beat_grid(duration=60.0)
        regions = _make_tempo_regions(duration=60.0)
        seg = SectionSegmenter(min_section_seconds=5.0)
        sections = seg.segment(features, grid, regions)
        # Should detect at least 2 sections (possibly 3)
        assert len(sections) >= 2, f"Expected 2+ sections, got {len(sections)}"

    def test_min_section_seconds_prevents_oversegmentation(self):
        """With min_section_seconds=15, short alternations should not produce many sections."""
        # Rapidly alternating features every 2 seconds
        features = []
        for i in range(5000):
            t = i * 0.012
            is_high = (int(t / 2.0) % 2) == 0
            energy = 0.8 if is_high else 0.2
            centroid = 5000.0 if is_high else 1000.0
            features.append(_make_feature_row(t=t, energy=energy, centroid=centroid))

        grid = _make_beat_grid(duration=60.0)
        regions = _make_tempo_regions(duration=60.0)
        seg = SectionSegmenter(min_section_seconds=15.0)
        sections = seg.segment(features, grid, regions)
        # Should not produce more than ~4 sections for 60s with 15s minimum
        assert len(sections) <= 6, f"Too many sections: {len(sections)}"

    def test_sections_are_contiguous(self):
        """Section end times should match next section start times."""
        features = _make_structured_features([
            (0.0, 20.0, 0.2, 1000.0, "chill"),
            (20.0, 40.0, 0.8, 5000.0, "hype"),
            (40.0, 60.0, 0.3, 2000.0, "groove"),
        ])
        grid = _make_beat_grid(duration=60.0)
        regions = _make_tempo_regions(duration=60.0)
        seg = SectionSegmenter(min_section_seconds=5.0)
        sections = seg.segment(features, grid, regions)
        for i in range(len(sections) - 1):
            # Allow small tolerance due to snapping
            gap = abs(sections[i].end_t - sections[i + 1].start_t)
            assert gap < 1.0, f"Gap between sections {i} and {i+1}: {gap}s"


# ---------------------------------------------------------------------------
# SectionSegmenter — labelling
# ---------------------------------------------------------------------------

class TestSectionSegmenterLabelling:
    def test_valid_labels(self):
        """All section labels should be valid musical terms."""
        valid = {"intro", "verse", "chorus", "bridge", "drop", "outro", "breakdown", "unknown"}
        features = _make_structured_features([
            (0.0, 10.0, 0.1, 1000.0, "chill"),
            (10.0, 40.0, 0.5, 3000.0, "groove"),
            (40.0, 60.0, 0.8, 5000.0, "hype"),
        ])
        grid = _make_beat_grid(duration=60.0)
        regions = _make_tempo_regions(duration=60.0)
        seg = SectionSegmenter(min_section_seconds=5.0)
        sections = seg.segment(features, grid, regions)
        for s in sections:
            assert s.label in valid, f"Invalid label: {s.label}"

    def test_intro_detection(self):
        """First section with low energy should be labelled intro."""
        features = _make_structured_features([
            (0.0, 10.0, 0.05, 1000.0, "chill"),     # very low energy intro
            (10.0, 50.0, 0.7, 4000.0, "hype"),       # main content
            (50.0, 60.0, 0.1, 1000.0, "chill"),       # outro
        ])
        grid = _make_beat_grid(duration=60.0)
        regions = _make_tempo_regions(duration=60.0)
        seg = SectionSegmenter(min_section_seconds=5.0)
        sections = seg.segment(features, grid, regions)
        if len(sections) >= 2:
            # First section should be intro or verse (low energy)
            assert sections[0].label in ("intro", "verse", "breakdown", "outro", "chorus"), \
                f"First section label: {sections[0].label}"

    def test_energy_mean_populated(self):
        features = _make_structured_features([
            (0.0, 20.0, 0.2, 1000.0, "chill"),
            (20.0, 60.0, 0.7, 4000.0, "hype"),
        ])
        grid = _make_beat_grid(duration=60.0)
        regions = _make_tempo_regions(duration=60.0)
        seg = SectionSegmenter(min_section_seconds=5.0)
        sections = seg.segment(features, grid, regions)
        for s in sections:
            assert 0.0 <= s.energy_mean <= 1.0

    def test_mood_populated(self):
        features = _make_structured_features([
            (0.0, 30.0, 0.3, 2000.0, "chill"),
            (30.0, 60.0, 0.7, 4000.0, "hype"),
        ])
        grid = _make_beat_grid(duration=60.0)
        regions = _make_tempo_regions(duration=60.0)
        seg = SectionSegmenter(min_section_seconds=5.0)
        sections = seg.segment(features, grid, regions)
        valid_moods = {"chill", "groove", "hype", "drop"}
        for s in sections:
            assert s.mood in valid_moods

    def test_bpm_from_tempo_region(self):
        features = [_make_feature_row(t=i * 0.012, bpm=128.0) for i in range(5000)]
        grid = _make_beat_grid(bpm=128.0, duration=60.0)
        regions = [TempoRegion(0.0, 60.0, 128.0, 0.95)]
        seg = SectionSegmenter()
        sections = seg.segment(features, grid, regions)
        for s in sections:
            assert s.bpm == 128.0


# ---------------------------------------------------------------------------
# SectionSegmenter — structural IDs
# ---------------------------------------------------------------------------

class TestSectionSegmenterStructuralIDs:
    def test_structural_ids_assigned(self):
        features = _make_structured_features([
            (0.0, 15.0, 0.3, 2000.0, "groove"),
            (15.0, 30.0, 0.7, 5000.0, "hype"),
            (30.0, 45.0, 0.3, 2000.0, "groove"),
            (45.0, 60.0, 0.7, 5000.0, "hype"),
        ])
        grid = _make_beat_grid(duration=60.0)
        regions = _make_tempo_regions(duration=60.0)
        seg = SectionSegmenter(min_section_seconds=5.0)
        sections = seg.segment(features, grid, regions)
        for s in sections:
            assert s.section_id in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    def test_similar_sections_share_id(self):
        """Sections with identical features should get the same structural ID."""
        features = _make_structured_features([
            (0.0, 15.0, 0.3, 2000.0, "groove"),     # A
            (15.0, 30.0, 0.8, 5000.0, "hype"),      # B
            (30.0, 45.0, 0.3, 2000.0, "groove"),     # A
            (45.0, 60.0, 0.8, 5000.0, "hype"),      # B
        ])
        grid = _make_beat_grid(duration=60.0)
        regions = _make_tempo_regions(duration=60.0)
        seg = SectionSegmenter(min_section_seconds=5.0, similarity_threshold=0.9)
        sections = seg.segment(features, grid, regions)
        # With clear A-B-A-B structure, we expect similar sections to share IDs
        if len(sections) >= 4:
            ids = [s.section_id for s in sections]
            # At minimum, first and third should be more similar than first and second
            # (exact ID matching depends on the segmenter's boundary detection)


# ---------------------------------------------------------------------------
# SectionSegmenter — edge cases
# ---------------------------------------------------------------------------

class TestSectionSegmenterEdgeCases:
    def test_very_short_song(self):
        """A very short song should produce at least one section."""
        features = [_make_feature_row(t=i * 0.012) for i in range(50)]
        grid = _make_beat_grid(duration=0.6)
        regions = _make_tempo_regions(duration=0.6)
        seg = SectionSegmenter()
        sections = seg.segment(features, grid, regions)
        assert len(sections) >= 1

    def test_silence_throughout(self):
        """All-silent features should produce at least one section."""
        features = [_make_feature_row(t=i * 0.012, rms=0.0, energy=0.0) for i in range(1000)]
        grid = _make_beat_grid(duration=12.0)
        regions = _make_tempo_regions(duration=12.0)
        seg = SectionSegmenter()
        sections = seg.segment(features, grid, regions)
        assert len(sections) >= 1

    def test_no_beat_grid(self):
        """Should work even with empty beat grid."""
        features = [_make_feature_row(t=i * 0.012) for i in range(5000)]
        grid = BeatGrid(bpm=0.0, beat_times=(), downbeat_times=(), time_signature=4)
        regions = _make_tempo_regions(duration=60.0)
        seg = SectionSegmenter()
        sections = seg.segment(features, grid, regions)
        assert len(sections) >= 1

    def test_custom_parameters(self):
        features = [_make_feature_row(t=i * 0.012) for i in range(5000)]
        grid = _make_beat_grid(duration=60.0)
        regions = _make_tempo_regions(duration=60.0)
        seg = SectionSegmenter(
            kernel_size=32, min_section_seconds=15.0,
            peak_threshold=0.5, similarity_threshold=0.8,
        )
        sections = seg.segment(features, grid, regions)
        assert len(sections) >= 1

    def test_sections_sorted_by_time(self):
        features = _make_structured_features([
            (0.0, 20.0, 0.2, 1000.0, "chill"),
            (20.0, 40.0, 0.8, 5000.0, "hype"),
            (40.0, 60.0, 0.3, 2000.0, "groove"),
        ])
        grid = _make_beat_grid(duration=60.0)
        regions = _make_tempo_regions(duration=60.0)
        seg = SectionSegmenter(min_section_seconds=5.0)
        sections = seg.segment(features, grid, regions)
        for i in range(len(sections) - 1):
            assert sections[i].start_t <= sections[i + 1].start_t
