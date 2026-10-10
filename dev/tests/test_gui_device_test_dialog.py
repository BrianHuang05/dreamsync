import os
import time

import pytest

from dreamsync.gui.services.device_discovery_service import DiscoveredDeviceEntry
from dreamsync.gui.widgets.device_test_dialog import show_device_test_dialog


def test_advanced_popup_updates_stops_restarts_and_closes():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    QtWidgets = pytest.importorskip('PySide6.QtWidgets')
    QtCore = pytest.importorskip('PySide6.QtCore')
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    calls, stopped, errors, saved = [], [], [], []

    class Service:
        def test_device(self, entry, spec, *, stop_event, spec_provider):
            assert spec.repeat
            calls.append(spec)
            while not stop_event.wait(0.01):
                current = spec_provider()
                if current != spec:
                    spec = current
                    calls.append(spec)
            stopped.append(spec)

    stage = 0
    deadline = time.monotonic() + 8

    def advance():
        nonlocal stage
        dialog = app.activeModalWidget()
        if dialog is None:
            return
        try:
            assert time.monotonic() < deadline, f'Timed out at stage {stage}'
            start = dialog.findChild(QtWidgets.QPushButton, 'deviceTestStartButton')
            stop = dialog.findChild(QtWidgets.QPushButton, 'deviceTestStopButton')
            if stage == 0:
                assert dialog.findChild(QtWidgets.QDoubleSpinBox, 'deviceTestDurationSpin') is None
                start.click()
                stage = 1
            elif stage == 1 and len(calls) == 1:
                dialog.findChild(QtWidgets.QComboBox, 'deviceTestPatternCombo').setCurrentIndex(1)
                dialog.findChild(QtWidgets.QLineEdit, 'deviceTestColorEdit').setText('#ff0000')
                dialog.findChild(QtWidgets.QDoubleSpinBox, 'deviceTestBrightnessSpin').setValue(0.5)
                dialog.findChild(QtWidgets.QSpinBox, 'deviceTestSegmentsSpin').setValue(8)
                stage = 2
            elif stage == 2 and len(calls) == 2:
                assert calls[-1].pattern == 'alternate'
                assert calls[-1].color == '#ff0000'
                assert calls[-1].brightness == 0.5
                assert calls[-1].segments == 8
                assert not stopped
                dialog.findChild(QtWidgets.QPushButton, 'deviceTestUpdateConfigButton').click()
                assert saved == [8]
                assert dialog.isVisible()
                stop.click()
                stage = 3
            elif stage == 3 and start.isEnabled():
                assert dialog.isVisible()
                assert len(stopped) == 1
                start.click()
                stage = 4
            elif stage == 4 and len(calls) == 3:
                dialog.reject()
                stage = 5
        except BaseException as exc:
            errors.append(exc)
            dialog.reject()

    timer = QtCore.QTimer()
    timer.timeout.connect(advance)
    timer.start(20)
    show_device_test_dialog(
        QtCore, QtWidgets, None, Service(),
        DiscoveredDeviceEntry(key='test', source='lan', name='Test', address='test'),
        segments=3, transport='ptreal', protocol='segment', save_segments=saved.append,
    )
    timer.stop()
    assert not errors, errors
    assert stage == 5
    assert len(stopped) == 2
