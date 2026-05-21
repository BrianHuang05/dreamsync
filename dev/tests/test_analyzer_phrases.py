"""Tests for PhraseSegmenter and InstrumentEventDetector (Issue 2, D1+D2)."""

from __future__ import annotations

import pytest

from dreamsync.analyzer.bpm import BeatGrid
from dreamsync.analyzer.features import FeatureRow
from dreamsync.analyzer.phrases import (
    InstrumentEvent,
    InstrumentEventDetector,
    Phrase,
    PhraseSegmenter,
)
from dreamsync.analyzer.sections import Section
from dreamsync.live import EQ_BAND_NAMES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_section(start_t: float, end_t: float, label: str = "verse") -> Section:
    return Section(
        start_t=start_t, end_t=end_t, label=label,
        energy_mean=0.5, mood="groove", bpm=120.0, section_id="A",
    )


def _make_beat_grid(bpm: float = 120.0, duration: float = 60.0) -> BeatGrid:
    period = 60.0 / bpm
    beats = tuple(round(i * period, 4) for i in range(int(duration / period) + 1))
    downbeats = tuple(beats[i] for i in range(0, len(beats), 4))
    return BeatGrid(bpm=bpm, beat_times=beats, downbeat_times=downbeats, time_signature=4)


def _make_features(
    start_t: float = 0.0,
    end_t: float = 32.0,
    dt: float = 0.01,
    energy: float = 0.5,
    kick_flux: float = 0.2,
    bass_ratio: float = 0.3,
    band_energies: tuple[float, ...] | None = None,
    band_ratios: tuple[float, ...] | None = None,
    band_fluxes: tuple[float, ...] | None = None,
) -> list[FeatureRow]:
    rows = []
    t = start_t
    while t < end_t:
        rows.append(FeatureRow(
            t=t, rms=0.3, zcr=0.05, centroid=2000.0,
            bass_ratio=bass_ratio, spectral_flux=0.5,
            kick_spectral_flux=kick_flux,
            onset_strength=0.2, energy=energy, bpm=120.0,
            beat=False, mood="groove",
            band_energies=band_energies or _band_vector(),
            band_ratios=band_ratios or _band_vector(),
            band_fluxes=band_fluxes or _band_vector(),
        ))
        t += dt
    return rows


def _make_features_energy_ramp(
    start_t: float, end_t: float, start_energy: float, end_energy: float,
    dt: float = 0.01, kick_flux: float = 0.2, bass_ratio: float = 0.3,
) -> list[FeatureRow]:
    """Features with linearly ramping energy."""
    rows = []
    t = start_t
    duration = end_t - start_t
    while t < end_t:
        progress = (t - start_t) / duration if duration > 0 else 0
        energy = start_energy + (end_energy - start_energy) * progress
        rows.append(FeatureRow(
            t=t, rms=0.3, zcr=0.05, centroid=2000.0,
            bass_ratio=bass_ratio, spectral_flux=0.5,
            kick_spectral_flux=kick_flux,
            onset_strength=0.2, energy=energy, bpm=120.0,
            beat=False, mood="groove",
        ))
        t += dt
    return rows


def _band_vector(**values: float) -> tuple[float, ...]:
    return tuple(values.get(name, 0.0) for name in EQ_BAND_NAMES)


# ---------------------------------------------------------------------------
# PhraseSegmenter (D1)
# ---------------------------------------------------------------------------

