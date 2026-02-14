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

    def test_ledfx_test_command_parses(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["ledfx-test", "--base-url", "http://127.0.0.1:8888", "--virtual-id", "abc", "--mode", "motion"]
        )
        self.assertEqual(args.command, "ledfx-test")
        self.assertEqual(args.base_url, "http://127.0.0.1:8888")
        self.assertEqual(args.virtual_id, ["abc"])
        self.assertEqual(args.mode, "motion")

    def test_ledfx_replay_command_parses(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "ledfx-replay",
                "song.wav",
                "--base-url",
                "http://127.0.0.1:8888",
                "--virtual-id",
                "abc",
                "--realtime",
                "--max-events",
                "100",
                "--jsonl",
                "out/ledfx_replay.jsonl",
            ]
        )
        self.assertEqual(args.command, "ledfx-replay")
        self.assertEqual(args.path, Path("song.wav"))
        self.assertEqual(args.base_url, "http://127.0.0.1:8888")
        self.assertEqual(args.virtual_id, ["abc"])
        self.assertTrue(args.realtime)
        self.assertEqual(args.max_events, 100)
        self.assertEqual(args.jsonl, Path("out/ledfx_replay.jsonl"))

    def test_ledfx_live_command_parses(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "ledfx-live",
                "--duration",
                "15",
                "--base-url",
                "http://127.0.0.1:8888",
                "--virtual-id",
                "abc",
                "--device",
                "2",
                "--jsonl",
                "out/ledfx_live.jsonl",
            ]
        )
        self.assertEqual(args.command, "ledfx-live")
        self.assertEqual(args.duration, 15.0)
        self.assertEqual(args.base_url, "http://127.0.0.1:8888")
        self.assertEqual(args.virtual_id, ["abc"])
        self.assertEqual(args.device, 2)
        self.assertEqual(args.jsonl, Path("out/ledfx_live.jsonl"))
