import unittest

from dreamsync.cli import build_parser


class SmokeTests(unittest.TestCase):
    def test_cli_builds(self) -> None:
        parser = build_parser()
        self.assertEqual(parser.prog, "dreamsync")
