import unittest
from pathlib import Path
from unittest.mock import patch

from dreamsync.cli import build_parser, main
from dreamsync.gui.app import GuiDependencyError


class SmokeTests(unittest.TestCase):
    def test_cli_builds(self) -> None:
        parser = build_parser()
        self.assertEqual(parser.prog, "dreamsync")

    def test_gui_command_parses(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["gui", "--config", "devices.yaml", "--profile", "profile.yaml"])
        self.assertEqual(args.command, "gui")
        self.assertEqual(args.config, Path("devices.yaml"))
        self.assertEqual(args.profile, Path("profile.yaml"))

    def test_gui_command_invokes_launcher(self) -> None:
        with patch("dreamsync.gui.app.launch_gui", return_value=0) as mock_launch:
            result = main(["gui", "--close-after-ms", "1"])
        self.assertEqual(result, 0)
        mock_launch.assert_called_once()

    def test_gui_command_reports_missing_dependency(self) -> None:
        with patch("dreamsync.gui.app.launch_gui", side_effect=GuiDependencyError("missing gui")):
            result = main(["gui"])
        self.assertEqual(result, 1)
