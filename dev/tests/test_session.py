"""Tests for the session orchestrator."""

import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dreamsync.output.auto_detect import (
    DeviceConfig,
    DetectedDevice,
    LatencyStats,
)
from dreamsync.output.govee_lan import MultiGoveeLanAdapter, TransportMode


class RunSessionTests(unittest.TestCase):
    """Test that run_session wires up correctly."""

    def _make_detected(self) -> list[DetectedDevice]:
        return [
            DetectedDevice(
                name="Test",
                address="192.168.1.10",
                connection_type="lan",
                latency=LatencyStats(samples=[5.0]),
                role="realtime",
                config=DeviceConfig(name="Test", address="192.168.1.10"),
                transport=TransportMode.PTREAL,
            ),
        ]

    @patch("dreamsync.session.run_live_to_govee")
    @patch("dreamsync.session.build_multi_adapter")
    @patch("dreamsync.session.detect_all_devices")
    @patch("dreamsync.session.load_device_config")
    @patch("dreamsync.session.print_detection_report")
    def test_stop_event_passed(
        self, mock_report, mock_load, mock_detect, mock_build, mock_live
    ) -> None:
        mock_load.return_value = [DeviceConfig(name="Test", address="192.168.1.10")]
        mock_detect.return_value = self._make_detected()
        mock_adapter = MagicMock(spec=MultiGoveeLanAdapter)
        mock_build.return_value = mock_adapter
        mock_live.return_value = ([], {"duration_seconds": 1.0})

        from dreamsync.session import run_session

        run_session(config_path=Path("test.yaml"))

        # Verify stop_event was passed
        call_kwargs = mock_live.call_args[1]
        self.assertIn("stop_event", call_kwargs)
        self.assertIsInstance(call_kwargs["stop_event"], threading.Event)

    @patch("dreamsync.session.run_live_to_govee")
    @patch("dreamsync.session.build_multi_adapter")
    @patch("dreamsync.session.detect_all_devices")
    @patch("dreamsync.session.load_device_config")
    @patch("dreamsync.session.print_detection_report")
    def test_duration_none_passed(
        self, mock_report, mock_load, mock_detect, mock_build, mock_live
    ) -> None:
        mock_load.return_value = [DeviceConfig(name="Test", address="192.168.1.10")]
        mock_detect.return_value = self._make_detected()
        mock_adapter = MagicMock(spec=MultiGoveeLanAdapter)
        mock_build.return_value = mock_adapter
        mock_live.return_value = ([], {"duration_seconds": 1.0})

        from dreamsync.session import run_session

        run_session(config_path=Path("test.yaml"))

        call_kwargs = mock_live.call_args[1]
        self.assertIsNone(call_kwargs["duration_seconds"])

    @patch("dreamsync.session.run_live_to_govee")
    @patch("dreamsync.session.build_multi_adapter")
    @patch("dreamsync.session.detect_all_devices")
    @patch("dreamsync.session.load_device_config")
    @patch("dreamsync.session.print_detection_report")
    def test_deactivate_called_on_success(
        self, mock_report, mock_load, mock_detect, mock_build, mock_live
    ) -> None:
        mock_load.return_value = [DeviceConfig(name="Test", address="192.168.1.10")]
        mock_detect.return_value = self._make_detected()
        mock_adapter = MagicMock(spec=MultiGoveeLanAdapter)
        mock_build.return_value = mock_adapter
        mock_live.return_value = ([], {"duration_seconds": 1.0})

        from dreamsync.session import run_session

        run_session(config_path=Path("test.yaml"))
        mock_adapter.deactivate.assert_called_once()

    @patch("dreamsync.session.run_live_to_govee")
    @patch("dreamsync.session.build_multi_adapter")
    @patch("dreamsync.session.detect_all_devices")
    @patch("dreamsync.session.load_device_config")
    @patch("dreamsync.session.print_detection_report")
    def test_deactivate_called_on_exception(
        self, mock_report, mock_load, mock_detect, mock_build, mock_live
    ) -> None:
        mock_load.return_value = [DeviceConfig(name="Test", address="192.168.1.10")]
        mock_detect.return_value = self._make_detected()
        mock_adapter = MagicMock(spec=MultiGoveeLanAdapter)
        mock_build.return_value = mock_adapter
        mock_live.side_effect = RuntimeError("audio error")

        from dreamsync.session import run_session

        with self.assertRaises(RuntimeError):
            run_session(config_path=Path("test.yaml"))

        mock_adapter.deactivate.assert_called_once()


    @patch("dreamsync.session.run_live_to_govee")
    @patch("dreamsync.session.build_multi_adapter")
    @patch("dreamsync.session.detect_all_devices")
    @patch("dreamsync.session.load_device_config")
    @patch("dreamsync.session.print_detection_report")
    def test_health_monitor_started_and_stopped(
        self, mock_report, mock_load, mock_detect, mock_build, mock_live
    ) -> None:
        mock_load.return_value = [DeviceConfig(name="Test", address="192.168.1.10")]
        mock_detect.return_value = self._make_detected()
        mock_adapter = MagicMock(spec=MultiGoveeLanAdapter)
        mock_adapter.devices = []
        mock_build.return_value = mock_adapter
        mock_live.return_value = ([], {"duration_seconds": 1.0})

        from dreamsync.session import run_session

        with patch("dreamsync.device_health.DeviceHealthMonitor") as MockHealthMon:
            mock_monitor = MagicMock()
            MockHealthMon.return_value = mock_monitor

            run_session(
                config_path=Path("test.yaml"),
                health_monitor=True,
                health_interval=15.0,
            )

            MockHealthMon.assert_called_once()
            mock_monitor.start.assert_called_once()
            mock_monitor.stop.assert_called_once()

    @patch("dreamsync.session.run_live_to_govee")
    @patch("dreamsync.session.build_multi_adapter")
    @patch("dreamsync.session.detect_all_devices")
    @patch("dreamsync.session.load_device_config")
    @patch("dreamsync.session.print_detection_report")
    def test_queue_pipeline_uses_consumer_not_reactive_loop(
        self, mock_report, mock_load, mock_detect, mock_build, mock_live
    ) -> None:
        mock_load.return_value = [DeviceConfig(name="Test", address="192.168.1.10")]
        mock_detect.return_value = self._make_detected()
        mock_build.return_value = MagicMock(spec=MultiGoveeLanAdapter)

        from dreamsync.audio.route import resolve_audio_route
        from dreamsync.session import run_session

        with patch("dreamsync.capture.writer.check_ffmpeg", return_value=True), \
             patch("dreamsync.capture.orchestrator.CaptureOrchestrator") as MockOrchestrator, \
             patch("dreamsync.show_pipeline_worker.ShowPipelineWorker"), \
             patch("dreamsync.show_playback_consumer.ShowPlaybackConsumer") as MockConsumer:
            orchestrator = MockOrchestrator.return_value
            orchestrator.stats = {
                "segments_completed": 0,
                "elapsed_seconds": 0.0,
                "drift_corrections": 0,
            }
            consumer = MagicMock()
            MockConsumer.return_value = consumer
            consumer.run.side_effect = lambda stop: stop.set()
            run_session(
                config_path=Path("test.yaml"), capture=True, pipeline=True,
                playback_device=7,
                audio_route=resolve_audio_route("spotify-queue"),
            )

        mock_live.assert_not_called()
        MockConsumer.assert_called_once()
        self.assertEqual(MockConsumer.call_args.kwargs["audio_device"], 7)


if __name__ == "__main__":
    unittest.main()
