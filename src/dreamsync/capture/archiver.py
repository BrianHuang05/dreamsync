"""Archive MP3 files in a directory into a zip, leaving JSON sidecars untouched."""

from __future__ import annotations

import time
import zipfile
from datetime import datetime
from pathlib import Path


def archive_mp3s(
    directory: Path,
    *,
    archive_name: str | None = None,
    delete_originals: bool = True,
    min_age_seconds: float = 0,
    dry_run: bool = False,
) -> Path | None:
    """Zip all MP3 files in *directory* into a single archive.

    Returns the archive path, or None if no MP3s found.
    Skips files newer than *min_age_seconds* (avoids archiving in-progress captures).
    JSON sidecar files (.analysis.json, .show.json, .meta.json) are left untouched.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return None

    now = time.time()
    mp3s: list[Path] = []
    for p in sorted(directory.glob("*.mp3")):
        if min_age_seconds > 0:
            age = now - p.stat().st_mtime
            if age < min_age_seconds:
                continue
        mp3s.append(p)

    if not mp3s:
        return None

    if archive_name is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"archived_{ts}"

    archive_path = directory / f"{archive_name}.zip"

    if dry_run:
        return archive_path

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for mp3 in mp3s:
            zf.write(mp3, mp3.name)

    if delete_originals:
        for mp3 in mp3s:
            mp3.unlink()

    return archive_path
