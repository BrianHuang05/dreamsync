"""Tests for DreamSync ffmpeg/ffprobe binary resolution."""

from __future__ import annotations

from pathlib import Path

from dreamsync import ffmpeg as ffmpeg_module


class TestResolveBinary:
    def setup_method(self):
        ffmpeg_module.clear_binary_cache()

    def teardown_method(self):
        ffmpeg_module.clear_binary_cache()

    def test_prefers_env_override(self, monkeypatch, tmp_path):
        ffmpeg_path = tmp_path / "ffmpeg.exe"
        ffmpeg_path.write_text("")
        monkeypatch.setenv("DREAMSYNC_FFMPEG", str(ffmpeg_path))
        monkeypatch.setattr(ffmpeg_module.shutil, "which", lambda tool: None)

        assert ffmpeg_module.resolve_ffmpeg() == str(ffmpeg_path)

    def test_falls_back_to_path_lookup(self, monkeypatch):
        monkeypatch.delenv("DREAMSYNC_FFMPEG", raising=False)
        monkeypatch.setattr(ffmpeg_module.shutil, "which", lambda tool: "/usr/bin/ffmpeg")

        assert ffmpeg_module.resolve_ffmpeg() == "/usr/bin/ffmpeg"

    def test_checks_common_msys2_locations_on_windows(self, monkeypatch):
        monkeypatch.delenv("DREAMSYNC_FFMPEG", raising=False)
        monkeypatch.setattr(ffmpeg_module, "os", type("OS", (), {"name": "nt", "environ": {}}))
        monkeypatch.setattr(ffmpeg_module.shutil, "which", lambda tool: None)

        mingw_ffmpeg = Path("C:/msys64/mingw64/bin/ffmpeg.exe")
        original_is_file = Path.is_file

        def _fake_is_file(path_obj: Path) -> bool:
            if Path(path_obj) == mingw_ffmpeg:
                return True
            return original_is_file(path_obj)

        monkeypatch.setattr(Path, "is_file", _fake_is_file)

        assert ffmpeg_module.resolve_ffmpeg() == str(mingw_ffmpeg)
