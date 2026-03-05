"""Tests for dreamsync.capture.file_namer — deterministic file naming."""

import os
import tempfile

import pytest

from dreamsync.capture.file_namer import FileNamer, sanitize


class TestSanitize:
    def test_removes_illegal_chars(self):
        assert sanitize('foo:bar/baz\\qux') == "foo_bar_baz_qux"

    def test_strips_dots_and_spaces(self):
        assert sanitize("  ..name.. ") == "name"

    def test_empty_string(self):
        assert sanitize("") == "untitled"

    def test_only_illegal_chars(self):
        # All illegal chars become underscores, which survive strip
        assert sanitize(':<>"|?*') == "_______"

    def test_normal_string(self):
        assert sanitize("Pink Floyd") == "Pink Floyd"


class TestFileNamer:
    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return str(tmp_path)

    def test_creates_output_dir(self, tmp_path):
        out = str(tmp_path / "new_subdir")
        namer = FileNamer(output_dir=out)
        assert os.path.isdir(out)

    def test_timestamp_pattern(self, tmp_dir):
        namer = FileNamer(output_dir=tmp_dir, pattern="timestamp")
        path = namer.next_filename()
        assert path.endswith(".mp3")
        assert "segment_000001" in path

    def test_counter_increments(self, tmp_dir):
        namer = FileNamer(output_dir=tmp_dir, pattern="timestamp")
        p1 = namer.next_filename()
        p2 = namer.next_filename()
        assert "000001" in p1
        assert "000002" in p2

    def test_metadata_pattern(self, tmp_dir):
        namer = FileNamer(output_dir=tmp_dir, pattern="metadata")
        path = namer.next_filename({"artist": "Pink Floyd", "song_title": "Time"})
        assert "Pink Floyd" in path
        assert "Time" in path
        assert path.endswith(".mp3")

    def test_metadata_fallback_to_timestamp(self, tmp_dir):
        namer = FileNamer(output_dir=tmp_dir, pattern="metadata")
        path = namer.next_filename(None)
        assert "segment_" in path

    def test_collision_avoidance(self, tmp_dir):
        namer = FileNamer(output_dir=tmp_dir, pattern="timestamp")
        p1 = namer.next_filename()
        # Create the file so the next call detects collision
        open(p1, "w").close()
        p2 = namer.next_filename()
        # p2 should still be valid and different
        assert p1 != p2
        assert p2.endswith(".mp3")

    def test_sanitizes_metadata(self, tmp_dir):
        namer = FileNamer(output_dir=tmp_dir, pattern="metadata")
        path = namer.next_filename({
            "artist": "AC/DC",
            "song_title": 'Back In "Black"',
        })
        assert "/" not in os.path.basename(path)
        assert '"' not in os.path.basename(path)
