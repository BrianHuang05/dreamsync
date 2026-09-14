"""Structured launcher preferences; also executable with stdlib-only Python."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit


@dataclass(frozen=True)
class LinuxLauncherSettings:
    route_mode: str = 'spotify-queue'
    audio_source: str = 'browser'
    physical_sink: str = ''
    browser_command: str = 'firefox'
    browser_profile: str = 'DreamSync'
    browser_url: str = ''
    spotify_command: str = 'spotify'

    def validate(self) -> None:
        for key, value in asdict(self).items():
            if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError(f'{key} must be text without control characters')
        if self.route_mode not in {'spotify-queue', 'live-learning'}:
            raise ValueError('Route mode must be spotify-queue or live-learning')
        if self.audio_source not in {'browser', 'spotify-desktop'}:
            raise ValueError('Audio source must be browser or spotify-desktop')
        for key in ('browser_command', 'spotify_command', 'browser_profile'):
            if not getattr(self, key).strip():
                raise ValueError(f'{key} cannot be empty')
        if self.physical_sink and not re.fullmatch(r'[A-Za-z0-9_.:-]+', self.physical_sink):
            raise ValueError('Physical sink must be a single PipeWire endpoint name')
        if self.browser_url:
            url = urlsplit(self.browser_url)
            if url.scheme not in {'http', 'https'} or not url.hostname or any(c.isspace() for c in self.browser_url):
                raise ValueError('Browser URL must be blank or a valid http/https URL')


def bridge_path() -> Path:
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'dreamsync' / 'linux-launcher.json'


def from_data(raw: dict) -> LinuxLauncherSettings:
    if not isinstance(raw, dict):
        raise ValueError('Launcher settings must be a JSON object')
    settings = LinuxLauncherSettings(**{k: v for k, v in raw.items() if k in asdict(LinuxLauncherSettings())})
    settings.validate()
    return settings


def resolve(path: Path, overrides: dict) -> LinuxLauncherSettings:
    raw = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(raw, dict):
        raise ValueError('Launcher settings must be a JSON object')
    return from_data({**raw, **overrides})


def save_bridge(settings: LinuxLauncherSettings, path: Path | None = None) -> None:
    settings.validate()
    path = path or bridge_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.linux-launcher-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(asdict(settings), stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for key in asdict(LinuxLauncherSettings()):
        parser.add_argument('--' + key.replace('_', '-'), default=None)
    args = vars(parser.parse_args())
    try:
        settings = resolve(bridge_path(), {k: v for k, v in args.items() if v is not None})
    except (OSError, ValueError) as exc:
        print(f'Invalid Linux launcher settings: {exc}', file=sys.stderr)
        return 2
    # Validated single-line fields, transferred as data; never evaluated as shell.
    sys.stdout.buffer.write(('\n'.join(asdict(settings).values()) + '\n').encode('utf-8'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
