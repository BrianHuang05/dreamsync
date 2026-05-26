"""Helpers for locating ffmpeg and ffprobe across Windows/MSYS2 setups."""

from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path

_COMMON_WINDOWS_DIRS = (
    Path("C:/msys64/mingw64/bin"),
    Path("C:/msys64/ucrt64/bin"),
    Path("C:/msys64/clang64/bin"),
    Path("C:/msys64/usr/bin"),
)

_OVERRIDE_ENV = {
    "ffmpeg": "DREAMSYNC_FFMPEG",
    "ffprobe": "DREAMSYNC_FFPROBE",
}


def _candidate_binary_names(tool: str) -> tuple[str, ...]:
    if os.name == "nt":
        return (f"{tool}.exe", tool)
    return (tool,)


@lru_cache(maxsize=None)
def resolve_binary(tool: str) -> str | None:
    """Resolve an ffmpeg-family executable.

    Resolution order:
    1. Explicit DreamSync override env vars.
    2. Normal PATH lookup.
    3. Common MSYS2/MinGW install directories on Windows.
    """
    override_var = _OVERRIDE_ENV.get(tool)
    if override_var:
        override = os.environ.get(override_var)
        if override:
            candidate = Path(override).expanduser()
            if candidate.is_file():
                return str(candidate)

    found = shutil.which(tool)
    if found:
        return found

    if os.name == "nt":
        for base_dir in _COMMON_WINDOWS_DIRS:
            for binary_name in _candidate_binary_names(tool):
                candidate = base_dir / binary_name
                if candidate.is_file():
                    return str(candidate)

    return None


def resolve_ffmpeg() -> str | None:
    return resolve_binary("ffmpeg")


def resolve_ffprobe() -> str | None:
    return resolve_binary("ffprobe")


def clear_binary_cache() -> None:
    """Reset cached ffmpeg/ffprobe resolution for tests or env changes."""
    resolve_binary.cache_clear()
