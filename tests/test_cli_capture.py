import unittest
from pathlib import Path

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
