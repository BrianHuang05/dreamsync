"""Tests for SongStructure model, serialisation, and analyze_song orchestrator (D4.5)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dreamsync.analyzer.bpm import BeatGrid, TempoRegion
from dreamsync.analyzer.instruments import InstrumentProxy
from dreamsync.analyzer.models import SongStructure
from dreamsync.analyzer.phrases import InstrumentEvent, Phrase
from dreamsync.analyzer.sections import Section
from dreamsync.live import EQ_BAND_NAMES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_song_structure(
    path: str = "test.mp3",
    duration: float = 180.0,
    bpm: float = 128.0,
) -> SongStructure:
    beat_period = 60.0 / bpm
    beats = tuple(round(i * beat_period, 4) for i in range(int(duration / beat_period)))
    downbeats = tuple(beats[i] for i in range(0, len(beats), 4))

    return SongStructure(
        path=path,
        duration=duration,
        bpm=bpm,
        time_signature=4,
        beat_grid=BeatGrid(
            bpm=bpm,
            beat_times=beats,
            downbeat_times=downbeats,
            time_signature=4,
        ),
        tempo_regions=(
            TempoRegion(0.0, duration, bpm, 0.95),
        ),
        sections=(
            Section(0.0, 16.0, "intro", 0.15, "chill", bpm, "A"),
            Section(16.0, 64.0, "verse", 0.35, "groove", bpm, "B"),
            Section(64.0, 112.0, "chorus", 0.70, "hype", bpm, "C"),
            Section(112.0, 144.0, "verse", 0.35, "groove", bpm, "B"),
            Section(144.0, 170.0, "chorus", 0.72, "hype", bpm, "C"),
            Section(170.0, 180.0, "outro", 0.10, "chill", bpm, "D"),
        ),
        metadata={"track_name": "Test Song", "artist": "Test Artist"},
    )


def _band_vector(**values: float) -> tuple[float, ...]:
    return tuple(values.get(name, 0.0) for name in EQ_BAND_NAMES)


# ---------------------------------------------------------------------------
# SongStructure dataclass
# ---------------------------------------------------------------------------

class TestSongStructure:
    def test_frozen(self):
        ss = _make_song_structure()
        with pytest.raises(AttributeError):
            ss.bpm = 130.0  # type: ignore[misc]

    def test_fields(self):
        ss = _make_song_structure(bpm=120.0)
        assert ss.bpm == 120.0
        assert ss.time_signature == 4
        assert len(ss.sections) == 6
        assert ss.metadata["artist"] == "Test Artist"


# ---------------------------------------------------------------------------
# Serialisation — to_dict / to_json / from_json
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_to_dict_returns_valid_json_types(self):
        ss = _make_song_structure()
        d = ss.to_dict()
        # Should be JSON-serialisable
        json_str = json.dumps(d)
        assert len(json_str) > 100

    def test_to_dict_fields(self):
        ss = _make_song_structure(bpm=128.0)
        d = ss.to_dict()
        assert d["bpm"] == 128.0
        assert d["time_signature"] == 4
        assert len(d["sections"]) == 6
        assert d["sections"][0]["label"] == "intro"
        assert d["beat_grid"]["bpm"] == 128.0
        assert isinstance(d["beat_grid"]["beat_times"], list)

    def test_to_json_creates_file(self, tmp_path: Path):
        ss = _make_song_structure()
        out = tmp_path / "analysis.json"
        ss.to_json(out)
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["bpm"] == 128.0

    def test_from_json_roundtrip(self, tmp_path: Path):
        ss = _make_song_structure()
        out = tmp_path / "analysis.json"
        ss.to_json(out)
        loaded = SongStructure.from_json(out)
        assert loaded.bpm == ss.bpm
        assert loaded.duration == ss.duration
        assert loaded.time_signature == ss.time_signature
        assert len(loaded.sections) == len(ss.sections)
        assert loaded.sections[0].label == ss.sections[0].label
        assert loaded.beat_grid.bpm == ss.beat_grid.bpm
        assert len(loaded.beat_grid.beat_times) == len(ss.beat_grid.beat_times)
        assert len(loaded.tempo_regions) == len(ss.tempo_regions)
        assert loaded.metadata == ss.metadata

    def test_from_dict_roundtrip(self):
        ss = _make_song_structure()
        d = ss.to_dict()
        loaded = SongStructure.from_dict(d)
        assert loaded.bpm == ss.bpm
        assert loaded.path == ss.path

    def test_phrase_band_fields_roundtrip(self):
        ss = replace(
            _make_song_structure(),
            phrases=(
                Phrase(
                    start_t=16.0,
                    end_t=24.0,
                    parent_section_index=1,
                    phrase_type="steady",
                    energy_delta=0.05,
                    has_kick=True,
                    band_energies=_band_vector(sub=0.1, bass=0.5),
                    band_ratios=_band_vector(sub=0.08, bass=0.32, presence=0.05),
                    band_fluxes=_band_vector(bass=0.12),
                    dominant_band="bass",
                ),
            ),
            instrument_events=(
                InstrumentEvent(
                    t=24.0,
                    event_type="presence_lift",
                    confidence=0.88,
                    band="presence",
                ),
            ),
            instrument_proxies=(
                InstrumentProxy(
                    start_t=16.0,
                    end_t=24.0,
                    parent_section_index=1,
                    dominant_proxy="bass",
                    drums=0.21,
                    bass=0.82,
                    vocals=0.18,
                    harmonic=0.24,
                    percussive=0.29,
                    pan_center=-0.35,
                    pan_width=0.42,
                    band_pan_centers=_band_vector(sub=-0.4, bass=-0.22, presence=0.18),
                ),
            ),
        )

        d = ss.to_dict()
        assert d["phrases"][0]["dominant_band"] == "bass"
        assert d["instrument_events"][0]["band"] == "presence"
        assert d["instrument_proxies"][0]["dominant_proxy"] == "bass"
        assert d["instrument_proxies"][0]["pan_center"] == -0.35

        loaded = SongStructure.from_dict(d)
        assert loaded.phrases[0].dominant_band == "bass"
        assert loaded.phrases[0].band_ratios == _band_vector(sub=0.08, bass=0.32, presence=0.05)
        assert loaded.instrument_events[0].band == "presence"
        assert loaded.instrument_proxies[0].bass == pytest.approx(0.82)
        assert loaded.instrument_proxies[0].dominant_proxy == "bass"
        assert loaded.instrument_proxies[0].pan_center == pytest.approx(-0.35)
        assert loaded.instrument_proxies[0].band_pan_centers == _band_vector(
            sub=-0.4,
            bass=-0.22,
            presence=0.18,
        )

    def test_to_json_creates_parent_dirs(self, tmp_path: Path):
        ss = _make_song_structure()
        out = tmp_path / "subdir" / "nested" / "analysis.json"
        ss.to_json(out)
        assert out.exists()

    def test_float_precision(self):
        ss = _make_song_structure()
        d = ss.to_dict()
        # Durations should have at most 4 decimal places
        assert isinstance(d["duration"], float)
        # BPM should have at most 2 decimal places
        bpm_str = str(d["bpm"])
        if "." in bpm_str:
            assert len(bpm_str.split(".")[1]) <= 2


# ---------------------------------------------------------------------------
# analyze_song orchestrator (mocked)
# ---------------------------------------------------------------------------

class TestAnalyzeSongMocked:
    def test_analyze_song_wires_pipeline(self, tmp_path: Path):
        """Mocked analyze_song chains decode → features → bpm → sections."""
        from dreamsync.analyzer.features import FeatureRow

        fake_mp3 = tmp_path / "test.mp3"
        fake_mp3.write_bytes(b"\xff\xfb" * 100)

        # Mock the decode step
        mock_audio = MagicMock()
        mock_audio.signal = np.random.randn(44100 * 5).astype(np.float32) * 0.3
        mock_audio.sample_rate = 44100
        mock_audio.duration = 5.0
        mock_audio.channels = 2

        with patch("dreamsync.analyzer.analyze.decode_mp3", return_value=mock_audio):
            from dreamsync.analyzer.analyze import analyze_song
            result = analyze_song(fake_mp3)

        assert isinstance(result, SongStructure)
        assert result.duration == 5.0
        assert result.bpm >= 0.0
        assert len(result.sections) >= 1

    def test_analyze_song_with_metadata(self, tmp_path: Path):
        fake_mp3 = tmp_path / "test.mp3"
        fake_mp3.write_bytes(b"\xff\xfb" * 100)

        mock_audio = MagicMock()
        mock_audio.signal = np.random.randn(44100 * 3).astype(np.float32) * 0.3
        mock_audio.sample_rate = 44100
        mock_audio.duration = 3.0
        mock_audio.channels = 2

        with patch("dreamsync.analyzer.analyze.decode_mp3", return_value=mock_audio):
            from dreamsync.analyzer.analyze import analyze_song
            result = analyze_song(
                fake_mp3,
                metadata={"track_name": "Around the World", "artist": "Daft Punk"},
            )

        assert result.metadata["artist"] == "Daft Punk"

    def test_analyze_song_records_optional_stem_backend_metadata(self, tmp_path: Path):
        from dreamsync.analyzer.separation import StemArtifact

        fake_mp3 = tmp_path / "test.mp3"
        fake_mp3.write_bytes(b"\xff\xfb" * 100)

        mock_audio = MagicMock()
        mock_audio.signal = np.random.randn(44100 * 3).astype(np.float32) * 0.3
        mock_audio.sample_rate = 44100
        mock_audio.duration = 3.0
        mock_audio.channels = 2

        class DummyStemBackend:
            @property
            def backend_name(self) -> str:
                return "dummy"

            def separate(self, audio_path: Path, audio) -> dict[str, StemArtifact]:
                return {
                    "vocals": StemArtifact(
                        name="vocals",
                        sample_rate=audio.sample_rate,
                        path=str(audio_path.with_suffix(".vocals.wav")),
                    ),
                }

        with patch("dreamsync.analyzer.analyze.decode_mp3", return_value=mock_audio):
            from dreamsync.analyzer.analyze import analyze_song
            result = analyze_song(fake_mp3, stem_backend=DummyStemBackend())

        assert result.metadata["stem_backend"] == "dummy"
        assert result.metadata["available_stems"] == ["vocals"]

    def test_analyze_song_json_output(self, tmp_path: Path):
        fake_mp3 = tmp_path / "test.mp3"
        fake_mp3.write_bytes(b"\xff\xfb" * 100)

        mock_audio = MagicMock()
        mock_audio.signal = np.random.randn(44100 * 3).astype(np.float32) * 0.3
        mock_audio.sample_rate = 44100
        mock_audio.duration = 3.0
        mock_audio.channels = 2

        with patch("dreamsync.analyzer.analyze.decode_mp3", return_value=mock_audio):
            from dreamsync.analyzer.analyze import analyze_song
            result = analyze_song(fake_mp3)

        out = tmp_path / "output.json"
        result.to_json(out)
        loaded = SongStructure.from_json(out)
        assert loaded.bpm == result.bpm
        assert len(loaded.sections) == len(result.sections)


# ---------------------------------------------------------------------------
# CLI integration (mocked)
# ---------------------------------------------------------------------------

class TestCLIAnalyze:
    def test_analyze_command_summary(self, tmp_path: Path, capsys):
        """CLI analyze --summary should print human-readable output."""
        from dreamsync.cli import main

        fake_mp3 = tmp_path / "test.mp3"
        fake_mp3.write_bytes(b"\xff\xfb" * 100)

        mock_audio = MagicMock()
        mock_audio.signal = np.random.randn(44100 * 5).astype(np.float32) * 0.3
        mock_audio.sample_rate = 44100
        mock_audio.duration = 5.0
        mock_audio.channels = 2

        with patch("dreamsync.analyzer.analyze.decode_mp3", return_value=mock_audio):
            ret = main(["analyze", str(fake_mp3), "--summary"])

        assert ret == 0
        captured = capsys.readouterr()
        assert "BPM:" in captured.out
        assert "Duration:" in captured.out
        assert "Sections" in captured.out

    def test_analyze_command_json_output(self, tmp_path: Path):
        from dreamsync.cli import main

        fake_mp3 = tmp_path / "test.mp3"
        fake_mp3.write_bytes(b"\xff\xfb" * 100)
        out = tmp_path / "result.json"

        mock_audio = MagicMock()
        mock_audio.signal = np.random.randn(44100 * 5).astype(np.float32) * 0.3
        mock_audio.sample_rate = 44100
        mock_audio.duration = 5.0
        mock_audio.channels = 2

        with patch("dreamsync.analyzer.analyze.decode_mp3", return_value=mock_audio):
            ret = main(["analyze", str(fake_mp3), "--output", str(out)])

        assert ret == 0
        assert out.exists()
        data = json.loads(out.read_text())
        assert "bpm" in data
        assert "sections" in data
