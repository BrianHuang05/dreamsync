from dataclasses import asdict, replace
import json

import pytest

from dreamsync.linux_launcher_settings import LinuxLauncherSettings, resolve, save_bridge
from dreamsync.gui.settings import GuiSettings, GuiSettingsStore


def test_migration_and_round_trip(tmp_path):
    store = GuiSettingsStore(tmp_path / 'gui.json')
    store.path.write_text('{}')
    assert store.load().linux_launcher == LinuxLauncherSettings()
    expected = LinuxLauncherSettings(browser_profile='Music Profile', browser_url='https://open.spotify.com/')
    store.save(GuiSettings(linux_launcher=expected))
    assert store.load().linux_launcher == expected


def test_precedence_for_every_field(tmp_path):
    path = tmp_path / 'launcher.json'
    defaults = LinuxLauncherSettings()
    saved = LinuxLauncherSettings(route_mode='live-learning', audio_source='spotify-desktop',
        physical_sink='alsa_output.speakers', browser_command='/opt/My Firefox/firefox',
        browser_profile='Music Profile', browser_url='https://open.spotify.com/', spotify_command='/opt/My Spotify/spotify')
    assert resolve(path, {}) == defaults
    save_bridge(saved, path)
    assert resolve(path, {}) == saved
    for key, value in asdict(defaults).items():
        assert getattr(resolve(path, {key: value}), key) == value


@pytest.mark.parametrize('changes', [
    {'browser_url': 'file:///tmp/test'}, {'browser_url': 'https://'},
    {'browser_command': ''}, {'physical_sink': 'sink; command'},
    {'browser_profile': 'one\ntwo'}, {'audio_source': 'other'},
])
def test_invalid_settings(changes):
    with pytest.raises(ValueError):
        replace(LinuxLauncherSettings(), **changes).validate()


def test_atomic_bridge_and_failed_validation(tmp_path):
    path = tmp_path / 'launcher.json'
    save_bridge(LinuxLauncherSettings(), path)
    original = path.read_bytes()
    with pytest.raises(ValueError):
        save_bridge(LinuxLauncherSettings(browser_url='bad'), path)
    assert path.read_bytes() == original
    assert json.loads(original)['browser_profile'] == 'DreamSync'


