"""Tests for MP3 archiving utility."""

import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from dreamsync.capture.archiver import archive_mp3s


class ArchiveTests(unittest.TestCase):
    def _make_dir(self, mp3_names: list[str], json_names: list[str] | None = None) -> Path:
        """Create a temp directory with fake MP3 and optional JSON files."""
        d = Path(tempfile.mkdtemp())
        for name in mp3_names:
            (d / name).write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 100)
        for name in (json_names or []):
            (d / name).write_text("{}", encoding="utf-8")
        return d

    def test_archive_creates_zip(self) -> None:
        d = self._make_dir(["a.mp3", "b.mp3", "c.mp3"])
        result = archive_mp3s(d, delete_originals=False)
        self.assertIsNotNone(result)
        self.assertTrue(result.exists())
        with zipfile.ZipFile(result) as zf:
            self.assertEqual(len(zf.namelist()), 3)
            self.assertIn("a.mp3", zf.namelist())

    def test_archive_deletes_originals(self) -> None:
        d = self._make_dir(["a.mp3", "b.mp3"])
        result = archive_mp3s(d, delete_originals=True)
        self.assertIsNotNone(result)
        self.assertEqual(list(d.glob("*.mp3")), [])
        self.assertTrue(result.exists())

    def test_archive_keeps_originals(self) -> None:
        d = self._make_dir(["a.mp3", "b.mp3"])
        archive_mp3s(d, delete_originals=False)
        self.assertEqual(len(list(d.glob("*.mp3"))), 2)

    def test_archive_leaves_json_untouched(self) -> None:
        d = self._make_dir(
            ["song.mp3"],
            ["song.analysis.json", "song.show.json", "song.meta.json"],
        )
        archive_mp3s(d, delete_originals=True)
        # JSON files should still exist
        self.assertTrue((d / "song.analysis.json").exists())
        self.assertTrue((d / "song.show.json").exists())
        self.assertTrue((d / "song.meta.json").exists())
        # MP3 should be gone
        self.assertFalse((d / "song.mp3").exists())

    def test_archive_empty_directory(self) -> None:
        d = Path(tempfile.mkdtemp())
        result = archive_mp3s(d)
        self.assertIsNone(result)
        self.assertEqual(list(d.glob("*.zip")), [])

    def test_archive_min_age_skips_recent(self) -> None:
        d = self._make_dir(["recent.mp3"])
        # File was just created — min_age_seconds=9999 should skip it
        result = archive_mp3s(d, min_age_seconds=9999)
        self.assertIsNone(result)
        self.assertTrue((d / "recent.mp3").exists())

    def test_archive_custom_name(self) -> None:
        d = self._make_dir(["a.mp3"])
        result = archive_mp3s(d, archive_name="my-session", delete_originals=False)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "my-session.zip")

    def test_archive_default_name(self) -> None:
        d = self._make_dir(["a.mp3"])
        result = archive_mp3s(d, delete_originals=False)
        self.assertIsNotNone(result)
        self.assertTrue(result.name.startswith("archived_"))
        self.assertTrue(result.name.endswith(".zip"))


if __name__ == "__main__":
    unittest.main()
