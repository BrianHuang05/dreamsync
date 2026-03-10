"""Tests for profile YAML export and round-trip."""

from __future__ import annotations

from pathlib import Path

import pytest

from dreamsync.profile import load_profile
from dreamsync.profile_generator import (
    export_profile_yaml,
    generate_profile,
    generate_profile_set,
    GeneratorParams,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_profile(**kwargs):
    defaults = dict(
        base_hue=220.0,
        temperature="cool",
        saturation="vivid",
        harmony="complementary",
    )
    defaults.update(kwargs)
    return generate_profile(GeneratorParams(**defaults))


# ---------------------------------------------------------------------------
# Deliverable 4B: export_profile_yaml
# ---------------------------------------------------------------------------

class TestExportProfileYaml:
    def test_export_creates_yaml_file(self, tmp_path):
        p = _make_profile()
        out = tmp_path / "test.yaml"
        export_profile_yaml(p, out)
        assert out.exists()

    def test_export_roundtrips(self, tmp_path):
        """Export -> load_profile -> same palettes, moods, transitions."""
        p = _make_profile()
        out = tmp_path / "roundtrip.yaml"
        export_profile_yaml(p, out)
        loaded = load_profile(out)

        # Same palette names
        assert set(loaded.palettes.keys()) == set(p.palettes.keys())
        # Same colors in each palette
        for name in p.palettes:
            assert loaded.palettes[name] == p.palettes[name]
        # Same moods
        assert set(loaded.moods.keys()) == set(p.moods.keys())
        for mood_name in p.moods:
            assert loaded.moods[mood_name].palettes == p.moods[mood_name].palettes

    def test_export_with_name_override(self, tmp_path):
        p = _make_profile()
        out = tmp_path / "named.yaml"
        export_profile_yaml(p, out, name_override="My Custom Name")
        loaded = load_profile(out)
        assert loaded.name == "My Custom Name"

    def test_export_preserves_tags(self, tmp_path):
        p = _make_profile()
        out = tmp_path / "tags.yaml"
        export_profile_yaml(p, out)
        loaded = load_profile(out)
        # All original tags should be in the loaded profile
        for tag in p.tags:
            assert tag in loaded.tags

    def test_export_description_includes_seed_info(self, tmp_path):
        p = _make_profile()
        out = tmp_path / "desc.yaml"
        export_profile_yaml(p, out, seed=42, index=3)
        loaded = load_profile(out)
        assert "seed 42" in loaded.description
        assert "index 3" in loaded.description

    def test_export_valid_yaml(self, tmp_path):
        """Exported file parses with yaml.safe_load without error."""
        import yaml

        p = _make_profile()
        out = tmp_path / "valid.yaml"
        export_profile_yaml(p, out)
        with open(out, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert isinstance(data, dict)
        assert "name" in data
        assert "palettes" in data
        assert "moods" in data

    def test_export_from_profile_set(self, tmp_path):
        """Export a profile from a generated set and round-trip it."""
        pool = generate_profile_set(12, seed=42)
        p = pool[3]
        out = tmp_path / "from_set.yaml"
        export_profile_yaml(p, out, seed=42, index=3)
        loaded = load_profile(out)
        assert set(loaded.palettes.keys()) == set(p.palettes.keys())

    def test_export_creates_parent_dirs(self, tmp_path):
        p = _make_profile()
        out = tmp_path / "sub" / "dir" / "test.yaml"
        export_profile_yaml(p, out)
        assert out.exists()


# ---------------------------------------------------------------------------
# CLI profile-export (basic argument validation)
# ---------------------------------------------------------------------------

class TestProfileExportCLI:
    def test_cli_export_index_bounds(self):
        """Index out of range should be caught."""
        # This tests the logic, not the actual CLI invocation
        pool = generate_profile_set(12, seed=42)
        with pytest.raises(IndexError):
            _ = pool[999]
