import unittest
from pathlib import Path

from dreamsync.cli import build_parser


class PlotCommandTests(unittest.TestCase):
    def test_plot_command_parses(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["plot", "in.jsonl", "out.png"])
        self.assertEqual(args.command, "plot")
        self.assertEqual(args.input_jsonl, Path("in.jsonl"))
        self.assertEqual(args.output_png, Path("out.png"))

    def test_replay_command_parses(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["replay", "song.wav", "--jsonl", "out/replay.jsonl"])
        self.assertEqual(args.command, "replay")
        self.assertEqual(args.path, Path("song.wav"))
        self.assertEqual(args.jsonl, Path("out/replay.jsonl"))

    def test_govee_live_single_device_ip(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["govee-live", "--device-ip", "192.168.1.23", "--segments", "10", "--duration", "30"]
        )
        self.assertEqual(args.command, "govee-live")
        self.assertEqual(args.device_ip, "192.168.1.23")
        self.assertEqual(args.segments, 10)
        self.assertIsNone(args.govee_devices)

    def test_govee_live_multi_device(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "govee-live",
                "--device", "192.168.1.23:15",
                "--device", "192.168.1.24:10:accent",
                "--duration", "60",
                "--render-mode", "pulse",
            ]
        )
        self.assertEqual(args.command, "govee-live")
        self.assertEqual(args.govee_devices, ["192.168.1.23:15", "192.168.1.24:10:accent"])
        self.assertIsNone(args.device_ip)
        self.assertEqual(args.render_mode, "pulse")