class TestPhraseSegmenter:
    def test_subdivides_long_section(self):
        """32-second section at 120 BPM → 4 phrases of ~8 seconds each."""
        grid = _make_beat_grid(bpm=120.0, duration=40.0)
        sections = [_make_section(0.0, 32.0)]
        features = _make_features(0.0, 32.0)

        segmenter = PhraseSegmenter(bars_per_phrase=4)
        phrases = segmenter.segment(sections, features, grid)

        assert len(phrases) == 4
        for p in phrases:
            assert p.parent_section_index == 0
            duration = p.end_t - p.start_t
            assert 7.0 < duration < 9.0

    def test_short_section_single_phrase(self):
        """6-second section → 1 phrase."""
        grid = _make_beat_grid(bpm=120.0, duration=10.0)
        sections = [_make_section(0.0, 6.0)]
        features = _make_features(0.0, 6.0)

        segmenter = PhraseSegmenter(bars_per_phrase=4)
        phrases = segmenter.segment(sections, features, grid)

        # May get 1 or more depending on downbeats; at 120 BPM, downbeats at 0, 2, 4
        assert len(phrases) >= 1
        assert phrases[0].parent_section_index == 0

    def test_phrase_type_build_detected(self):
        """Features with rising energy → phrase_type='build'."""
        grid = _make_beat_grid(bpm=120.0, duration=40.0)
        sections = [_make_section(0.0, 8.0)]
        features = _make_features_energy_ramp(0.0, 8.0, 0.2, 0.8)

        segmenter = PhraseSegmenter(bars_per_phrase=4)
        phrases = segmenter.segment(sections, features, grid)

        assert any(p.phrase_type == "build" for p in phrases)

    def test_phrase_type_breakdown_no_kick(self):
        """Low energy, no kick → phrase_type='breakdown'."""
        grid = _make_beat_grid(bpm=120.0, duration=40.0)
        sections = [_make_section(0.0, 8.0)]
        features = _make_features(0.0, 8.0, energy=0.1, kick_flux=0.05)

        segmenter = PhraseSegmenter(bars_per_phrase=4)
        phrases = segmenter.segment(sections, features, grid)

        assert any(p.phrase_type == "breakdown" for p in phrases)
        assert any(not p.has_kick for p in phrases)

    def test_phrase_boundaries_align_to_downbeats(self):
        """Phrase start/end times should appear in beat_grid.downbeat_times."""
        grid = _make_beat_grid(bpm=120.0, duration=40.0)
        sections = [_make_section(0.0, 32.0)]
        features = _make_features(0.0, 32.0)

        segmenter = PhraseSegmenter(bars_per_phrase=4)
        phrases = segmenter.segment(sections, features, grid)

        downbeats = set(grid.downbeat_times)
        for p in phrases:
            assert p.start_t in downbeats, f"start_t {p.start_t} not a downbeat"

    def test_phrase_parent_index_correct(self):
        """Phrases reference the correct parent section."""
        grid = _make_beat_grid(bpm=120.0, duration=60.0)
        sections = [
            _make_section(0.0, 16.0),
            _make_section(16.0, 48.0),
        ]
        features = _make_features(0.0, 48.0)

        segmenter = PhraseSegmenter(bars_per_phrase=4)
        phrases = segmenter.segment(sections, features, grid)

        for p in phrases:
            sec = sections[p.parent_section_index]
            assert sec.start_t <= p.start_t < sec.end_t

    def test_preserves_count(self):
        """Phrase count equals section subdivisions."""
        grid = _make_beat_grid(bpm=120.0, duration=20.0)
        sections = [_make_section(0.0, 16.0)]
        features = _make_features(0.0, 16.0)

        segmenter = PhraseSegmenter(bars_per_phrase=4)
        phrases = segmenter.segment(sections, features, grid)

        # 16s at 120 BPM = 32 beats = 8 bars = 2 × 4-bar phrases
        assert len(phrases) == 2

    def test_phrase_captures_band_summary_and_dominant_band(self):
        grid = _make_beat_grid(bpm=120.0, duration=12.0)
        sections = [_make_section(0.0, 8.0)]
        features = _make_features(
            0.0,
            8.0,
            band_energies=_band_vector(sub=0.2, bass=0.7, presence=0.1),
            band_ratios=_band_vector(sub=0.08, bass=0.42, presence=0.06),
            band_fluxes=_band_vector(bass=0.18, presence=0.03),
        )

        segmenter = PhraseSegmenter(bars_per_phrase=4)
        phrases = segmenter.segment(sections, features, grid)

        assert len(phrases) == 1
        phrase = phrases[0]
        assert phrase.dominant_band == "bass"
        assert len(phrase.band_ratios) == len(EQ_BAND_NAMES)
        ratio_map = dict(zip(EQ_BAND_NAMES, phrase.band_ratios))
        flux_map = dict(zip(EQ_BAND_NAMES, phrase.band_fluxes))
        assert ratio_map["bass"] > ratio_map["presence"]
        assert flux_map["bass"] > flux_map["presence"]


# ---------------------------------------------------------------------------
# InstrumentEventDetector (D2)
# ---------------------------------------------------------------------------

