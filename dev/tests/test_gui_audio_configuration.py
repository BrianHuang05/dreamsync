"""Audio configuration migration and launcher routing regressions."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dreamsync.gui import settings as preferences
from dreamsync.gui.models.capture_settings import CaptureSettings
from dreamsync.gui.settings import GuiSettings, GuiSettingsStore, resolve_gui_audio_settings


@pytest.mark.parametrize("raw, expected", [
    ({"selected_output_audio_device_id": 7,
      "capture_settings": {"pipeline_playback_device_id": 9}}, 7),
    ({"capture_settings": {"pipeline_playback_device_id": 9}}, 9),
    ({"playback_audio_device_id": None,
      "selected_output_audio_device_id": 7}, None),
])
def test_legacy_playback_choices_migrate_to_one_setting(tmp_path, monkeypatch, raw, expected):
    monkeypatch.setattr(preferences, "sys", SimpleNamespace(platform="win32"))
    store = GuiSettingsStore(tmp_path / "gui.json")
    store.path.write_text(json.dumps(raw))
    loaded = store.load()
    assert loaded.selected_output_audio_device_id == expected
    assert loaded.capture_settings.pipeline_playback_device_id == expected
    store.save(loaded)
    saved = json.loads(store.path.read_text())
    assert saved["playback_audio_device_id"] == expected
    assert "selected_output_audio_device_id" not in saved
    assert "pipeline_playback_device_id" not in saved["capture_settings"]
    assert store.load() == loaded


def test_linux_discards_stale_device_ids_and_uses_active_launcher_monitor(tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setenv("DREAMSYNC_ALOOP_CAPTURE_SOURCE", "alsa_output.loopback.monitor")
    monkeypatch.setenv("DREAMSYNC_ALOOP_PLAYBACK_SINK", "alsa_output.loopback")
    monkeypatch.setenv("PULSE_SOURCE", "alsa_input.microphone")
    settings = GuiSettings(selected_output_audio_device_id=7,
        capture_settings=CaptureSettings(device_pattern="old.monitor", pipeline_playback_device_id=9))
    resolved = resolve_gui_audio_settings(settings)
    assert resolved.selected_output_audio_device_id is None
    assert resolved.capture_settings.pipeline_playback_device_id is None
    assert resolved.capture_settings.device_pattern == "alsa_output.loopback.monitor"
    store = GuiSettingsStore(tmp_path / "gui.json")
    store.save(resolved)
    saved = json.loads(store.path.read_text())
    assert "playback_audio_device_id" not in saved
    assert "device_pattern" not in saved["capture_settings"]
    assert store.load() == resolved


def test_linux_without_launcher_has_no_stale_capture_fallback(monkeypatch):
    monkeypatch.setattr(preferences, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.delenv("DREAMSYNC_ALOOP_CAPTURE_SOURCE", raising=False)
    monkeypatch.delenv("DREAMSYNC_ALOOP_PLAYBACK_SINK", raising=False)
    monkeypatch.delenv("PULSE_SOURCE", raising=False)
    resolved = resolve_gui_audio_settings(GuiSettings())
    assert not resolved.capture_settings.device_pattern
    assert "Capture device pattern is required." in resolved.capture_settings.validate()


def test_linux_player_opens_pulse_output_instead_of_default_hardware(monkeypatch):
    from dreamsync.show import player
    monkeypatch.setattr(player, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setenv("PULSE_SINK", "alsa_output.usb_speakers")
    sd = MagicMock()
    sd.query_hostapis.return_value = [{"name": "ALSA"}]
    sd.query_devices.return_value = [
        {"name": "default", "hostapi": 0, "max_output_channels": 2},
        {"name": "pulse", "hostapi": 0, "max_output_channels": 2},
    ]
    audio = SimpleNamespace(signal=np.zeros(100, dtype=np.float32))
    with patch.object(player, "decode_mp3", return_value=audio), patch.dict("sys.modules", sounddevice=sd):
        playback = player.AudioPlayer(Path("test.mp3"))
        playback.play()
        assert sd.OutputStream.call_args.kwargs["device"] == 1
        playback.stop()


def test_pulse_output_requires_output_capability():
    from dreamsync.audio.system_input import pulse_output_device_id
    sd = SimpleNamespace(query_hostapis=lambda: [{"name": "ALSA"}], query_devices=lambda: [
        {"name": "pulse", "hostapi": 0, "max_input_channels": 2, "max_output_channels": 0},
    ])
    with pytest.raises(RuntimeError, match="Pulse ALSA output endpoint"):
        pulse_output_device_id(sd)


def test_linux_config_exposes_one_route_and_read_only_capture(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from dreamsync.gui import main_window
    from dreamsync.gui.qt import require_qt
    from dreamsync.gui.widgets import queue_panel
    from dreamsync.gui.services.audio_device_service import AudioDeviceService
    monkeypatch.setattr(preferences, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(main_window, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(queue_panel, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setenv("DREAMSYNC_ALOOP_CAPTURE_SOURCE", "alsa_output.loopback.monitor")
    monkeypatch.setenv("DREAMSYNC_ALOOP_PLAYBACK_SINK", "alsa_output.loopback")
    monkeypatch.setenv("PULSE_SINK", "alsa_output.usb_speakers")
    monkeypatch.setattr(AudioDeviceService, "list_physical_sink_options", lambda _: ())
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = main_window.create_main_window(require_qt(), GuiSettings(),
        config_path=Path("dev/devices-dummy.yaml"))
    try:
        runtime = window.findChild(QtWidgets.QGroupBox, "runtimeRoutingGroup")
        playback = window.findChild(QtWidgets.QComboBox, "outputAudioDeviceCombo")
        reactive = window.findChild(QtWidgets.QGroupBox, "reactiveSettingsGroup")
        input_device = window.findChild(QtWidgets.QComboBox, "liveInputDeviceCombo")
        capture = window.findChild(QtWidgets.QComboBox, "captureSourceCombo")
        status = window.findChild(QtWidgets.QLabel, "resolvedCaptureRouteLabel")
        assert not runtime.isAncestorOf(playback)
        assert not runtime.isAncestorOf(input_device)
        assert reactive.isAncestorOf(input_device)
        assert window.findChild(QtWidgets.QComboBox, "pipelinePlaybackDeviceCombo") is None
        assert window.findChild(QtWidgets.QGroupBox, "audioPlaybackSettingsGroup").isHidden()
        assert capture.isHidden() and not capture.isEnabled()
        assert capture.currentData() == "alsa_output.loopback.monitor"
        assert status.text() == "Resolved capture monitor: alsa_output.loopback.monitor"
        assert app is not None
    finally:
        window.close()
