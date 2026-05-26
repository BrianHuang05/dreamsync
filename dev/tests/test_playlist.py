"""Tests for PlaylistManager and content_hash_track_id — D8.1."""

from pathlib import Path
from unittest.mock import patch

import pytest

from dreamsync.playlist import PlaylistManager, content_hash_track_id


# ---------------------------------------------------------------------------
# PlaylistManager Construction
# ---------------------------------------------------------------------------

class TestPlaylistConstruction:
    """Constructors: from_file, from_directory, from_m3u, from_path."""

    def test_from_file(self, tmp_path):
        """Single file creates a one-track playlist."""
        mp3 = tmp_path / "song.mp3"
        mp3.touch()

        pl = PlaylistManager.from_file(mp3)

        assert len(pl) == 1
        assert pl.current == mp3

    def test_from_directory(self, tmp_path):
        """Directory scan finds audio files, sorted alphabetically."""
        (tmp_path / "b_song.mp3").touch()
        (tmp_path / "a_song.mp3").touch()
        (tmp_path / "c_song.flac").touch()
        (tmp_path / "readme.txt").touch()  # non-audio, should be skipped

        pl = PlaylistManager.from_directory(tmp_path)

        assert len(pl) == 3
        names = [t.name for t in pl]
        assert names == ["a_song.mp3", "b_song.mp3", "c_song.flac"]

    def test_from_m3u_absolute_paths(self, tmp_path):
        """M3U with absolute paths loads correctly."""
        song1 = tmp_path / "song1.mp3"
        song2 = tmp_path / "song2.mp3"
        song1.touch()
        song2.touch()

        m3u = tmp_path / "playlist.m3u"
        m3u.write_text(f"#EXTM3U\n#EXTINF:180,Song 1\n{song1}\n{song2}\n")

        pl = PlaylistManager.from_m3u(m3u)

        assert len(pl) == 2

    def test_from_m3u_relative_paths(self, tmp_path):
        """M3U with relative paths resolves against M3U directory."""
        subdir = tmp_path / "music"
        subdir.mkdir()
        (subdir / "song.mp3").touch()

        m3u = tmp_path / "playlist.m3u"
        m3u.write_text("music/song.mp3\n")

        pl = PlaylistManager.from_m3u(m3u)

        assert len(pl) == 1
        assert pl.current.name == "song.mp3"

    def test_from_m3u_skips_missing_files(self, tmp_path):
        """M3U skips non-existent files with warning."""
        song = tmp_path / "exists.mp3"
        song.touch()

        m3u = tmp_path / "playlist.m3u"
        m3u.write_text(f"{song}\n/nonexistent/missing.mp3\n")

        pl = PlaylistManager.from_m3u(m3u)

        assert len(pl) == 1

    def test_from_path_auto_detects_directory(self, tmp_path):
        """from_path auto-detects directory source."""
        (tmp_path / "song.mp3").touch()

        pl = PlaylistManager.from_path(tmp_path)

        assert len(pl) == 1

    def test_from_path_auto_detects_m3u(self, tmp_path):
        """from_path auto-detects M3U file."""
        song = tmp_path / "song.mp3"
        song.touch()
        m3u = tmp_path / "playlist.m3u"
        m3u.write_text(f"{song}\n")

        pl = PlaylistManager.from_path(m3u)

        assert len(pl) == 1

    def test_empty_directory(self, tmp_path):
        """Empty directory creates empty playlist."""
        pl = PlaylistManager.from_directory(tmp_path)

        assert len(pl) == 0
        assert pl.current is None


# ---------------------------------------------------------------------------
# Iteration + Shuffle + Repeat
# ---------------------------------------------------------------------------

class TestIteration:
    """next, prev, peek_next, shuffle, repeat."""

    def test_next_advances_and_exhausts(self, tmp_path):
        """next() advances through tracks, returns None at end."""
        for name in ("a.mp3", "b.mp3", "c.mp3"):
            (tmp_path / name).touch()

        pl = PlaylistManager.from_directory(tmp_path)

        assert pl.current.name == "a.mp3"
        assert pl.next().name == "b.mp3"
        assert pl.next().name == "c.mp3"
        assert pl.next() is None  # exhausted

    def test_prev_goes_back(self, tmp_path):
        """prev() goes back; stays at 0 at start."""
        for name in ("a.mp3", "b.mp3"):
            (tmp_path / name).touch()

        pl = PlaylistManager.from_directory(tmp_path)
        pl.next()  # at b.mp3

        assert pl.prev().name == "a.mp3"
        assert pl.prev().name == "a.mp3"  # stays at 0

    def test_repeat_wraps(self, tmp_path):
        """repeat=True wraps to index 0 at end."""
        for name in ("a.mp3", "b.mp3"):
            (tmp_path / name).touch()

        pl = PlaylistManager.from_directory(tmp_path, repeat=True)

        pl.next()  # b.mp3
        result = pl.next()  # wraps to a.mp3

        assert result.name == "a.mp3"
        assert pl.current_index == 0

    def test_peek_next(self, tmp_path):
        """peek_next previews upcoming without advancing."""
        for name in ("a.mp3", "b.mp3", "c.mp3"):
            (tmp_path / name).touch()

        pl = PlaylistManager.from_directory(tmp_path)
        upcoming = pl.peek_next(count=2)

        assert len(upcoming) == 2
        assert upcoming[0].name == "b.mp3"
        assert upcoming[1].name == "c.mp3"
        assert pl.current.name == "a.mp3"  # not advanced