def test_form_preserves_disabled_values_and_rejects_invalid_url(monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    QtWidgets = pytest.importorskip('PySide6.QtWidgets')
    from dreamsync.gui.widgets.linux_launcher_form import build_linux_launcher_form
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    expected = LinuxLauncherSettings(browser_url='https://open.spotify.com/', browser_profile='Music Profile')
    group, snapshot = build_linux_launcher_form(QtWidgets, expected)
    assert snapshot() == expected
    source = group.findChild(QtWidgets.QComboBox, 'linux_launcher_audio_source')
    url = group.findChild(QtWidgets.QLineEdit, 'linux_launcher_browser_url')
    source.setCurrentText('spotify-desktop')
    assert not url.isEnabled()
    assert snapshot().browser_url == expected.browser_url
    source.setCurrentText('browser')
    assert url.isEnabled()
    url.setText('file:///tmp/private')
    with pytest.raises(ValueError):
        snapshot()
    group.close()
    assert app is not None


def test_sink_dropdown_refresh_and_missing_saved_output(monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    QtWidgets = pytest.importorskip('PySide6.QtWidgets')
    from dreamsync.gui.widgets.linux_launcher_form import build_linux_launcher_form
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    options = [('alsa_output.usb', 'USB speakers'), ('bluez_output.headphones', 'Headphones')]
    group, snapshot = build_linux_launcher_form(
        QtWidgets, LinuxLauncherSettings(physical_sink='alsa_output.usb'),
        discover_sinks=lambda: tuple(options))
    combo = group.findChild(QtWidgets.QComboBox, 'linux_launcher_physical_sink')
    refresh = group.findChild(QtWidgets.QPushButton, 'linux_launcher_refresh_sinks')
    assert not combo.isEditable()
    assert snapshot().physical_sink == 'alsa_output.usb'
    options.clear()
    refresh.click()
    assert 'Unavailable' in combo.currentText()
    assert snapshot().physical_sink == 'alsa_output.usb'
    options.append(('bluez_output.headphones', 'Headphones'))
    refresh.click()
    combo.setCurrentIndex(combo.findData('bluez_output.headphones'))
    assert snapshot().physical_sink == 'bluez_output.headphones'
    combo.setCurrentIndex(0)
    assert snapshot().physical_sink == ''
    group.close()
    assert app is not None


def test_physical_sink_discovery_excludes_virtual_outputs(monkeypatch):
    from types import SimpleNamespace
    from dreamsync.gui.services import audio_device_service as service
    monkeypatch.setattr(service, 'sys', SimpleNamespace(platform='linux'))
    rows = [
        {'name': 'alsa_output.usb', 'description': 'USB speakers'},
        {'name': 'bluez_output.headphones', 'description': 'Headphones'},
        {'name': 'alsa_output.platform-snd_aloop.0', 'description': 'Loopback'},
        {'name': 'alsa_output.custom', 'properties': {'alsa.card_name': 'Loopback'}},
        {'name': 'dreamsync_capture'}, {'name': 'xrdp-sink'},
        {'name': 'alsa_output.virtual', 'properties': {'device.class': 'abstract'}},
    ]
    def run(args, **kwargs):
        assert args == ['pactl', '--format=json', 'list', 'sinks']
        assert kwargs['timeout'] == 3
        return SimpleNamespace(returncode=0, stdout=json.dumps(rows))
    monkeypatch.setattr(service.subprocess, 'run', run)
    assert dict(service.AudioDeviceService().list_physical_sink_options()) == {
        'alsa_output.usb': 'USB speakers', 'bluez_output.headphones': 'Headphones'}


def test_sink_discovery_failure_keeps_saved_selection(monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    QtWidgets = pytest.importorskip('PySide6.QtWidgets')
    from dreamsync.gui.widgets.linux_launcher_form import build_linux_launcher_form
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    def fail():
        raise RuntimeError('PipeWire unavailable')
    group, snapshot = build_linux_launcher_form(
        QtWidgets, LinuxLauncherSettings(physical_sink='alsa_output.usb'), discover_sinks=fail)
    assert snapshot().physical_sink == 'alsa_output.usb'
    assert 'PipeWire unavailable' in group.findChild(QtWidgets.QLabel, 'linux_launcher_sink_status').text()
    group.close()
    assert app is not None


def test_main_window_save_writes_bridge_only_after_valid_configuration(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import patch
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    QtWidgets = pytest.importorskip('PySide6.QtWidgets')
    from dreamsync.gui import main_window
    from dreamsync.gui.qt import require_qt
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    store = GuiSettingsStore(tmp_path / 'gui.json')
    window = main_window.create_main_window(require_qt(), GuiSettings(),
        config_path=Path('dev/devices-dummy.yaml'), settings_store=store)
    monkeypatch.setattr(main_window, 'sys', SimpleNamespace(platform='linux'))
    url = window.findChild(QtWidgets.QLineEdit, 'linux_launcher_browser_url')
    bridge = tmp_path / 'config/dreamsync/linux-launcher.json'
    try:
        url.setText('https://open.spotify.com/')
        assert window._dreamsync_save_configuration()
        assert store.load().linux_launcher.browser_url == url.text()
        assert json.loads(bridge.read_text())['browser_url'] == url.text()
        before = bridge.read_bytes()
        url.setText('file:///tmp/test')
        with patch.object(QtWidgets.QMessageBox, 'warning'):
            assert not window._dreamsync_save_configuration()
        assert bridge.read_bytes() == before
    finally:
        window.close()
    assert app is not None
