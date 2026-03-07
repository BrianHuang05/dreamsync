"""Tests for CaptureDirectoryScanner and CaptureTrack."""

import json
import logging

import pytest

from dreamsync.capture.scanner import CaptureDirectoryScanner, CaptureTrack


def _write_mp3(path):
    """Write a minimal fake MP3 file."""
    path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 100)


def _write_sidecar(path, data):
    """Write a JSON sidecar file."""
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_sidecar_data(*, segment_index=0, song_title="Song", artist="Artist",
                       album="Album", duration=180.0, **extra):
    data = {
        "segmentIndex": segment_index,
        "songTitle": song_title,
        "artist": artist,
        "album": album,
        "segmentDurationSeconds": duration,
    }
    data.update(extra)
    return data


class TestCaptureDirectoryScanner:

    def test_empty_directory(self, tmp_path):
        scanner = CaptureDirectoryScanner(tmp_path)
        assert scanner.scan() == []

    def test_single_mp3_no_sidecar(self, tmp_path):
        _write_mp3(tmp_path / "track_001.mp3")
        tracks = CaptureDirectoryScanner(tmp_path).scan()
        assert len(tracks) == 1
        t = tracks[0]
        assert t.mp3_path == tmp_path / "track_001.mp3"
        assert t.sidecar_path is None
        assert t.song_title is None
        assert t.artist is None
        assert t.album is None
        assert t.duration_seconds == 0.0
        assert t.metadata == {}

    def test_single_mp3_with_sidecar(self, tmp_path):
        _write_mp3(tmp_path / "track_001.mp3")
        sidecar = _make_sidecar_data(segment_index=0, song_title="Hello",
                                     artist="Adele", album="25", duration=295.3)
        _write_sidecar(tmp_path / "track_001.json", sidecar)

        tracks = CaptureDirectoryScanner(tmp_path).scan()
        assert len(tracks) == 1
        t = tracks[0]
        assert t.sidecar_path == tmp_path / "track_001.json"
        assert t.song_title == "Hello"
        assert t.artist == "Adele"
        assert t.album == "25"
        assert t.duration_seconds == 295.3
        assert t.metadata == sidecar

    def test_multiple_tracks_sorted_by_index(self, tmp_path):
        # Write tracks in reverse filename order but with ascending segmentIndex
        for i, name in enumerate(["c_track", "b_track", "a_track"]):
            _write_mp3(tmp_path / f"{name}.mp3")
            _write_sidecar(tmp_path / f"{name}.json",
                           _make_sidecar_data(segment_index=i, song_title=f"Song {i}"))

        tracks = CaptureDirectoryScanner(tmp_path).scan()
        assert len(tracks) == 3
        assert [t.segment_index for t in tracks] == [0, 1, 2]
        # c_track has index 0, b_track has index 1, a_track has index 2
        assert tracks[0].song_title == "Song 0"
        assert tracks[1].song_title == "Song 1"
        assert tracks[2].song_title == "Song 2"

    def test_missing_sidecar_for_some(self, tmp_path):
        # Track A has sidecar, track B does not
        _write_mp3(tmp_path / "a_track.mp3")
        _write_sidecar(tmp_path / "a_track.json",
                       _make_sidecar_data(segment_index=0, song_title="Track A"))
        _write_mp3(tmp_path / "b_track.mp3")

        tracks = CaptureDirectoryScanner(tmp_path).scan()
        assert len(tracks) == 2
        assert tracks[0].song_title == "Track A"
        assert tracks[0].sidecar_path is not None
        assert tracks[1].song_title is None
        assert tracks[1].sidecar_path is None

    def test_corrupt_sidecar_logged(self, tmp_path, caplog):
        _write_mp3(tmp_path / "track.mp3")
        (tmp_path / "track.json").write_text("not valid json {{{", encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            tracks = CaptureDirectoryScanner(tmp_path).scan()

        assert len(tracks) == 1
        assert tracks[0].sidecar_path is None
        assert tracks[0].song_title is None
        assert "Corrupt sidecar" in caplog.text

    def test_non_mp3_files_ignored(self, tmp_path):
        _write_mp3(tmp_path / "song.mp3")
        (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
        (tmp_path / "audio.wav").write_bytes(b"\x00" * 100)
        (tmp_path / "data.json").write_text("{}", encoding="utf-8")

        tracks = CaptureDirectoryScanner(tmp_path).scan()
        assert len(tracks) == 1
        assert tracks[0].mp3_path.name == "song.mp3"

    def test_directory_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Capture directory not found"):
            CaptureDirectoryScanner(tmp_path / "nonexistent")

    def test_sidecar_fields_extracted(self, tmp_path):
        _write_mp3(tmp_path / "track.mp3")
        sidecar = _make_sidecar_data(
            segment_index=5,
            song_title="Bohemian Rhapsody",
            artist="Queen",
            album="A Night at the Opera",
            duration=354.7,
            extra_field="bonus",
        )
        _write_sidecar(tmp_path / "track.json", sidecar)

        tracks = CaptureDirectoryScanner(tmp_path).scan()
        t = tracks[0]
        assert t.segment_index == 5
        assert t.song_title == "Bohemian Rhapsody"
        assert t.artist == "Queen"
        assert t.album == "A Night at the Opera"
        assert t.duration_seconds == 354.7
        assert t.metadata["extra_field"] == "bonus"

    def test_sort_falls_back_to_filename(self, tmp_path):
        # No sidecars — all get fallback index from sorted filename position
        _write_mp3(tmp_path / "z_song.mp3")
        _write_mp3(tmp_path / "a_song.mp3")
        _write_mp3(tmp_path / "m_song.mp3")

        tracks = CaptureDirectoryScanner(tmp_path).scan()
        assert len(tracks) == 3
        # Sorted by filename (fallback index from sorted order)
        assert tracks[0].mp3_path.name == "a_song.mp3"
        assert tracks[1].mp3_path.name == "m_song.mp3"
        assert tracks[2].mp3_path.name == "z_song.mp3"
