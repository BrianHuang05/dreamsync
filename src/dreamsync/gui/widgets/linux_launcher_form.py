"""Config controls for the next Linux launcher start."""
from dataclasses import asdict

from dreamsync.linux_launcher_settings import LinuxLauncherSettings
from dreamsync.gui.services.audio_device_service import AudioDeviceService


def build_linux_launcher_form(QtWidgets, settings, *, discover_sinks=None):
    group = QtWidgets.QGroupBox('Linux launcher — applies at next launcher start')
    layout = QtWidgets.QFormLayout(group)
    controls = {}
    labels = {
        'route_mode': 'Audio route', 'audio_source': 'Audio source',
        'physical_sink': 'Physical audio output',
        'browser_command': 'Firefox executable', 'browser_profile': 'Firefox profile',
        'browser_url': 'Initial browser URL (optional)', 'spotify_command': 'Spotify executable',
    }
    for key, value in asdict(settings).items():
        choices = {'route_mode': ('spotify-queue', 'live-learning'),
                   'audio_source': ('browser', 'spotify-desktop')}.get(key)
        if key == 'physical_sink':
            control = QtWidgets.QComboBox()
            control.setEditable(False)
            control.setMinimumContentsLength(32)
            control.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        elif choices:
            control = QtWidgets.QComboBox()
            control.addItems(choices)
            control.setCurrentText(value)
        else:
            control = QtWidgets.QLineEdit(value)
        control.setObjectName('linux_launcher_' + key)
        controls[key] = control
        layout.addRow(labels[key], control)

    sink_status = QtWidgets.QLabel()
    sink_status.setWordWrap(True)
    sink_status.setObjectName('linux_launcher_sink_status')
    refresh = QtWidgets.QPushButton('Refresh audio outputs')
    refresh.setObjectName('linux_launcher_refresh_sinks')
    layout.addRow(refresh, sink_status)
    discover_sinks = discover_sinks or AudioDeviceService().list_physical_sink_options

    def refresh_sinks(*_, initial=False):
        combo = controls['physical_sink']
        selected = settings.physical_sink if initial else (combo.currentData() or '')
        try:
            options = discover_sinks()
            message = '' if options else 'No physical outputs found. Connect your output device, then refresh.'
        except RuntimeError as exc:
            options = ()
            message = str(exc)
        combo.clear()
        combo.addItem('Automatic (system default / remembered output)', '')
        for name, label in options:
            combo.addItem(f'{label} — {name}', name)
        if selected and combo.findData(selected) < 0:
            combo.addItem(f'Unavailable (saved): {selected}', selected)
            message = message or 'Saved output is unavailable. Reconnect it or select another output.'
        combo.setCurrentIndex(max(0, combo.findData(selected)))
        sink_status.setText(message)

    refresh.clicked.connect(refresh_sinks)
    refresh_sinks(initial=True)

    def update_source(*_):
        browser = controls['audio_source'].currentText() == 'browser'
        for key in ('browser_command', 'browser_profile', 'browser_url'):
            controls[key].setEnabled(browser)
        controls['spotify_command'].setEnabled(not browser)

    controls['audio_source'].currentTextChanged.connect(update_source)
    update_source()

    def snapshot():
        values = {key: (control.currentText() if key in {'route_mode', 'audio_source'} else control.text())
                  for key, control in controls.items() if key != 'physical_sink'}
        values['physical_sink'] = controls['physical_sink'].currentData() or ''
        result = LinuxLauncherSettings(**values)
        result.validate()
        return result

    return group, snapshot
