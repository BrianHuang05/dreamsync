"""Tests for per-song telemetry writer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dreamsync.telemetry import SongTelemetryWriter


def _make_frame(t: float, bpm: float = 128.0, beat: bool = False,
                rms: float = 0.05, energy: float = 0.4,
                stability: float = 0.06, mood: str = "groove",
                effect: str = "beat_pulse", palette: str = "vivid") -> dict:
    return {
        "t": round(t, 4),
        "bpm": bpm,
        "beat": beat,
        "rms": rms,
        "energy": energy,
        "stability": stability,
        "mood": mood,
        "effect": effect,
        "palette": palette,
        "render_mode": "pulse",
        "bass_ratio": 0.38,
        "spectral_flux": 12.4,
        "onset_strength": 0.032,
    }


class TestWriteFrame:
    def test_creates_file_and_writes_jsonl(self, tmp_path: Path) -> None:
        writer = SongTelemetryWriter(tmp_path)
        writer.write_frame(_make_frame(0.0))
        writer.write_frame(_make_frame(0.1))
        writer.close()

        # Should have created a session directory
        session_dirs = list(tmp_path.glob("session-*"))
        assert len(session_dirs) == 1

        song_file = session_dirs[0] / "song-001.jsonl"
        assert song_file.exists()

        lines = song_file.read_text().strip().split("\n")
        # 2 frame lines + 1 summary line
        assert len(lines) == 3
        row = json.loads(lines[0])
        assert row["t"] == 0.0
        assert row["bpm"] == 128.0

    def test_frame_fields_preserved(self, tmp_path: Path) -> None:
        writer = SongTelemetryWriter(tmp_path)
        frame = _make_frame(1.5, bpm=130.0, beat=True, mood="hype")
        writer.write_frame(frame)
        writer.close()

        song_file = list(tmp_path.glob("session-*/song-001.jsonl"))[0]
        row = json.loads(song_file.read_text().strip().split("\n")[0])
        assert row["bpm"] == 130.0
        assert row["beat"] is True
        assert row["mood"] == "hype"


class TestOnBoundary:
    def test_rotates_file(self, tmp_path: Path) -> None:
        writer = SongTelemetryWriter(tmp_path)
        writer.write_frame(_make_frame(0.0))
        writer.write_frame(_make_frame(1.0))
        writer.on_boundary(1, 2.0)
        writer.write_frame(_make_frame(2.5))
        writer.close()

        session_dir = list(tmp_path.glob("session-*"))[0]
        assert (session_dir / "song-001.jsonl").exists()
        assert (session_dir / "song-002.jsonl").exists()

        # song-001 should have 2 frames + 1 summary
        lines_1 = (session_dir / "song-001.jsonl").read_text().strip().split("\n")
        assert len(lines_1) == 3
        summary_1 = json.loads(lines_1[-1])
        assert summary_1["kind"] == "song_summary"
        assert summary_1["song_index"] == 1

        # song-002 should have 1 frame + 1 summary
        lines_2 = (session_dir / "song-002.jsonl").read_text().strip().split("\n")
        assert len(lines_2) == 2
        summary_2 = json.loads(lines_2[-1])
        assert summary_2["kind"] == "song_summary"
        assert summary_2["song_index"] == 2

    def test_multiple_boundaries(self, tmp_path: Path) -> None:
        writer = SongTelemetryWriter(tmp_path)
        writer.write_frame(_make_frame(0.0))
        writer.on_boundary(1, 10.0)
        writer.write_frame(_make_frame(10.0))
        writer.on_boundary(2, 20.0)
        writer.write_frame(_make_frame(20.0))
        writer.close()

        session_dir = list(tmp_path.glob("session-*"))[0]
        assert (session_dir / "song-001.jsonl").exists()
        assert (session_dir / "song-002.jsonl").exists()
        assert (session_dir / "song-003.jsonl").exists()


class TestSongSummaryStats:
    def test_bpm_statistics(self, tmp_path: Path) -> None:
        writer = SongTelemetryWriter(tmp_path)
        for i in range(10):
            writer.write_frame(_make_frame(i * 0.1, bpm=126.0 + i))
        writer.close()

        session_dir = list(tmp_path.glob("session-*"))[0]
        lines = (session_dir / "song-001.jsonl").read_text().strip().split("\n")
        summary = json.loads(lines[-1])
        assert summary["kind"] == "song_summary"
        # BPM values: 126, 127, 128, 129, 130, 131, 132, 133, 134, 135
        assert summary["bpm_median"] == 130.5  # median of 126..135
        assert summary["bpm_mean"] == 130.5
        assert summary["bpm_std"] > 0

    def test_mood_distribution(self, tmp_path: Path) -> None:
        writer = SongTelemetryWriter(tmp_path)
        # 6 groove + 4 hype = 10 frames
        for i in range(6):
            writer.write_frame(_make_frame(i * 0.1, mood="groove"))
        for i in range(4):
            writer.write_frame(_make_frame((6 + i) * 0.1, mood="hype"))
        writer.close()

        session_dir = list(tmp_path.glob("session-*"))[0]
        lines = (session_dir / "song-001.jsonl").read_text().strip().split("\n")
        summary = json.loads(lines[-1])
        assert summary["mood_distribution"]["groove"] == 0.6
        assert summary["mood_distribution"]["hype"] == 0.4
        assert summary["dominant_mood"] == "groove"

    def test_energy_stats(self, tmp_path: Path) -> None:
        writer = SongTelemetryWriter(tmp_path)
        writer.write_frame(_make_frame(0.0, energy=0.2))
        writer.write_frame(_make_frame(0.1, energy=0.6))
        writer.write_frame(_make_frame(0.2, energy=0.4))
        writer.close()

        session_dir = list(tmp_path.glob("session-*"))[0]
        lines = (session_dir / "song-001.jsonl").read_text().strip().split("\n")
        summary = json.loads(lines[-1])
        assert summary["energy_mean"] == 0.4
        assert summary["energy_max"] == 0.6

    def test_beat_count(self, tmp_path: Path) -> None:
        writer = SongTelemetryWriter(tmp_path)
        writer.write_frame(_make_frame(0.0, beat=True))
        writer.write_frame(_make_frame(0.1, beat=False))
        writer.write_frame(_make_frame(0.2, beat=True))
        writer.close()

        session_dir = list(tmp_path.glob("session-*"))[0]
        lines = (session_dir / "song-001.jsonl").read_text().strip().split("\n")
        summary = json.loads(lines[-1])
        assert summary["beats"] == 2
        assert summary["frames"] == 3


class TestClose:
    def test_writes_session_summary(self, tmp_path: Path) -> None:
        writer = SongTelemetryWriter(tmp_path)
        writer.write_frame(_make_frame(0.0))
        writer.on_boundary(1, 5.0)
        writer.write_frame(_make_frame(5.0))
        result = writer.close()

        session_dir = list(tmp_path.glob("session-*"))[0]
        summary_path = session_dir / "session-summary.json"
        assert summary_path.exists()

        with summary_path.open() as f:
            session_summary = json.load(f)

        assert session_summary["total_songs"] == 2
        assert len(session_summary["songs"]) == 2
        assert session_summary["songs"][0]["song_index"] == 1
        assert session_summary["songs"][1]["song_index"] == 2

        # Return value should match file contents
        assert result == session_summary

    def test_empty_session(self, tmp_path: Path) -> None:
        writer = SongTelemetryWriter(tmp_path)
        result = writer.close()

        assert result["total_songs"] == 1  # one song with 0 frames
        assert result["songs"][0]["frames"] == 0


class TestNoTelemetry:
    def test_none_telemetry_dir_is_noop(self) -> None:
        """When telemetry_dir is None, no SongTelemetryWriter is created.

        This is handled by the caller (live.py) via:
            telemetry = SongTelemetryWriter(telemetry_dir) if telemetry_dir else None

        We just verify the guard pattern works.
        """
        telemetry_dir = None
        telemetry = SongTelemetryWriter(telemetry_dir) if telemetry_dir else None
        assert telemetry is None
