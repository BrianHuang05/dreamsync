import json
from pathlib import Path

from dreamsync.storage_layout import LibraryRoots, migrate_legacy_storage


def _sidecar(track_id: str, duration: float, expected: float) -> dict:
    return {
        "spotifyTrackId": track_id,
        "spotifyUri": f"spotify:track:{track_id}",
        "songTitle": "Song",
        "artist": "Artist",
        "segmentDurationSeconds": duration,
        "expectedDurationSeconds": expected,
        "observedStartProgressSeconds": 0.0,
        "observedEndProgressSeconds": expected if duration == expected else None,
    }


def test_migration_splits_verified_audio_temp_analysis_and_shows(tmp_path: Path):
    captures = tmp_path / "captured_songs"
    cache = tmp_path / "cache"
    captures.mkdir()
    learned = cache / "spotify_track"
    learned.mkdir(parents=True)

    complete = captures / "complete.mp3"
    complete.write_bytes(b"audio" * 300)
    complete.with_suffix(".json").write_text(
        json.dumps(_sidecar("track", 60.0, 60.0)), encoding="utf-8"
    )
    partial = captures / "partial.mp3"
    partial.write_bytes(b"audio" * 300)
    partial.with_suffix(".json").write_text(
        json.dumps(_sidecar("other", 10.0, 60.0)), encoding="utf-8"
    )
    (learned / "analysis.json").write_text("{}", encoding="utf-8")
    (learned / "00000000.show.json").write_text("{}", encoding="utf-8")
    (learned / "manifest.json").write_text("{}", encoding="utf-8")

    roots = LibraryRoots.from_values(
        tmp_path / "library/audio",
        tmp_path / "library/analysis",
        tmp_path / "library/shows",
        tmp_path / "library/temp",
    )
    report = migrate_legacy_storage(
        legacy_capture_root=captures,
        legacy_cache_root=cache,
        roots=roots,
    )

    assert report.promoted_audio_files == 1
    assert report.temporary_files == 1
    assert report.analysis_files == 1
    assert report.compiled_show_files == 2
    assert (roots.audio / "complete.mp3").is_file()
    assert (roots.temp / "legacy" / "partial.mp3").is_file()
    assert (roots.analysis / "spotify_track" / "analysis.json").is_file()
    assert (roots.shows / "spotify_track" / "00000000.show.json").is_file()
    assert (roots.shows / "spotify_track" / "manifest.json").is_file()
    moved_sidecar = json.loads(
        (roots.audio / "complete.json").read_text(encoding="utf-8")
    )
    assert moved_sidecar["outputFile"] == str(
        (roots.audio / "complete.mp3").resolve()
    )
