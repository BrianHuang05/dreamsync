"""Tests for cached_compile_show() and path_based_track_id() — D4.3,
plus CLI cache subcommands — D4.4."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from dreamsync.cache import (
    ShowCache,
    cached_compile_show,
    path_based_track_id,
    _sanitize_track_id,
)
from dreamsync.show.models import ShowCue, ShowTimeline


def _make_timeline(**kwargs) -> ShowTimeline:
    """Minimal valid ShowTimeline."""
    defaults = dict(
        song_path="test.mp3",
        duration=180.0,
        bpm=128.0,
        time_signature=4,
        beat_times=tuple(i * 0.46875 for i in range(384)),
        downbeat_times=tuple(i * 1.875 for i in range(96)),
        cues=(
            ShowCue(t=0.0, render_mode="scroll", color_palette=("#FF0000", "#00FF00", "#0000FF"),
                    intensity=0.5, speed=1.0, params={}, transition="cut", transition_beats=0),
        ),
        metadata={"track_name": "Test Song", "artist": "Test Artist"},
    )
    defaults.update(kwargs)
    return ShowTimeline(**defaults)


class TestCachedCompileShow:
    def test_cache_hit(self, tmp_path):
        cache = ShowCache(tmp_path / "cache")
        tl = _make_timeline()
        cache.put("track123", tl)

        mock_structure = MagicMock()
        result, from_cache = cached_compile_show(
            mock_structure, cache=cache, track_id="track123"
        )

        assert from_cache is True
        assert result.duration == 180.0

    @patch("dreamsync.compiler.compile.compile_show")
    def test_cache_miss(self, mock_compile, tmp_path):
        cache = ShowCache(tmp_path / "cache")
        tl = _make_timeline()
        mock_compile.return_value = tl
        mock_structure = MagicMock()

        result, from_cache = cached_compile_show(
            mock_structure, cache=cache, track_id="track123"
        )

        assert from_cache is False
        mock_compile.assert_called_once()
        assert cache.has("track123")

    @patch("dreamsync.compiler.compile.compile_show")
    def test_compile_kwargs_passthrough(self, mock_compile, tmp_path):
        cache = ShowCache(tmp_path / "cache")
        tl = _make_timeline()
        mock_compile.return_value = tl
        mock_structure = MagicMock()

        cached_compile_show(
            mock_structure, cache=cache, track_id="track123",
            seed=42, intro_intensity=0.3,
        )

        _, call_kwargs = mock_compile.call_args
        assert call_kwargs["seed"] == 42
        assert call_kwargs["intro_intensity"] == 0.3


class TestPathBasedTrackId:
    def test_determinism_and_different_paths(self):
        id1 = path_based_track_id("/music/song.mp3")
        id2 = path_based_track_id("/music/song.mp3")
        id3 = path_based_track_id("/other/song.mp3")

        assert id1 == id2                        # deterministic
        assert id1 != id3                        # different paths -> different IDs
        assert id1.replace("_", "").replace("-", "").isalnum()  # filesystem-safe


class TestSanitizeTrackId:
    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            _sanitize_track_id("")

    def test_sanitizes_special_chars(self):
        result = _sanitize_track_id("track/with:special chars!")
        assert "/" not in result
        assert ":" not in result
        assert " " not in result

    def test_spotify_id_unchanged(self):
        assert _sanitize_track_id("4iV5W6fMJg0KuzBpVbG3E0") == "4iV5W6fMJg0KuzBpVbG3E0"


# ---------------------------------------------------------------------------
# D4.4 — CLI Cache Subcommand Arg Parsing
# ---------------------------------------------------------------------------

class TestCacheCliArgs:
    def test_cache_list_args(self):
        from dreamsync.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["cache-list"])
        assert args.command == "cache-list"
        assert args.cache_dir == "~/.dreamsync/cache"

    def test_cache_list_custom_dir(self):
        from dreamsync.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["cache-list", "--cache-dir", "/tmp/my-cache"])
        assert args.cache_dir == "/tmp/my-cache"

    def test_cache_clear_args(self):
        from dreamsync.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["cache-clear", "--track-id", "abc123", "--yes"])
        assert args.command == "cache-clear"
        assert args.track_id == "abc123"
        assert args.yes is True

    def test_cache_info_args(self):
        from dreamsync.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["cache-info"])
        assert args.command == "cache-info"
        assert args.cache_dir == "~/.dreamsync/cache"

    def test_compile_cache_dir_arg(self):
        from dreamsync.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["compile", "structure.json", "--cache-dir", "/tmp/cache"])
        assert args.command == "compile"
        assert args.cache_dir == "/tmp/cache"

    def test_compile_cache_dir_default_none(self):
        from dreamsync.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["compile", "structure.json"])
        assert args.cache_dir is None

    def test_compile_and_play_cache_dir_arg(self):
        from dreamsync.cli import build_parser
        parser = build_parser()
        args = parser.parse_args([
            "compile-and-play", "song.mp3", "--config", "config.yaml",
            "--cache-dir", "/tmp/cache",
        ])
        assert args.command == "compile-and-play"
        assert args.cache_dir == "/tmp/cache"