class TestMutableQueue:
    """Queue mutation helpers used by interactive playback."""

    def test_remove_future_track_keeps_current(self, tmp_path):
        for name in ("a.mp3", "b.mp3", "c.mp3"):
            (tmp_path / name).touch()

        pl = PlaylistManager.from_directory(tmp_path)
        removed = pl.remove(1)

        assert removed.name == "b.mp3"
        assert pl.current.name == "a.mp3"
        assert [t.name for t in pl.snapshot()] == ["a.mp3", "c.mp3"]

    def test_move_future_track_reorders_queue(self, tmp_path):
        for name in ("a.mp3", "b.mp3", "c.mp3", "d.mp3"):
            (tmp_path / name).touch()

        pl = PlaylistManager.from_directory(tmp_path)
        pl.move(3, 1)

        assert [t.name for t in pl.snapshot()] == ["a.mp3", "d.mp3", "b.mp3", "c.mp3"]
        assert pl.current.name == "a.mp3"

    def test_jump_to_changes_current_track(self, tmp_path):
        for name in ("a.mp3", "b.mp3", "c.mp3"):
            (tmp_path / name).touch()

        pl = PlaylistManager.from_directory(tmp_path)
        jumped = pl.jump_to(2)

        assert jumped.name == "c.mp3"
        assert pl.current.name == "c.mp3"
        assert pl.current_index == 2

    def test_shuffle_upcoming_keeps_current_fixed(self, tmp_path):
        for name in ("a.mp3", "b.mp3", "c.mp3", "d.mp3"):
            (tmp_path / name).touch()

        pl = PlaylistManager.from_directory(tmp_path)
        original = list(pl.snapshot())

        with patch("random.shuffle", side_effect=lambda seq: seq.reverse()):
            pl.shuffle_upcoming()

        shuffled = list(pl.snapshot())
        assert shuffled[0] == original[0]
        assert shuffled[1:] == list(reversed(original[1:]))

    def test_append_adds_track_to_end(self, tmp_path):
        for name in ("a.mp3", "b.mp3", "c.mp3"):
            (tmp_path / name).touch()

        pl = PlaylistManager.from_tracks([tmp_path / "a.mp3", tmp_path / "b.mp3"])
        pl.append(tmp_path / "c.mp3")

        assert [t.name for t in pl.snapshot()] == ["a.mp3", "b.mp3", "c.mp3"]

    def test_insert_adds_track_at_absolute_index(self, tmp_path):
        for name in ("a.mp3", "b.mp3", "c.mp3"):
            (tmp_path / name).touch()

        pl = PlaylistManager.from_tracks([tmp_path / "a.mp3", tmp_path / "c.mp3"])
        pl.insert(1, tmp_path / "b.mp3")

        assert [t.name for t in pl.snapshot()] == ["a.mp3", "b.mp3", "c.mp3"]


# ---------------------------------------------------------------------------
# Content Hash
# ---------------------------------------------------------------------------

class TestContentHash:
    """content_hash_track_id stability and uniqueness."""

    def test_same_content_same_id(self, tmp_path):
        """Identical content in different filenames produces same ID."""
        content = b"fake mp3 content " * 100

        file1 = tmp_path / "song_v1.mp3"
        file2 = tmp_path / "renamed_song.mp3"
        file1.write_bytes(content)
        file2.write_bytes(content)

        id1 = content_hash_track_id(file1)
        id2 = content_hash_track_id(file2)

        assert id1 == id2
        assert id1.startswith("contenthash_")

    def test_different_content_different_id(self, tmp_path):
        """Different content produces different IDs."""
        file1 = tmp_path / "song1.mp3"
        file2 = tmp_path / "song2.mp3"
        file1.write_bytes(b"content A" * 100)
        file2.write_bytes(b"content B" * 100)

        id1 = content_hash_track_id(file1)
        id2 = content_hash_track_id(file2)

        assert id1 != id2
