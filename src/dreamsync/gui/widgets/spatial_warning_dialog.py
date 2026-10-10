"""Explicit save override with a preview of invalid strip nodes."""

from dreamsync.gui.models.spatial_scene import project_point


def confirm_spatial_warnings(QtCore, QtGui, QtWidgets, parent, nodes, warnings):
    dialog = QtWidgets.QDialog(parent)
    dialog.setObjectName("spatialWarningDialog")
    dialog.setWindowTitle("Layout Warning")
    dialog.resize(700, 580)
    layout = QtWidgets.QVBoxLayout(dialog)
    explanation = QtWidgets.QLabel(
        "The layout has warnings. Offending nodes are outlined in red. "
        "Cancel to edit the layout, or explicitly override to save it."
    )
    explanation.setWordWrap(True)
    layout.addWidget(explanation)
    details = QtWidgets.QPlainTextEdit()
    details.setReadOnly(True)
    details.setPlainText("\n".join(
        f"{warning.label}: {error}" for warning in warnings for error in warning.errors
    ))
    details.setMaximumHeight(110)
    layout.addWidget(details)
    invalid_keys = {key for warning in warnings for link in warning.invalid_links for key in link}
    for warning in warnings:
        if len(warning.errors) > len(warning.invalid_links):
            invalid_keys.update(node.key for node in nodes if node.chain_key == warning.chain_key)
    scene = QtWidgets.QGraphicsScene(dialog)
    view = QtWidgets.QGraphicsView(scene)
    view.setObjectName("spatialWarningPreview")
    view.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    view.setMinimumHeight(260)
    # Each device gets its own preview, so overlapping strips remain identifiable.
    for row, warning in enumerate(warnings):
        chain = [node for node in nodes if node.chain_key == warning.chain_key]
        title = scene.addText(warning.label)
        title.setPos(0, row * 330)
        for node in chain:
            x, y = project_point(node.x, node.y, node.z)
            item = scene.addEllipse(
                x - 7, y + row * 330 - 7, 14, 14,
                QtGui.QPen(QtGui.QColor("#dc2626" if node.key in invalid_keys else "#64748b"),
                           3 if node.key in invalid_keys else 1),
                QtGui.QBrush(QtGui.QColor("#e2e8f0")),
            )
            item.setData(0, node.key)
            item.setToolTip(node.label)
            label = scene.addText(str((node.section_index or 0) + 1))
            label.setPos(x + 8, y + row * 330 - 12)
    layout.addWidget(view)
    advanced = QtWidgets.QPushButton("Advanced >> Override Warning")
    advanced.setObjectName("spatialWarningAdvancedButton")
    advanced.setCheckable(True)
    layout.addWidget(advanced)
    options = QtWidgets.QWidget()
    options_layout = QtWidgets.QVBoxLayout(options)
    checks = {}
    for warning in warnings:
        if not warning.invalid_links:
            continue
        check = QtWidgets.QCheckBox("Permanently disable spacing warnings for this device")
        check.setObjectName(f"disableSpacingWarnings:{warning.chain_key}")
        check.setToolTip(warning.label)
        options_layout.addWidget(QtWidgets.QLabel(warning.label))
        options_layout.addWidget(check)
        checks[warning.chain_key] = check
    options.hide()
    advanced.toggled.connect(options.setVisible)
    layout.addWidget(options)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
    override = buttons.addButton("Override Warning and Save", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
    override.setObjectName("spatialWarningOverrideButton")
    override.setAutoDefault(False)
    buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setDefault(True)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    accepted = dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted
    disabled = {address for address, check in checks.items() if check.isChecked()} if accepted else set()
    dialog.deleteLater()
    return accepted, disabled