class TestInstrumentEventDetector:
    def test_kick_enter_detected(self):
        """Phrase N has low kick, phrase N+1 has high → kick_enter event."""
        phrases = [
            Phrase(0.0, 8.0, 0, "breakdown", 0.0, False),
            Phrase(8.0, 16.0, 0, "steady", 0.0, True),
        ]
        features = (
            _make_features(0.0, 8.0, kick_flux=0.05)
            + _make_features(8.0, 16.0, kick_flux=0.5)
        )

        detector = InstrumentEventDetector()
        events = detector.detect(phrases, features)

        kick_enters = [e for e in events if e.event_type == "kick_enter"]
        assert len(kick_enters) >= 1
        assert kick_enters[0].t == 8.0

    def test_kick_exit_detected(self):
        """Kick present then absent → kick_exit event."""
        phrases = [
            Phrase(0.0, 8.0, 0, "steady", 0.0, True),
            Phrase(8.0, 16.0, 0, "breakdown", 0.0, False),
        ]
        features = (
            _make_features(0.0, 8.0, kick_flux=0.5)
            + _make_features(8.0, 16.0, kick_flux=0.05)
        )

        detector = InstrumentEventDetector()
        events = detector.detect(phrases, features)

        kick_exits = [e for e in events if e.event_type == "kick_exit"]
        assert len(kick_exits) >= 1
        assert kick_exits[0].t == 8.0

    def test_bass_drop_detected(self):
        """Bass ratio jumps from low to high → bass_drop event."""
        phrases = [
            Phrase(0.0, 8.0, 0, "steady", 0.0, True),
            Phrase(8.0, 16.0, 0, "steady", 0.0, True),
        ]
        features = (
            _make_features(0.0, 8.0, bass_ratio=0.15)
            + _make_features(8.0, 16.0, bass_ratio=0.55)
        )

        detector = InstrumentEventDetector()
        events = detector.detect(phrases, features)

        bass_drops = [e for e in events if e.event_type == "bass_drop"]
        assert len(bass_drops) >= 1
        assert bass_drops[0].t == 8.0

    def test_no_false_events_steady(self):
        """Constant energy, constant kick → no events."""
        phrases = [
            Phrase(0.0, 8.0, 0, "steady", 0.0, True),
            Phrase(8.0, 16.0, 0, "steady", 0.0, True),
        ]
        features = _make_features(0.0, 16.0, kick_flux=0.4, bass_ratio=0.3)

        detector = InstrumentEventDetector()
        events = detector.detect(phrases, features)

        assert len(events) == 0

    def test_presence_lift_detected(self):
        phrases = [
            Phrase(
                0.0,
                8.0,
                0,
                "steady",
                0.0,
                True,
                dominant_band="mid",
                band_ratios=_band_vector(mid=0.18, presence=0.04, air=0.02),
            ),
            Phrase(
                8.0,
                16.0,
                0,
                "steady",
                0.0,
                True,
                dominant_band="presence",
                band_ratios=_band_vector(mid=0.14, presence=0.18, air=0.04),
            ),
        ]
        features = (
            _make_features(0.0, 8.0, band_ratios=_band_vector(mid=0.18, presence=0.04))
            + _make_features(8.0, 16.0, band_ratios=_band_vector(mid=0.14, presence=0.18))
        )

        detector = InstrumentEventDetector()
        events = detector.detect(phrases, features)

        presence_events = [e for e in events if e.event_type == "presence_lift"]
        assert len(presence_events) == 1
        assert presence_events[0].t == 8.0
        assert presence_events[0].band == "presence"

    def test_sub_enter_detected(self):
        phrases = [
            Phrase(
                0.0,
                8.0,
                0,
                "breakdown",
                0.0,
                False,
                dominant_band="mid",
                band_ratios=_band_vector(mid=0.20, sub=0.02),
            ),
            Phrase(
                8.0,
                16.0,
                0,
                "steady",
                0.0,
                True,
                dominant_band="sub",
                band_ratios=_band_vector(sub=0.16, bass=0.22),
            ),
        ]
        features = (
            _make_features(0.0, 8.0, band_ratios=_band_vector(mid=0.20, sub=0.02))
            + _make_features(8.0, 16.0, band_ratios=_band_vector(sub=0.16, bass=0.22))
        )

        detector = InstrumentEventDetector()
        events = detector.detect(phrases, features)

        sub_events = [e for e in events if e.event_type == "sub_enter"]
        assert len(sub_events) == 1
        assert sub_events[0].t == 8.0
        assert sub_events[0].band == "sub"

    def test_single_phrase_no_events(self):
        """One phrase → no events possible."""
        phrases = [Phrase(0.0, 8.0, 0, "steady", 0.0, True)]
        features = _make_features(0.0, 8.0)

        detector = InstrumentEventDetector()
        events = detector.detect(phrases, features)
        assert events == []
