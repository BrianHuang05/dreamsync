import unittest
from unittest.mock import patch
import urllib.error

from dreamsync.output.ledfx import _default_transport


class LedFxErrorHandlingTests(unittest.TestCase):
    def test_http_error_is_caught_and_logged(self) -> None:
        error = urllib.error.HTTPError(
            url="http://127.0.0.1:8888/api/virtuals/abc/effects",
            code=500,
            msg="Server Error",
            hdrs=None,
            fp=None,
        )
        with (
            patch("dreamsync.output.ledfx.urllib.request.urlopen", side_effect=error),
            self.assertLogs("dreamsync.output.ledfx", level="WARNING") as logs,
        ):
            _default_transport("http://127.0.0.1:8888/api/virtuals/abc/effects", {}, 0.1)
        self.assertTrue(any("http error" in msg.lower() for msg in logs.output))

    def test_url_error_is_caught_and_logged(self) -> None:
        error = urllib.error.URLError("Connection refused")
        with (
            patch("dreamsync.output.ledfx.urllib.request.urlopen", side_effect=error),
            self.assertLogs("dreamsync.output.ledfx", level="WARNING") as logs,
        ):
            _default_transport("http://127.0.0.1:8888/api/virtuals/abc/effects", {}, 0.1)
        self.assertTrue(any("connection error" in msg.lower() for msg in logs.output))

    def test_timeout_error_is_caught_and_logged(self) -> None:
        with (
            patch("dreamsync.output.ledfx.urllib.request.urlopen", side_effect=TimeoutError()),
            self.assertLogs("dreamsync.output.ledfx", level="WARNING") as logs,
        ):
            _default_transport("http://127.0.0.1:8888/api/virtuals/abc/effects", {}, 0.1)
        self.assertTrue(any("timeout" in msg.lower() for msg in logs.output))

    def test_os_error_is_caught_and_logged(self) -> None:
        with (
            patch("dreamsync.output.ledfx.urllib.request.urlopen", side_effect=OSError("Network down")),
            self.assertLogs("dreamsync.output.ledfx", level="WARNING") as logs,
        ):
            _default_transport("http://127.0.0.1:8888/api/virtuals/abc/effects", {}, 0.1)
        self.assertTrue(any("network error" in msg.lower() for msg in logs.output))
