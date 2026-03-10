"""Tests for CLI auto-palette and smart-rotation flags."""

from __future__ import annotations

import argparse

import pytest

from dreamsync.cli import _parse_chain_interval, _resolve_profile_chain_from_args


# ---------------------------------------------------------------------------
# Chain interval parsing
# ---------------------------------------------------------------------------


def test_chain_interval_default() -> None:
    mn, mx = _parse_chain_interval(None)
    assert mn == 60.0
    assert mx == 180.0


def test_chain_interval_parsing() -> None:
    mn, mx = _parse_chain_interval("60-180")
    assert mn == 60.0
    assert mx == 180.0


def test_chain_interval_single_value() -> None:
    mn, mx = _parse_chain_interval("90")
    assert mn == 90.0
    assert mx == 270.0  # 90 * 3


# ---------------------------------------------------------------------------
# Profile chain resolution
# ---------------------------------------------------------------------------


def _make_args(**kwargs) -> argparse.Namespace:
    defaults = dict(
        auto_palette=False,
        auto_palette_seed=None,
        auto_palette_count=12,
        smart_rotation=False,
        profile_rotation=None,
        profile=None,
        chain_blend=8.0,
        chain_interval=None,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_resolve_chain_none_when_no_flags() -> None:
    profile, chain = _resolve_profile_chain_from_args(_make_args())
    assert profile is None
    assert chain is None


def test_resolve_chain_auto_palette() -> None:
    profile, chain = _resolve_profile_chain_from_args(
        _make_args(auto_palette=True, auto_palette_count=5, auto_palette_seed=42)
    )
    assert profile is not None
    assert chain is not None
    assert profile.name  # non-empty


def test_auto_palette_mutually_exclusive_with_profile(capsys) -> None:
    profile, chain = _resolve_profile_chain_from_args(
        _make_args(auto_palette=True, profile="aurora")
    )
    assert profile == "error"
    captured = capsys.readouterr()
    assert "mutually exclusive" in captured.out


def test_smart_rotation_with_profiles() -> None:
    profile, chain = _resolve_profile_chain_from_args(
        _make_args(smart_rotation=True, profile_rotation="aurora,neon_city")
    )
    assert profile is not None
    assert chain is not None


def test_smart_rotation_requires_profile_rotation(capsys) -> None:
    profile, chain = _resolve_profile_chain_from_args(
        _make_args(smart_rotation=True)
    )
    assert profile == "error"
    captured = capsys.readouterr()
    assert "--profile-rotation" in captured.out


def test_auto_palette_count() -> None:
    profile, chain = _resolve_profile_chain_from_args(
        _make_args(auto_palette=True, auto_palette_count=20, auto_palette_seed=1)
    )
    assert chain is not None
    assert len(chain._pool) == 20


def test_chain_blend_duration() -> None:
    profile, chain = _resolve_profile_chain_from_args(
        _make_args(auto_palette=True, chain_blend=12.0, auto_palette_seed=1)
    )
    assert chain is not None
    assert chain._config.blend_duration == 12.0


def test_chain_interval_wired() -> None:
    profile, chain = _resolve_profile_chain_from_args(
        _make_args(auto_palette=True, chain_interval="30-90", auto_palette_seed=1)
    )
    assert chain is not None
    assert chain._config.min_profile_duration == 30.0
    assert chain._config.max_profile_duration == 90.0


def test_auto_palette_seed_deterministic() -> None:
    _, chain1 = _resolve_profile_chain_from_args(
        _make_args(auto_palette=True, auto_palette_count=5, auto_palette_seed=42)
    )
    _, chain2 = _resolve_profile_chain_from_args(
        _make_args(auto_palette=True, auto_palette_count=5, auto_palette_seed=42)
    )
    names1 = [p.name for p in chain1._pool]
    names2 = [p.name for p in chain2._pool]
    assert names1 == names2
