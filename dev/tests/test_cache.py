"""Tests for ShowCache — D4.2."""

import json
from pathlib import Path

import pytest

from dreamsync.cache import ShowCache, CacheEntry, CacheStats, profile_fingerprint
from dreamsync.profile import (
    MoodEffectEntry, MoodProfileConfig, ProfileConfig, TransitionRule,
)
from dreamsync.show.models import ShowCue, ShowTimeline


def _make_profile(**kwargs) -> ProfileConfig:
    """Minimal valid profile."""
    defaults = dict(
        name="test_profile",
        palettes={
            "warm": ("#FF0000", "#FF8800", "#FFFF00"),
            "cool": ("#0000FF", "#0088FF", "#00FFFF"),
        },
        moods={
            "chill": MoodProfileConfig(palettes=("cool",), effects=(MoodEffectEntry("breathe", 1.0),), params={"breathe_rate_mult": 0.6}),
            "groove": MoodProfileConfig(palettes=("warm",), effects=(MoodEffectEntry("scroll", 1.0),), params={}),
            "hype": MoodProfileConfig(palettes=("warm",), effects=(MoodEffectEntry("pulse", 1.0),), params={}),
            "drop": MoodProfileConfig(palettes=("warm", "cool"), effects=(MoodEffectEntry("pulse", 1.0),), params={}),
        },
    )
    defaults.update(kwargs)
    return ProfileConfig(**defaults)


def _make_timeline(**kwargs) -> ShowTimeline:
    """Minimal valid ShowTimeline for cache testing."""
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


class TestShowCache:
    def test_put_get_round_trip(self, tmp_path):
        cache = ShowCache(tmp_path / "cache")
        tl = _make_timeline()
        profile = _make_profile()

        cache.put("track123", tl, profile)
        result = cache.get("track123", profile)

        assert result is not None
        assert result.duration == tl.duration
        assert result.bpm == tl.bpm
        assert result.cues[0].render_mode == "scroll"
        assert result.metadata["track_name"] == "Test Song"

    def test_get_miss_unknown_track(self, tmp_path):
        cache = ShowCache(tmp_path / "cache")
        assert cache.get("unknown_track") is None

    def test_get_miss_different_profile(self, tmp_path):
        cache = ShowCache(tmp_path / "cache")
        tl = _make_timeline()
        profile_a = _make_profile(name="profile_a")
        profile_b = _make_profile(name="profile_b")

        cache.put("track123", tl, profile_a)
        assert cache.get("track123", profile_b) is None

    def test_get_corrupt_file(self, tmp_path):
        cache = ShowCache(tmp_path / "cache")
        fp = profile_fingerprint(None)
        corrupt_path = cache._entry_path("track123", fp)
        corrupt_path.parent.mkdir(parents=True)
        corrupt_path.write_text("{invalid json!!", encoding="utf-8")

        result = cache.get("track123")
        assert result is None
        assert not corrupt_path.exists()

    def test_has_true_after_put(self, tmp_path):
        cache = ShowCache(tmp_path / "cache")
        tl = _make_timeline()
        cache.put("track123", tl)
        assert cache.has("track123") is True

    def test_has_false_before_put(self, tmp_path):
        cache = ShowCache(tmp_path / "cache")
        assert cache.has("track123") is False

    def test_atomic_write(self, tmp_path):
        cache = ShowCache(tmp_path / "cache")
        tl = _make_timeline()
        path = cache.put("track123", tl)

        assert path.exists()
        # No .tmp files should remain
        tmp_files = list(path.parent.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_metadata_augmentation(self, tmp_path):
        cache = ShowCache(tmp_path / "cache")
        tl = _make_timeline()
        profile = _make_profile()

        path = cache.put("track123", tl, profile)
        data = json.loads(path.read_text())

        assert data["metadata"]["_cache_profile_name"] == "test_profile"
        assert data["metadata"]["_cache_profile_fp"] == profile_fingerprint(profile)
        assert "_cache_compiled_at" in data["metadata"]

    def test_invalidate_known_track(self, tmp_path):
        cache = ShowCache(tmp_path / "cache")
        tl = _make_timeline()
        profile_a = _make_profile(name="profile_a")
        profile_b = _make_profile(name="profile_b")

        cache.put("track123", tl, profile_a)
        cache.put("track123", tl, profile_b)
        count = cache.invalidate("track123")

        assert count == 2
        assert not cache.has("track123", profile_a)
        assert not cache.has("track123", profile_b)

    def test_invalidate_unknown_track(self, tmp_path):
        cache = ShowCache(tmp_path / "cache")
        count = cache.invalidate("nonexistent")
        assert count == 0

    def test_clear(self, tmp_path):
        cache = ShowCache(tmp_path / "cache")
        tl = _make_timeline()

        cache.put("track1", tl)
        cache.put("track2", tl)
        cache.put("track3", tl)
        count = cache.clear()

        assert count == 3
        assert cache.stats().entry_count == 0

    def test_list_entries(self, tmp_path):
        cache = ShowCache(tmp_path / "cache")
        tl_a = _make_timeline(metadata={"track_name": "Alpha Song", "artist": "Artist A"})
        tl_b = _make_timeline(metadata={"track_name": "Beta Song", "artist": "Artist B"})

        cache.put("track_a", tl_a)
        cache.put("track_b", tl_b)

        entries = cache.list_entries()
        assert len(entries) == 2
        # Sorted by track_name
        assert entries[0].track_name == "Alpha Song"
        assert entries[1].track_name == "Beta Song"
        assert entries[0].artist == "Artist A"
        assert isinstance(entries[0].file_size, int)
        assert entries[0].file_size > 0

    def test_stats(self, tmp_path):
        cache = ShowCache(tmp_path / "cache")
        tl = _make_timeline()
        profile_a = _make_profile(name="profile_a")

        cache.put("track1", tl)
        cache.put("track1", tl, profile_a)
        cache.put("track2", tl)

        s = cache.stats()
        assert s.entry_count == 3
        assert s.track_count == 2
        assert s.total_bytes > 0
        assert s.cache_dir == cache.cache_dir

    def test_auto_create_dir(self, tmp_path):
        nested = tmp_path / "new" / "nested"
        cache = ShowCache(nested)
        assert nested.exists()
        assert nested.is_dir()
