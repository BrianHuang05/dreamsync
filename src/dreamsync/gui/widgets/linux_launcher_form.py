"""Config controls for the next Linux launcher start."""
from dataclasses import asdict

from dreamsync.linux_launcher_settings import LinuxLauncherSettings


def build_linux_launcher_form(QtWidgets, settings):
    group = QtWidgets.QGroupBox('Linux launcher — applies at next launcher start')
    layout = QtWidgets.QFormLayout(group)
    controls = {}
    labels = {
        'route_mode': 'Audio route', 'audio_source': 'Audio source',
        'physical_sink': 'Physical PipeWire sink (blank: remembered/default)',
        'browser_command': 'Firefox executable', 'browser_profile': 'Firefox profile',
        'browser_url': 'Initial browser URL (optional)', 'spotify_command': 'Spotify executable',
    }
    for key, value in asdict(settings).items():
        choices = {'route_mode': ('spotify-queue', 'live-learning'),
                   'audio_source': ('browser', 'spotify-desktop')}.get(key)
        if choices:
            control = QtWidgets.QComboBox()
            control.addItems(choices)
            control.setCurrentText(value)
        else:
            control = QtWidgets.QLineEdit(value)
        control.setObjectName('linux_launcher_' + key)
        controls[key] = control
        layout.addRow(labels[key], control)

    def update_source(*_):
        browser = controls['audio_source'].currentText() == 'browser'
        for key in ('browser_command', 'browser_profile', 'browser_url'):
            controls[key].setEnabled(browser)
        controls['spotify_command'].setEnabled(not browser)

    controls['audio_source'].currentTextChanged.connect(update_source)
    update_source()

    def snapshot():
        values = {key: (control.currentText() if key in {'route_mode', 'audio_source'} else control.text())
                  for key, control in controls.items()}
        result = LinuxLauncherSettings(**values)
        result.validate()
        return result

    return group, snapshot
