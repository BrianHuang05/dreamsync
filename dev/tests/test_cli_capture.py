"""Tests for CLI capture commands — existing + D5 orchestrator integration."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dreamsync.cli import build_parser


class CaptureCommandTests(unittest.TestCase):
    def test_devices_command_parses(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["devices"])
        self.assertEqual(args.command, "devices")

    def test_capture_command_parses(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "capture",
                "--duration",
                "10",
                "--sample-rate",
                "48000",
                "--channels",
                "2",
                "--device",
                "1",
                "--heartbeat-seconds",
                "2.5",
                "--jsonl",
                "out/capture.jsonl",
            ]
        )
        self.assertEqual(args.command, "capture")
        self.assertEqual(args.duration, 10.0)
        self.assertEqual(args.sample_rate, 48000)
        self.assertEqual(args.channels, 2)
        self.assertEqual(args.device, 1)
        self.assertEqual(args.heartbeat_seconds, 2.5)
        self.assertEqual(args.jsonl, Path("out/capture.jsonl"))


# ======================================================================
# D5.5 — Capture MP3 subcommand args
# ======================================================================


class TestCaptureMp3Args(unittest.TestCase):
    def test_capture_mp3_flag_parses(self):
        parser = build_parser()
        args = parser.parse_args(["capture", "--duration", "60", "--mp3"])
        self.assertTrue(args.mp3)
        self.assertEqual(args.command, "capture")

    def test_capture_mp3_with_all_options(self):
        parser = build_parser()
        args = parser.parse_args([
            "capture", "--duration", "60", "--mp3",
            "--output-dir", "/tmp/songs",
            "--naming", "metadata",
            "--device-pattern", "Stereo Mix",
        ])
        self.assertTrue(args.mp3)
        self.assertEqual(args.output_dir, "/tmp/songs")
        self.assertEqual(args.naming, "metadata")
        self.assertEqual(args.device_pattern, "Stereo Mix")

    def test_capture_without_mp3_unchanged(self):
        parser = build_parser()
        args = parser.parse_args(["capture", "--duration", "10"])
        self.assertFalse(getattr(args, "mp3", False))

    def test_capture_mp3_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["capture", "--duration", "30", "--mp3"])
        self.assertEqual(args.output_dir, "captured_songs")
        self.assertEqual(args.naming, "timestamp")
        self.assertEqual(args.device_pattern, "CABLE Output")


# ======================================================================
# D5.8 — Govee-live capture flags
# ======================================================================


class TestGoveeLiveCaptureFlags(unittest.TestCase):
    def test_govee_live_capture_flag(self):
        parser = build_parser()
        args = parser.parse_args([
            "govee-live", "--device-ip", "192.168.1.1",
            "--duration", "60", "--capture",
        ])
        self.assertTrue(args.capture)

    def test_govee_live_capture_dir_and_naming(self):
        parser = build_parser()
        args = parser.parse_args([
            "govee-live", "--device-ip", "192.168.1.1",
            "--duration", "60", "--capture",
            "--capture-dir", "/tmp/cap",
            "--capture-naming", "metadata",
        ])
        self.assertEqual(args.capture_dir, "/tmp/cap")
        self.assertEqual(args.capture_naming, "metadata")

    def test_govee_live_capture_defaults(self):
        parser = build_parser()
        args = parser.parse_args([
            "govee-live", "--device-ip", "192.168.1.1",
            "--duration", "60",
        ])
        self.assertFalse(args.capture)
        self.assertEqual(args.capture_dir, "captured_songs")
        self.assertEqual(args.capture_naming, "timestamp")


# ======================================================================
# D5.1/5.2 — Session orchestrator config
# ======================================================================


class TestSessionOrchestratorConfig(unittest.TestCase):
    def test_orchestrator_config_from_session_args(self):
        """OrchestratorConfig constructed correctly from session-style args."""
        from dreamsync.capture.orchestrator import OrchestratorConfig

        capture_dir = "/tmp/songs"
        cfg = OrchestratorConfig(
            sample_rate=48000,
            channels=2,
            output_dir=capture_dir,
            naming="metadata",
            log_dir=str(Path(capture_dir) / "logs"),
        )
        self.assertEqual(cfg.sample_rate, 48000)
        self.assertEqual(cfg.channels, 2)
        self.assertEqual(cfg.output_dir, capture_dir)
        self.assertEqual(cfg.naming, "metadata")

    def test_orchestrator_shutdown_idempotent(self):
        """shutdown() is safe to call without start()."""
        from dreamsync.capture.orchestrator import CaptureOrchestrator, OrchestratorConfig
        import tempfile

        td = tempfile.mkdtemp()
        cfg = OrchestratorConfig(
            output_dir=str(Path(td) / "out"),
            log_dir=str(Path(td) / "logs"),
        )
        orch = CaptureOrchestrator(config=cfg)
        orch.shutdown()
        self.assertFalse(orch.is_running)
        stats = orch.stats
        self.assertIn("segments_completed", stats)
        self.assertIn("drift_corrections", stats)


# ======================================================================
# D5.3 — Spotify track change integration
# ======================================================================


class TestSpotifyTrackChange(unittest.TestCase):
    def test_track_change_data_format(self):
        """Timing data dict matches orchestrator.on_track_change() format."""
        new_track = MagicMock()
        new_track.name = "Test Song"
        new_track.artist = "Test Artist"
        new_track.album = "Test Album"
        new_track.duration_ms = 180_000

        timing_data = {
            "song_durations": [new_track.duration_ms / 1000.0],
            "current_playback_time": 0.0,
            "current_song": {
                "song_title": new_track.name,
                "artist": new_track.artist,
                "album": new_track.album,
            },
        }
        self.assertEqual(timing_data["song_durations"], [180.0])
        self.assertEqual(timing_data["current_song"]["song_title"], "Test Song")

    def test_track_change_includes_previous_song(self):
        """govee-live callback includes previous_song when old track is available."""
        new_track = MagicMock()
        new_track.name = "New Song"
        new_track.artist = "New Artist"
        new_track.album = "New Album"
        new_track.duration_ms = 200_000

        old_track = MagicMock()
        old_track.name = "Old Song"
        old_track.artist = "Old Artist"
        old_track.album = "Old Album"

        # Replicate the govee-live callback logic from cli.py
        timing_data = {
            "song_durations": [new_track.duration_ms / 1000.0],
            "current_playback_time": 0.0,
            "current_song": {
                "song_title": new_track.name,
                "artist": new_track.artist,
                "album": new_track.album,
            },
        }
        if old_track is not None:
            timing_data["previous_song"] = {
                "song_title": old_track.name,
                "artist": old_track.artist,
                "album": old_track.album,
            }

        self.assertIn("previous_song", timing_data)
        self.assertEqual(timing_data["previous_song"]["song_title"], "Old Song")
        self.assertEqual(timing_data["previous_song"]["artist"], "Old Artist")
        self.assertEqual(timing_data["previous_song"]["album"], "Old Album")

    def test_track_change_no_previous_song_when_old_is_none(self):
        """govee-live callback omits previous_song when old track is None."""
        new_track = MagicMock()
        new_track.name = "First Song"
        new_track.artist = "Artist"
        new_track.album = "Album"
        new_track.duration_ms = 180_000
        old_track = None

        timing_data = {
            "song_durations": [new_track.duration_ms / 1000.0],
            "current_playback_time": 0.0,
            "current_song": {
                "song_title": new_track.name,
                "artist": new_track.artist,
                "album": new_track.album,
            },
        }
        if old_track is not None:
            timing_data["previous_song"] = {
                "song_title": old_track.name,
                "artist": old_track.artist,
                "album": old_track.album,
            }

        self.assertNotIn("previous_song", timing_data)

    def test_track_change_reaches_orchestrator(self):
        """on_track_change() receives correct timing data."""
        from dreamsync.capture.orchestrator import CaptureOrchestrator, OrchestratorConfig
        import tempfile

        td = tempfile.mkdtemp()
        cfg = OrchestratorConfig(
            output_dir=str(Path(td) / "out"),
            log_dir=str(Path(td) / "logs"),
        )
        orch = CaptureOrchestrator(config=cfg)
        timing_data = {
            "song_durations": [180.0, 240.0],
            "current_playback_time": 0.0,
            "current_song": {"song_title": "Test", "artist": "Artist"},
        }
        count = orch.on_track_change(timing_data)
        self.assertEqual(count, 2)

    def test_previous_song_triggers_immediate_boundary(self):
        """previous_song in timing data inserts an immediate boundary at current frame."""
        from dreamsync.capture.boundary_queue import BoundaryQueue
        from dreamsync.capture.timing_integrator import TimingIntegrator

        bq = BoundaryQueue(safety_margin_frames=0)
        current_frame = 1_000_000
        ti = TimingIntegrator(
            boundary_queue=bq,
            get_current_frame=lambda: current_frame,
            sample_rate=44100,
        )

        timing_data = {
            "song_durations": [200.0],
            "current_playback_time": 0.0,
            "current_song": {"song_title": "New Song", "artist": "New Artist", "album": "New Album"},
            "previous_song": {"song_title": "Old Song", "artist": "Old Artist", "album": "Old Album"},
        }
        ti.on_track_change(timing_data)

        # The immediate boundary should be at current_frame with the old song's metadata
        entries = bq.entries()
        immediate = [e for e in entries if e.frame_position == current_frame]
        self.assertEqual(len(immediate), 1, "Expected immediate boundary at current frame")
        self.assertEqual(immediate[0].metadata["song_title"], "Old Song")

    def test_no_previous_song_no_immediate_boundary(self):
        """Without previous_song, no immediate boundary is inserted (only future ones)."""
        from dreamsync.capture.boundary_queue import BoundaryQueue
        from dreamsync.capture.timing_integrator import TimingIntegrator

        bq = BoundaryQueue(safety_margin_frames=0)
        current_frame = 1_000_000
        ti = TimingIntegrator(
            boundary_queue=bq,
            get_current_frame=lambda: current_frame,
            sample_rate=44100,
        )

        timing_data = {
            "song_durations": [200.0],
            "current_playback_time": 0.0,
            "current_song": {"song_title": "New Song", "artist": "New Artist", "album": "New Album"},
        }
        ti.on_track_change(timing_data)

        # Only future boundaries, none at current_frame
        entries = bq.entries()
        at_current = [e for e in entries if e.frame_position == current_frame]
        self.assertEqual(len(at_current), 0, "No immediate boundary without previous_song")
