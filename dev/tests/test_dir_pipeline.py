"""Tests for DirectoryPipeline — scan -> analyze -> compile -> play."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dreamsync.analyzer.bpm import BeatGrid
from dreamsync.analyzer.models import SongStructure
from dreamsync.cache import ShowCache, sidecar_track_id, track_id_for_capture
from dreamsync.capture.scanner import CaptureTrack
from dreamsync.dir_pipeline import DirectoryPipeline, PipelineResult, TrackResult
from dreamsync.show.models import ShowCue, ShowTimeline


def _write_mp3(path):
    path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 100)


def _write_sidecar(path, **kwargs):
    defaults = {
        "segmentIndex": 0,
        "songTitle": "Test Song",
        "artist": "Test Artist",
        "album": "Test Album",
        "segmentDurationSeconds": 180.0,
    }
    defaults.update(kwargs)
    path.write_text(json.dumps(defaults), encoding="utf-8")


def _make_structure(**kwargs) -> SongStructure:
    defaults = dict(
        path="test.mp3",
        duration=180.0,
        bpm=128.0,
        time_signature=4,
        beat_grid=BeatGrid(
            bpm=128.0,
            beat_times=tuple(i * 0.46875 for i in range(384)),
            downbeat_times=tuple(i * 1.875 for i in range(96)),
            time_signature=4,
        ),
        tempo_regions=(),
        sections=(),
        metadata={},
    )
    defaults.update(kwargs)
    return SongStructure(**defaults)


def _make_timeline(**kwargs) -> ShowTimeline:
    defaults = dict(
        song_path="test.mp3",
        duration=180.0,
        bpm=128.0,
        time_signature=4,
        beat_times=tuple(i * 0.46875 for i in range(384)),
        downbeat_times=tuple(i * 1.875 for i in range(96)),
        cues=(
            ShowCue(t=0.0, render_mode="scroll", color_palette=("#FF0000",),
                    intensity=0.5, speed=1.0, params={}, transition="cut",
                    transition_beats=0),
        ),
        metadata={"track_name": "Test Song", "artist": "Test Artist"},
    )
    defaults.update(kwargs)
    return ShowTimeline(**defaults)


def _setup_track(tmp_path, name="track_001", sidecar=True, **sidecar_kwargs):
    mp3_path = tmp_path / f"{name}.mp3"
    _write_mp3(mp3_path)
    if sidecar:
        _write_sidecar(tmp_path / f"{name}.json", **sidecar_kwargs)
    return mp3_path


class TestDirectoryPipeline:

    @patch("dreamsync.dir_pipeline.analyze_song")
    def test_prepare_empty_dir(self, mock_analyze, tmp_path):
        cache = ShowCache(tmp_path / "cache")
        pipeline = DirectoryPipeline(tmp_path, cache=cache)
        result = pipeline.prepare()
        assert result.tracks == []
        assert result.analyzed == 0
        assert result.compiled == 0
        mock_analyze.assert_not_called()

    @patch("dreamsync.dir_pipeline.cached_compile_show")
    @patch("dreamsync.dir_pipeline.analyze_song")
    def test_prepare_single_track(self, mock_analyze, mock_compile, tmp_path):
        _setup_track(tmp_path)
        structure = _make_structure()
        timeline = _make_timeline()
        mock_analyze.return_value = structure
        mock_compile.return_value = (timeline, False)

        cache = ShowCache(tmp_path / "cache")
        pipeline = DirectoryPipeline(tmp_path, cache=cache)
        result = pipeline.prepare()

        assert len(result.tracks) == 1
        assert result.analyzed == 1
        assert result.compiled == 1
        assert result.errors == 0
        mock_analyze.assert_called_once()
        mock_compile.assert_called_once()

    @patch("dreamsync.dir_pipeline.cached_compile_show")
    @patch("dreamsync.dir_pipeline.analyze_song")
    def test_prepare_uses_sidecar_metadata(self, mock_analyze, mock_compile, tmp_path):
        _setup_track(tmp_path, songTitle="My Song", artist="My Artist", album="My Album")
        mock_analyze.return_value = _make_structure()
        mock_compile.return_value = (_make_timeline(), False)

        cache = ShowCache(tmp_path / "cache")
        pipeline = DirectoryPipeline(tmp_path, cache=cache)
        pipeline.prepare()

        call_kwargs = mock_analyze.call_args
        metadata = call_kwargs.kwargs.get("metadata") or call_kwargs[1].get("metadata")
        assert metadata["track_name"] == "My Song"
        assert metadata["artist"] == "My Artist"
        assert metadata["album"] == "My Album"

    @patch("dreamsync.dir_pipeline.analyze_song")
    def test_prepare_cache_hit_skips_analysis(self, mock_analyze, tmp_path):
        _setup_track(tmp_path, songTitle="Cached Song", artist="Cached Artist")
        timeline = _make_timeline()

        cache = ShowCache(tmp_path / "cache")
        # Pre-populate cache
        track_id = sidecar_track_id("Cached Song", "Cached Artist")
        cache.put(track_id, timeline)

        pipeline = DirectoryPipeline(tmp_path, cache=cache)
        result = pipeline.prepare()

        assert len(result.tracks) == 1
        assert result.cache_hits == 1
        assert result.tracks[0].from_cache is True
        mock_analyze.assert_not_called()

    @patch("dreamsync.dir_pipeline.cached_compile_show")
    @patch("dreamsync.dir_pipeline.analyze_song")
    def test_prepare_cache_miss_analyzes_and_caches(self, mock_analyze, mock_compile, tmp_path):
        _setup_track(tmp_path, songTitle="New Song", artist="New Artist")
        structure = _make_structure()
        timeline = _make_timeline()
        mock_analyze.return_value = structure
        mock_compile.return_value = (timeline, False)

        cache = ShowCache(tmp_path / "cache")
        pipeline = DirectoryPipeline(tmp_path, cache=cache)
        result = pipeline.prepare()

        assert result.cache_hits == 0
        assert result.compiled == 1
        mock_analyze.assert_called_once()
        mock_compile.assert_called_once()

    @patch("dreamsync.dir_pipeline.cached_compile_show")
    @patch("dreamsync.dir_pipeline.analyze_song")
    def test_prepare_analysis_failure_continues(self, mock_analyze, mock_compile, tmp_path):
        _setup_track(tmp_path, name="good_track", songTitle="Good", artist="Artist",
                     segmentIndex=0)
        _setup_track(tmp_path, name="bad_track", songTitle="Bad", artist="Artist",
                     segmentIndex=1)

        structure = _make_structure()
        timeline = _make_timeline()

        def side_effect(path, **kwargs):
            if "bad" in str(path):
                raise RuntimeError("decode failed")
            return structure

        mock_analyze.side_effect = side_effect
        mock_compile.return_value = (timeline, False)

        cache = ShowCache(tmp_path / "cache")
        pipeline = DirectoryPipeline(tmp_path, cache=cache)
        result = pipeline.prepare()

        assert len(result.tracks) == 2
        assert result.errors == 1
        assert result.analyzed == 1
        # Find the error track
        error_track = [t for t in result.tracks if t.error][0]
        assert "decode failed" in error_track.error

    @patch("dreamsync.dir_pipeline.cached_compile_show")
    @patch("dreamsync.dir_pipeline.analyze_song")
    def test_prepare_multiple_tracks(self, mock_analyze, mock_compile, tmp_path):
        for i in range(3):
            _setup_track(tmp_path, name=f"track_{i:03d}",
                         songTitle=f"Song {i}", artist="Artist",
                         segmentIndex=i)

        mock_analyze.return_value = _make_structure()
        mock_compile.return_value = (_make_timeline(), False)

        cache = ShowCache(tmp_path / "cache")
        pipeline = DirectoryPipeline(tmp_path, cache=cache)
        result = pipeline.prepare()

        assert len(result.tracks) == 3
        assert result.analyzed == 3
        assert result.compiled == 3
        assert result.errors == 0

    @patch("dreamsync.dir_pipeline.cached_compile_show")
    @patch("dreamsync.dir_pipeline.analyze_song")
    def test_prepare_progress_callback(self, mock_analyze, mock_compile, tmp_path):
        for i in range(2):
            _setup_track(tmp_path, name=f"track_{i:03d}",
                         songTitle=f"Song {i}", artist="Artist",
                         segmentIndex=i)

        mock_analyze.return_value = _make_structure()
        mock_compile.return_value = (_make_timeline(), False)

        progress_calls = []

        def on_progress(step, index, total, track):
            progress_calls.append((step, index, total, track.song_title))

        cache = ShowCache(tmp_path / "cache")
        pipeline = DirectoryPipeline(tmp_path, cache=cache, on_progress=on_progress)
        pipeline.prepare()

        assert len(progress_calls) == 2
        assert progress_calls[0][1] == 0  # index
        assert progress_calls[0][2] == 2  # total
        assert progress_calls[1][1] == 1

    @patch("dreamsync.dir_pipeline.cached_compile_show")
    @patch("dreamsync.dir_pipeline.analyze_song")
    def test_playable_tracks_excludes_errors(self, mock_analyze, mock_compile, tmp_path):
        _setup_track(tmp_path, name="good", songTitle="Good", artist="A", segmentIndex=0)
        _setup_track(tmp_path, name="bad", songTitle="Bad", artist="A", segmentIndex=1)

        def side_effect(path, **kwargs):
            if "bad" in str(path):
                raise RuntimeError("fail")
            return _make_structure()

        mock_analyze.side_effect = side_effect
        mock_compile.return_value = (_make_timeline(), False)

        cache = ShowCache(tmp_path / "cache")
        pipeline = DirectoryPipeline(tmp_path, cache=cache)
        pipeline.prepare()

        playable = pipeline.playable_tracks()
        assert len(playable) == 1
        assert "good" in playable[0].name

    @patch("dreamsync.dir_pipeline.cached_compile_show")
    @patch("dreamsync.dir_pipeline.analyze_song")
    def test_playable_tracks_preserves_order(self, mock_analyze, mock_compile, tmp_path):
        for i in range(3):
            _setup_track(tmp_path, name=f"track_{i:03d}",
                         songTitle=f"Song {i}", artist="Artist",
                         segmentIndex=i)

        mock_analyze.return_value = _make_structure()
        mock_compile.return_value = (_make_timeline(), False)

        cache = ShowCache(tmp_path / "cache")
        pipeline = DirectoryPipeline(tmp_path, cache=cache)
        pipeline.prepare()

        playable = pipeline.playable_tracks()
        assert len(playable) == 3
        names = [p.name for p in playable]
        assert names == ["track_000.mp3", "track_001.mp3", "track_002.mp3"]

    @patch("dreamsync.dir_pipeline.cached_compile_show")
    @patch("dreamsync.dir_pipeline.analyze_song")
    def test_prepare_writes_analysis_json(self, mock_analyze, mock_compile, tmp_path):
        _setup_track(tmp_path)
        structure = _make_structure()
        mock_analyze.return_value = structure
        mock_compile.return_value = (_make_timeline(), False)

        cache = ShowCache(tmp_path / "cache")
        pipeline = DirectoryPipeline(tmp_path, cache=cache)
        pipeline.prepare()

        analysis_path = tmp_path / "track_001.analysis.json"
        assert analysis_path.exists()
        data = json.loads(analysis_path.read_text(encoding="utf-8"))
        assert data["bpm"] == 128.0
        assert data["duration"] == 180.0

    @patch("dreamsync.dir_pipeline.cached_compile_show")
    @patch("dreamsync.dir_pipeline.analyze_song")
    def test_prepare_reanalyzes_even_if_analysis_json_exists(self, mock_analyze, mock_compile, tmp_path):
        _setup_track(tmp_path)
        # Pre-create analysis file
        (tmp_path / "track_001.analysis.json").write_text("{}", encoding="utf-8")

        mock_analyze.return_value = _make_structure()
        mock_compile.return_value = (_make_timeline(), False)

        cache = ShowCache(tmp_path / "cache")
        pipeline = DirectoryPipeline(tmp_path, cache=cache)
        pipeline.prepare()

        # Should still call analyze (analysis is cheap, cache handles compile)
        mock_analyze.assert_called_once()


# ---------------------------------------------------------------------------
# Integration tests (Step 5)
# ---------------------------------------------------------------------------


class TestPipelineIntegration:

    @patch("dreamsync.dir_pipeline.cached_compile_show")
    @patch("dreamsync.dir_pipeline.analyze_song")
    def test_pipeline_analyze_only_writes_analysis_files(self, mock_analyze, mock_compile, tmp_path):
        for i in range(2):
            _setup_track(tmp_path, name=f"track_{i:03d}",
                         songTitle=f"Song {i}", artist="Artist",
                         segmentIndex=i)

        mock_analyze.return_value = _make_structure()
        mock_compile.return_value = (_make_timeline(), False)

        cache = ShowCache(tmp_path / "cache")
        pipeline = DirectoryPipeline(tmp_path, cache=cache)
        pipeline.prepare()

        for i in range(2):
            assert (tmp_path / f"track_{i:03d}.analysis.json").exists()

    @patch("dreamsync.dir_pipeline.cached_compile_show")
    @patch("dreamsync.dir_pipeline.analyze_song")
    def test_pipeline_compile_caches_shows(self, mock_analyze, mock_compile, tmp_path):
        _setup_track(tmp_path, songTitle="Cached Song", artist="Artist")
        timeline = _make_timeline()
        mock_analyze.return_value = _make_structure()

        cache = ShowCache(tmp_path / "cache")
        mock_compile.return_value = (timeline, False)

        pipeline = DirectoryPipeline(tmp_path, cache=cache)
        result = pipeline.prepare()
        assert result.compiled == 1

    @patch("dreamsync.dir_pipeline.cached_compile_show")
    @patch("dreamsync.dir_pipeline.analyze_song")
    def test_pipeline_playable_tracks_order(self, mock_analyze, mock_compile, tmp_path):
        for i in range(3):
            _setup_track(tmp_path, name=f"track_{i:03d}",
                         songTitle=f"Song {i}", artist="Artist",
                         segmentIndex=i)

        mock_analyze.return_value = _make_structure()
        mock_compile.return_value = (_make_timeline(), False)

        cache = ShowCache(tmp_path / "cache")
        pipeline = DirectoryPipeline(tmp_path, cache=cache)
        pipeline.prepare()

        playable = pipeline.playable_tracks()
        assert [p.name for p in playable] == [
            "track_000.mp3", "track_001.mp3", "track_002.mp3"
        ]

    @patch("dreamsync.dir_pipeline.analyze_song")
    def test_pipeline_sidecar_cache_reuse(self, mock_analyze, tmp_path):
        _setup_track(tmp_path, songTitle="Same Song", artist="Same Artist")
        structure = _make_structure()
        timeline = _make_timeline()
        mock_analyze.return_value = structure

        cache = ShowCache(tmp_path / "cache")
        # Pre-populate cache with sidecar-based ID
        track_id = sidecar_track_id("Same Song", "Same Artist")
        cache.put(track_id, timeline)

        pipeline = DirectoryPipeline(tmp_path, cache=cache)
        result = pipeline.prepare()

        assert result.cache_hits == 1
        assert result.compiled == 0
        mock_analyze.assert_not_called()

    @patch("dreamsync.dir_pipeline.cached_compile_show")
    @patch("dreamsync.dir_pipeline.analyze_song")
    def test_pipeline_mixed_success_failure(self, mock_analyze, mock_compile, tmp_path):
        _setup_track(tmp_path, name="good", songTitle="Good", artist="A", segmentIndex=0)
        _setup_track(tmp_path, name="bad", songTitle="Bad", artist="A", segmentIndex=1)

        def side_effect(path, **kwargs):
            if "bad" in str(path):
                raise RuntimeError("decode error")
            return _make_structure()

        mock_analyze.side_effect = side_effect
        mock_compile.return_value = (_make_timeline(), False)

        cache = ShowCache(tmp_path / "cache")
        pipeline = DirectoryPipeline(tmp_path, cache=cache)
        result = pipeline.prepare()

        assert result.analyzed == 1
        assert result.errors == 1
        playable = pipeline.playable_tracks()
        assert len(playable) == 1
