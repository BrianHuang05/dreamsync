import json

from dreamsync.analyzer.bpm import BeatGrid, TempoRegion
from dreamsync.analyzer.models import SongStructure
from dreamsync.analyzer.sections import Section
from dreamsync.cache import ShowCache, spotify_track_cache_id
from dreamsync.capture.eligibility import SpotifyCaptureCandidate
from dreamsync.show.models import ShowCue, ShowTimeline
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
        song_path="song.mp3", duration=10.0, bpm=120.0, time_signature=4,
        beat_times=(0.0, 0.5), downbeat_times=(0.0,),
        cues=(ShowCue(0.0, "solid", ("#ffffff",), 1.0, 1.0, {}, "cut", 0),),
        metadata={"track_name": "Song", "artist": "Artist"},
    )


def candidate() -> SpotifyCaptureCandidate:
    return SpotifyCaptureCandidate(
        track_id="abc", uri="spotify:track:abc", title="Song", artist="Artist",
        expected_duration_seconds=10.0, observed_end_progress_seconds=10.0,
        captured_duration_seconds=10.0,
    )


def test_publish_requires_analysis_and_reloadable_show(tmp_path):
    cache = ShowCache(tmp_path)
    store = LearnedTrackStore(cache)
    store.write_analysis("abc", structure())
    cache.put(spotify_track_cache_id("abc"), timeline())
    store.publish_manifest(candidate())
    assert store.is_complete("abc")
    assert store.lookup("abc").metadata["track_name"] == "Song"


def test_corrupt_analysis_fails_closed(tmp_path):
    cache = ShowCache(tmp_path)
    store = LearnedTrackStore(cache)
    store.analysis_path("abc").parent.mkdir(parents=True)
    store.analysis_path("abc").write_text("{bad", encoding="utf-8")
    cache.put(spotify_track_cache_id("abc"), timeline())
    store.manifest_path("abc").write_text(json.dumps({
        "schemaVersion": 1, "provider": "spotify", "providerTrackId": "abc",
        "captureComplete": True,
    }), encoding="utf-8")
    assert not store.is_complete("abc")


def test_no_manifest_is_published_when_show_is_missing(tmp_path):
    store = LearnedTrackStore(tmp_path)
    store.write_analysis("abc", structure())
    try:
        store.publish_manifest(candidate())
    except RuntimeError:
        pass
    assert not store.manifest_path("abc").exists()

