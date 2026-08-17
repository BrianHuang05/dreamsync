from pathlib import Path
import json
from unittest.mock import patch

from dreamsync.analyzer.bpm import BeatGrid, TempoRegion
from dreamsync.analyzer.models import SongStructure
from dreamsync.analyzer.sections import Section
from dreamsync.cache import ShowCache
from dreamsync.show.models import ShowCue, ShowTimeline
from dreamsync.show_pipeline_worker import ShowPipelineWorker
from dreamsync.spotify.learned_track import LearnedTrackStore


def structure() -> SongStructure:
    return SongStructure(
        path="song.mp3", duration=10.0, bpm=120.0, time_signature=4,
        beat_grid=BeatGrid(120.0, (0.0, 0.5), (0.0,), 4),
        tempo_regions=(TempoRegion(0.0, 10.0, 120.0, 1.0),),
        sections=(Section(0.0, 10.0, "verse", 0.5, "groove", 120.0, "A"),),
        metadata={"track_name": "Song", "artist": "Artist"},
    )


def timeline() -> ShowTimeline:
    return ShowTimeline(
        "song.mp3", 10.0, 120.0, 4, (0.0,), (0.0,),
        (ShowCue(0.0, "solid", ("#fff",), 1.0, 1.0, {}, "cut", 0),),
        {"track_name": "Song"},
    )


def metadata() -> dict:
    return {
        "spotify_track_id": "abc", "spotify_uri": "spotify:track:abc",
        "song_title": "Song", "artist": "Artist",
        "expected_duration_seconds": 10.0,
        "segment_duration_seconds": 10.0,
        "observed_end_progress_seconds": 10.0,
    }


def source(tmp_path: Path) -> Path:
    path = tmp_path / "song.mp3"
    path.write_bytes(b"audio" * 1024)
    path.with_suffix(".json").write_text(
        json.dumps({"spotifyTrackId": "abc"}), encoding="utf-8"
    )
    return path


def test_verified_learned_compile_deletes_source_only_after_publish(tmp_path):
    cache = ShowCache(tmp_path / "cache")
    mp3 = source(tmp_path)
    worker = ShowPipelineWorker(
        cache, learned_live=True,
        retention_policy="delete_after_verified_compile",
    )

    def compile_and_store(_structure, profile, *, cache, track_id):
        result = timeline()
        cache.put(track_id, result, profile)
        return result, False

    with patch("dreamsync.analyzer.analyze.analyze_song", return_value=structure()), patch(
        "dreamsync.cache.cached_compile_show", side_effect=compile_and_store
    ):
        worker._process(mp3, metadata())
    worker.shutdown()
    assert LearnedTrackStore(cache).is_complete("abc")
    assert not mp3.exists()
    assert not mp3.with_suffix(".json").exists()


def test_compile_failure_retains_recoverable_source_and_analysis(tmp_path):
    cache = ShowCache(tmp_path / "cache")
    mp3 = source(tmp_path)
    worker = ShowPipelineWorker(
        cache, learned_live=True,
        retention_policy="delete_after_verified_compile",
    )
    with patch("dreamsync.analyzer.analyze.analyze_song", return_value=structure()), patch(
        "dreamsync.cache.cached_compile_show", side_effect=RuntimeError("compile failed")
    ):
        worker._process(mp3, metadata())
    worker.shutdown()
    assert mp3.exists()
    assert LearnedTrackStore(cache).read_analysis("abc") is not None
    assert not LearnedTrackStore(cache).manifest_path("abc").exists()


def test_existing_analysis_is_reused_when_show_is_not_compiled(tmp_path):
    cache = ShowCache(tmp_path / "cache")
    mp3 = source(tmp_path)
    LearnedTrackStore(cache).write_analysis("abc", structure())
    worker = ShowPipelineWorker(cache, learned_live=True)

    def compile_and_store(saved_structure, profile, *, cache, track_id):
        assert saved_structure.duration == 10.0
        result = timeline()
        cache.put(track_id, result, profile)
        return result, False

    with patch(
        "dreamsync.analyzer.analyze.analyze_song",
        side_effect=AssertionError("saved analysis should be reused"),
    ), patch(
        "dreamsync.cache.cached_compile_show", side_effect=compile_and_store
    ):
        worker._process(mp3, metadata())
    worker.shutdown()

    assert LearnedTrackStore(cache).is_complete("abc")


def test_verified_temp_capture_is_promoted_to_audio_library(tmp_path):
    cache = ShowCache(tmp_path / "shows")
    (tmp_path / "temp").mkdir()
    temp_mp3 = source(tmp_path / "temp")
    audio_root = tmp_path / "audio"
    analysis_root = tmp_path / "analysis"
    worker = ShowPipelineWorker(
        cache,
        learned_live=True,
        retention_policy="keep_all",
        audio_root=audio_root,
        analysis_root=analysis_root,
    )

    def compile_and_store(_structure, profile, *, cache, track_id):
        result = timeline()
        cache.put(track_id, result, profile)
        return result, False

    with patch("dreamsync.analyzer.analyze.analyze_song", return_value=structure()), patch(
        "dreamsync.cache.cached_compile_show", side_effect=compile_and_store
    ):
        worker._process(temp_mp3, metadata())
    worker.shutdown()

    promoted = audio_root / temp_mp3.name
    assert not temp_mp3.exists()
    assert promoted.is_file()
    assert promoted.with_suffix(".json").is_file()
    store = LearnedTrackStore(cache, analysis_root=analysis_root)
    assert store.analysis_path("abc").is_file()
    assert store.is_complete("abc")


def test_keep_recent_never_deletes_unrelated_replay_audio(tmp_path):
    cache = ShowCache(tmp_path / "cache")
    mp3 = source(tmp_path)
    unrelated = tmp_path / "compiled-replay.mp3"
    unrelated.write_bytes(b"audio" * 1024)
    worker = ShowPipelineWorker(
        cache, learned_live=True, retention_policy="keep_recent",
        retained_mp3_limit=0,
    )

    def compile_and_store(_structure, profile, *, cache, track_id):
        result = timeline()
        cache.put(track_id, result, profile)
        return result, False

    with patch("dreamsync.analyzer.analyze.analyze_song", return_value=structure()), patch(
        "dreamsync.cache.cached_compile_show", side_effect=compile_and_store
    ):
        worker._process(mp3, metadata())
    worker.shutdown()
    assert not mp3.exists()
    assert unrelated.exists()
