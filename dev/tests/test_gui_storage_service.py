"""Tests for bounded GUI storage and capture archive operations."""

from pathlib import Path
from unittest.mock import patch
import zipfile

import pytest

from dreamsync.cache import ShowCache, path_based_track_id
from dreamsync.gui.services.storage_service import StorageService
from dreamsync.show.models import ShowCue, ShowTimeline


def _timeline(track: Path) -> ShowTimeline:
    return ShowTimeline(
        song_path=str(track),
        duration=1.0,
        bpm=120.0,
        time_signature=4,
        beat_times=(0.0, 0.5),
        downbeat_times=(0.0,),
        cues=(ShowCue(0.0, "solid", ("#123456",), 1.0, 0.0, {}, "cut", 0),),
        metadata={"track_name": track.stem},
    )


def test_storage_snapshot_and_track_bounded_cache_clear(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()
    track = tmp_path / "song.mp3"
    track.write_bytes(b"audio")
    (capture_dir / "capture.mp3").write_bytes(b"12345")
    ShowCache(cache_dir).put(path_based_track_id(track), _timeline(track))
    service = StorageService(cache_dir)

    snapshot = service.snapshot(capture_dir)

    assert snapshot.cache.file_count == 1
    assert snapshot.captures.file_count == 1
    assert snapshot.captures.total_bytes == 5
    assert service.clear_track_cache(track) == 1
    assert service.snapshot(capture_dir).cache.file_count == 0


def test_clear_all_cache_preserves_unrelated_files(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    track = tmp_path / "song.mp3"
    track.write_bytes(b"audio")
    cache = ShowCache(cache_dir)
    cache.put(path_based_track_id(track), _timeline(track))
    unrelated = cache_dir / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")

    count = StorageService(cache_dir).clear_all_cache()

    assert count == 1
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_archive_captures_keeps_sidecars_and_defaults_to_keep_originals(tmp_path: Path):
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()
    first = capture_dir / "a.mp3"
    second = capture_dir / "b.mp3"
    sidecar = capture_dir / "a.show.json"
    first.write_bytes(b"aaa")
    second.write_bytes(b"bbbb")
    sidecar.write_text("{}", encoding="utf-8")
    destination = tmp_path / "archives" / "captures.zip"
    service = StorageService(tmp_path / "cache")

    result = service.archive_captures(capture_dir, destination)

    assert result == destination
    assert first.exists() and second.exists()
    assert sidecar.exists()
    with zipfile.ZipFile(destination) as archive:
        assert archive.namelist() == ["a.mp3", "b.mp3"]


def test_archive_deletes_originals_only_after_verified_zip(tmp_path: Path):
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()
    source = capture_dir / "a.mp3"
    source.write_bytes(b"aaa")
    service = StorageService(tmp_path / "cache")

    service.archive_captures(
        capture_dir,
        tmp_path / "captures.zip",
        keep_originals=False,
    )

    assert source.exists() is False


def test_archive_failure_leaves_originals_and_destination_untouched(tmp_path: Path):
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()
    source = capture_dir / "a.mp3"
    source.write_bytes(b"aaa")
    destination = tmp_path / "captures.zip"
    service = StorageService(tmp_path / "cache")

    with patch(
        "dreamsync.gui.services.storage_service.zipfile.ZipFile",
        side_effect=OSError("disk full"),
    ), pytest.raises(OSError, match="disk full"):
        service.archive_captures(
            capture_dir,
            destination,
            keep_originals=False,
        )

    assert source.exists()
    assert destination.exists() is False


def test_storage_rejects_filesystem_root_as_cache_directory(tmp_path: Path):
    with pytest.raises(ValueError, match="filesystem root"):
        StorageService(Path(tmp_path.anchor))
