"""Interactive, cancellable advanced test for a single device."""

import threading

from dreamsync.gui.services.device_discovery_service import DeviceTestSpec


def show_device_test_dialog(
    QtCore, QtWidgets, parent, service, entry, *, segments, transport, protocol, save_segments=None, lighting_advance_ms=0.0, save_lighting_advance=None, tone_device_id=None
):
    state = {"running": False, "pending": None, "thread": None, "error": "", "closing": None}
    stop_event = threading.Event()

    class TestDialog(QtWidgets.QDialog):
        def done(self, result):
            state["closing"] = result
            stop_test()
            if state["thread"] is None:
                super().done(result)

    dialog = TestDialog(parent)
    dialog.setObjectName("advancedDeviceTestDialog")
    dialog.setWindowTitle(f"Advanced Test — {entry.name}")
    layout = QtWidgets.QFormLayout(dialog)
    color = QtWidgets.QLineEdit("#3366ff")
    color.setObjectName("deviceTestColorEdit")
    pattern = QtWidgets.QComboBox()
    pattern.setObjectName("deviceTestPatternCombo")
    patterns = ("solid", "alternate", "rainbow", "walk") if entry.source == "lan" else ("solid", "walk")
    for value in patterns:
        pattern.addItem(value.title(), value)
    brightness = QtWidgets.QDoubleSpinBox()
    brightness.setObjectName("deviceTestBrightnessSpin")
    brightness.setRange(0.05, 1.0)
    brightness.setSingleStep(0.05)
    brightness.setValue(0.2)
    segment_count = QtWidgets.QSpinBox()
    segment_count.setObjectName("deviceTestSegmentsSpin")
    segment_count.setRange(1, 2048)
    segment_count.setValue(segments)
    layout.addRow("Segments", segment_count)
    layout.addRow("Color", color)
    layout.addRow("Pattern", pattern)
    layout.addRow("Brightness", brightness)
    advance = QtWidgets.QDoubleSpinBox()
    advance.setObjectName("deviceLightingAdvanceSpin")
    advance.setRange(-2000.0, 2000.0)
    advance.setSingleStep(10.0)
    advance.setDecimals(0)
    advance.setSuffix(" ms")
    advance.setValue(float(lighting_advance_ms))
    layout.addRow("Light advance", advance)
    sync_button = QtWidgets.QPushButton("Play Tone + Flash")
    sync_button.setObjectName("deviceCalibrationButton")
    sync_button.setToolTip("Hear a short tone and compare it with this device's flash")
    layout.addRow(sync_button)
    save_sync_button = QtWidgets.QPushButton("Save Calibration")
    save_sync_button.setObjectName("saveDeviceCalibrationButton")
    save_sync_button.setEnabled(save_lighting_advance is not None)
    layout.addRow(save_sync_button)
    calibration_status = QtWidgets.QLabel("If the flash follows the tone, increase advance. If it leads, decrease it, then repeat.")
    calibration_status.setObjectName("deviceCalibrationStatusLabel")
    calibration_status.setWordWrap(True)
    layout.addRow(calibration_status)
    warning = QtWidgets.QLabel(
        f"Only {entry.name} ({entry.address}) will be tested. "
        "The device will be turned off when the test ends."
    )
    warning.setTextFormat(QtCore.Qt.TextFormat.PlainText)
    warning.setWordWrap(True)
    layout.addRow(warning)
    status = QtWidgets.QLabel("Ready.")
    status.setObjectName("deviceTestStatusLabel")
    status.setWordWrap(True)
    layout.addRow(status)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
    start = buttons.addButton("Start Test", QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)
    start.setObjectName("deviceTestStartButton")
    stop = buttons.addButton("Stop Test", QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)
    stop.setObjectName("deviceTestStopButton")
    stop.setEnabled(False)
    save = buttons.addButton("Update Config", QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)
    save.setObjectName("deviceTestUpdateConfigButton")
    save.setEnabled(save_segments is not None)
    save.setToolTip("Save the segment count to this device's config entry.")
    save_status = QtWidgets.QLabel()
    save_status.setObjectName("deviceTestSaveStatusLabel")
    save_status.setWordWrap(True)
    layout.addRow(save_status)
    layout.addRow(buttons)

    def update_config():
        try:
            save_segments(segment_count.value())
        except Exception as exc:
            save_status.setText(f"Could not update config: {exc}")
        else:
            save_status.setText(f"Saved {segment_count.value()} segments to config.")

    save.clicked.connect(update_config)

    def save_calibration():
        try:
            save_lighting_advance(advance.value())
        except Exception as exc:
            calibration_status.setText(f"Could not save calibration: {exc}")
        else:
            calibration_status.setText(f"Saved {advance.value():.0f} ms light advance for {entry.name}.")

    save_sync_button.clicked.connect(save_calibration)

    def run_calibration():
        if state["thread"] is not None:
            return
        state["error"] = ""
        stop_event.clear()
        initial_status = (
            "Connecting to the light over Bluetooth…"
            if entry.source == "ble" else "Preparing the light…"
        )
        state["calibration_message"] = initial_status
        calibration_status.setText(initial_status)
        sync_button.setEnabled(False)
        spec = DeviceTestSpec(
            color=color.text().strip(), pattern="solid", brightness=brightness.value(),
            segments=segment_count.value(), transport=transport, protocol=protocol,
        )
        advance_ms = advance.value()
        state["thread"] = threading.Thread(
            target=lambda: _run_calibration(spec, advance_ms), daemon=True,
        )
        state["thread"].start()

    def _run_calibration(spec, advance_ms):
        try:
            service.calibrate_device(
                entry, spec, advance_ms=advance_ms, stop_event=stop_event,
                tone_device_id=tone_device_id,
                on_status=lambda message: state.__setitem__("calibration_message", message),
            )
        except Exception as exc:
            state["error"] = str(exc).strip() or type(exc).__name__

    sync_button.clicked.connect(run_calibration)

    def stop_test():
        state["running"] = False
        state["pending"] = None
        stop_event.set()
        start.setEnabled(state["thread"] is None)
        stop.setEnabled(False)
        status.setText("Stopping…" if state["thread"] else "Test stopped.")

    def request_test():
        state["running"] = True
        start.setEnabled(False)
        stop.setEnabled(True)
        spec = DeviceTestSpec(
            color=color.text().strip(), pattern=str(pattern.currentData()),
            brightness=brightness.value(), segments=segment_count.value(),
            transport=transport, protocol=protocol, repeat=True,
        )
        try:
            spec.validate(entry.source)
        except ValueError as exc:
            stop_event.set()
            state["pending"] = None
            status.setText(str(exc))
            return
        state["latest"] = spec
        if state["thread"] is not None and not stop_event.is_set():
            status.setText(f"Testing {spec.pattern}, {spec.brightness:.0%} brightness…")
        else:
            state["pending"] = spec
            status.setText("Starting test…")

    def run_test(spec):
        try:
            service.test_device(
                entry, spec, stop_event=stop_event, spec_provider=lambda: state["latest"]
            )
        except Exception as exc:
            state["error"] = str(exc).strip() or type(exc).__name__

    def poll_test():
        thread = state["thread"]
        if thread is not None:
            calibration_status.setText(state.get("calibration_message", "Preparing the test…"))
            if thread.is_alive():
                return
            thread.join()
            state["thread"] = None
            if state["error"]:
                state["running"] = False
                state["pending"] = None
                status.setText(f"Test failed: {state['error']}")
            elif not state["running"]:
                status.setText("Test stopped.")
            calibration_status.setText(
                "Calibration test failed: " + state["error"] if state["error"]
                else "Flash and tone test finished. Nudge the advance and repeat until they align."
            )
            sync_button.setEnabled(True)
        if state["closing"] is not None:
            QtWidgets.QDialog.done(dialog, state["closing"])
            return
        start.setEnabled(not state["running"])
        stop.setEnabled(state["running"])
        spec = state["pending"]
        if spec is None:
            return
        state["pending"] = None
        state["error"] = ""
        stop_event.clear()
        state["thread"] = threading.Thread(target=run_test, args=(spec,))
        state["thread"].start()
        status.setText(f"Testing {spec.pattern}, {spec.brightness:.0%} brightness…")

    start.clicked.connect(request_test)
    stop.clicked.connect(stop_test)
    buttons.rejected.connect(dialog.reject)
    color.textChanged.connect(lambda: request_test() if state["running"] else None)
    pattern.currentIndexChanged.connect(lambda: request_test() if state["running"] else None)
    segment_count.valueChanged.connect(lambda: request_test() if state["running"] else None)
    brightness.valueChanged.connect(lambda: request_test() if state["running"] else None)
    timer = QtCore.QTimer(dialog)
    timer.setInterval(50)
    timer.timeout.connect(poll_test)
    timer.start()
    dialog.exec()
    timer.stop()
    dialog.deleteLater()
