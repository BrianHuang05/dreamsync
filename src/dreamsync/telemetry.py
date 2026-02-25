"""Per-song telemetry writer — splits frame-level JSONL by song boundary."""

from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO


class SongTelemetryWriter:
    """Writes per-frame JSONL data to one file per song, rotating on boundaries."""

    def __init__(self, output_dir: Path) -> None:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._session_dir = output_dir / f"session-{ts}"
        self._session_dir.mkdir(parents=True, exist_ok=True)

        self._song_index = 1
        self._file: TextIO | None = None
        self._summaries: list[dict[str, Any]] = []
        self._open_song_file()
        self._reset_accumulators(0.0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write_frame(self, row: dict[str, Any]) -> None:
        """Append a single frame row as JSON to the current song file."""
        if self._file is not None:
            self._file.write(json.dumps(row, separators=(",", ":")) + "\n")
        self._accumulate(row)

    def on_boundary(self, boundary_index: int, t: float) -> None:
        """Flush the current song and open the next song file."""
        self._flush_song_summary(t)
        self._song_index += 1
        self._open_song_file()
        self._reset_accumulators(t)

    def close(self) -> dict[str, Any]:
        """Flush the final song, write session-summary.json, and return it."""
        # Use the last frame's timestamp as the end time for the final song
        last_t = self._last_t if self._frame_count > 0 else self._start_t
        self._flush_song_summary(last_t)

        session_summary = {
            "songs": self._summaries,
            "total_songs": len(self._summaries),
        }
        summary_path = self._session_dir / "session-summary.json"
        with summary_path.open("w", encoding="utf-8") as f:
            json.dump(session_summary, f, indent=2)

        return session_summary

    @property
    def session_dir(self) -> Path:
        return self._session_dir

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open_song_file(self) -> None:
        if self._file is not None:
            self._file.close()
        path = self._session_dir / f"song-{self._song_index:03d}.jsonl"
        self._file = path.open("w", encoding="utf-8")

    def _reset_accumulators(self, t: float) -> None:
        self._start_t = t
        self._last_t = t
        self._frame_count = 0
        self._beat_count = 0
        self._bpm_values: list[float] = []
        self._energy_sum = 0.0
        self._energy_max = 0.0
        self._stability_sum = 0.0
        self._mood_counts: dict[str, int] = {}
        self._effect_counts: dict[str, int] = {}
        self._palette_counts: dict[str, int] = {}

    def _accumulate(self, row: dict[str, Any]) -> None:
        self._frame_count += 1
        self._last_t = row.get("t", self._last_t)

        if row.get("beat"):
            self._beat_count += 1

        bpm = row.get("bpm", 0)
        if bpm and bpm > 0:
            self._bpm_values.append(float(bpm))

        energy = row.get("energy", 0.0)
        self._energy_sum += energy
        if energy > self._energy_max:
            self._energy_max = energy

        self._stability_sum += row.get("stability", 0.0)

        mood = row.get("mood")
        if mood:
            self._mood_counts[mood] = self._mood_counts.get(mood, 0) + 1

        effect = row.get("effect")
        if effect:
            self._effect_counts[effect] = self._effect_counts.get(effect, 0) + 1

        palette = row.get("palette")
        if palette:
            self._palette_counts[palette] = self._palette_counts.get(palette, 0) + 1

    def _flush_song_summary(self, end_t: float) -> None:
        n = self._frame_count
        summary: dict[str, Any] = {
            "kind": "song_summary",
            "song_index": self._song_index,
            "start_t": round(self._start_t, 4),
            "end_t": round(end_t, 4),
            "duration_seconds": round(end_t - self._start_t, 4),
            "frames": n,
            "beats": self._beat_count,
        }

        if self._bpm_values:
            summary["bpm_median"] = round(statistics.median(self._bpm_values), 2)
            summary["bpm_mean"] = round(statistics.mean(self._bpm_values), 2)
            summary["bpm_std"] = round(statistics.pstdev(self._bpm_values), 2) if len(self._bpm_values) > 1 else 0.0
        else:
            summary["bpm_median"] = 0.0
            summary["bpm_mean"] = 0.0
            summary["bpm_std"] = 0.0

        summary["energy_mean"] = round(self._energy_sum / n, 4) if n > 0 else 0.0
        summary["energy_max"] = round(self._energy_max, 4)
        summary["stability_mean"] = round(self._stability_sum / n, 4) if n > 0 else 0.0

        summary["mood_distribution"] = _counts_to_distribution(self._mood_counts, n)
        summary["effect_distribution"] = _counts_to_distribution(self._effect_counts, n)
        summary["palette_distribution"] = _counts_to_distribution(self._palette_counts, n)

        summary["dominant_mood"] = max(self._mood_counts, key=self._mood_counts.get) if self._mood_counts else None
        summary["dominant_effect"] = max(self._effect_counts, key=self._effect_counts.get) if self._effect_counts else None

        # Write as last line of current song file
        if self._file is not None:
            self._file.write(json.dumps(summary, separators=(",", ":")) + "\n")
            self._file.close()
            self._file = None

        self._summaries.append(summary)


def _counts_to_distribution(counts: dict[str, int], total: int) -> dict[str, float]:
    if total == 0:
        return {}
    return {k: round(v / total, 4) for k, v in sorted(counts.items())}
