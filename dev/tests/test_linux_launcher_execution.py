"""Run the real Bash launcher with isolated external process stubs."""
from pathlib import Path
import os
import shlex
import shutil
import subprocess
import sys

import pytest

from dreamsync.linux_launcher_settings import LinuxLauncherSettings, save_bridge


@pytest.fixture
def harness(tmp_path):
    bash = 'C:/Program Files/Git/bin/bash.exe' if os.name == 'nt' else shutil.which('bash')
    if not bash or not Path(bash).exists():
        pytest.skip('Bash unavailable')
    root = tmp_path / 'repository with spaces'
    scripts = root / 'dev/scripts'
    scripts.mkdir(parents=True)
    module_dir = root / 'src/dreamsync'
    module_dir.mkdir(parents=True)
    for name in ('start_linux_dreamsync.sh', 'start_linux_spotify_queue.sh'):
        target = scripts / name
        target.write_text((Path('dev/scripts') / name).read_text(), newline='\n')
        target.chmod(0o755)
    shutil.copyfile('src/dreamsync/linux_launcher_settings.py', module_dir / 'linux_launcher_settings.py')
    (root / 'dev/devices.yaml').write_text('{}')
    binaries = root / '.venv/bin'
    binaries.mkdir(parents=True)

    def executable(path, text):
        path.write_text('#!/usr/bin/env bash\n' + text, encoding='utf-8', newline='\n')
        path.chmod(0o755)

    executable(binaries / 'python', 'if [[ "$1" == "-m" ]]; then printf "%s\\0" "$@" > "$TEST_APP_ARGS"; exec "$TEST_APP_COMMAND"; fi\nexec ' + shlex.quote(Path(sys.executable).as_posix()) + ' "$@"\n')
    executable(scripts / 'setup_linux_pipewire_capture.sh', """
if [[ "$1" == "--teardown" ]]; then exit 0; fi
if [[ "${2:-}" == "missing_sink" ]]; then
    echo 'Physical sink not found: missing_sink' >&2
    exit 1
fi
mkdir -p "$XDG_STATE_HOME/dreamsync"
printf 'dreamsync_physical_sink=alsa_output.test\\ndreamsync_browser_sink=loopback\\ndreamsync_capture_source=%s\\n' "${TEST_CAPTURE_SOURCE:-loopback.monitor}" > "$XDG_STATE_HOME/dreamsync/pipewire-route-state"
""")
    executable(binaries / 'pactl', 'exit 0\n')
    executable(binaries / 'pavucontrol', 'exit 0\n')
    executable(binaries / 'setsid', 'exec "$@"\n')
    source = binaries / 'Music Browser'
    executable(source, """printf '%s\\n' "$PULSE_SINK" > "$TEST_SOURCE_SINK"
printf '%s\\0' "$@" > "$TEST_SOURCE_ARGS"
""")
    command = binaries / 'app'
    executable(command, """for i in {1..100}; do
    [[ -f "$TEST_SOURCE_ARGS" ]] && break
    sleep 0.02
done
printf '%s\\n%s\\n' "$PULSE_SINK" "$PULSE_SOURCE" > "$TEST_APP_ENV"
""")
    env = {**os.environ, 'XDG_CONFIG_HOME': (tmp_path / 'config').as_posix(),
           'XDG_STATE_HOME': (tmp_path / 'state').as_posix(),
           'TEST_SOURCE_ARGS': (tmp_path / 'args').as_posix(),
           'TEST_SOURCE_SINK': (tmp_path / 'source-sink').as_posix(),
           'TEST_APP_ENV': (tmp_path / 'app-env').as_posix(),
           'TEST_APP_ARGS': (tmp_path / 'app-args').as_posix(),
           'TEST_APP_COMMAND': command.as_posix()}
    # This harness stubs route setup. Precreate its state directory so Git
    # Bash's mkdir need not inspect restricted Windows ancestor directories.
    (tmp_path / 'state/dreamsync').mkdir(parents=True)
    settings = LinuxLauncherSettings(browser_command=source.as_posix(), spotify_command=source.as_posix(),
        browser_profile='Profile with spaces', browser_url='https://example.com/?q=$(touch%20oops)&x=1')
    save_bridge(settings, Path(env['XDG_CONFIG_HOME']) / 'dreamsync/linux-launcher.json')

    def run(*args, wrapper=False, default_command=False):
        bin_path = binaries.as_posix()
        if os.name == 'nt':
            bin_path = '/' + bin_path[0].lower() + bin_path[2:]
        script = scripts / ('start_linux_spotify_queue.sh' if wrapper else 'start_linux_dreamsync.sh')
        invocation = 'export PATH=' + shlex.quote(bin_path) + ':/usr/bin:/bin:"$PATH"; exec ' + shlex.quote(script.as_posix())
        command_args = args if wrapper or default_command else (*args, '--', command.as_posix())
        invocation += ' ' + ' '.join(shlex.quote(a) for a in command_args)
        return subprocess.run([bash, '-c', invocation], env=env, capture_output=True, text=True, timeout=15)

    return run, env, settings


def test_real_launcher_preserves_arguments_and_routes(harness):
    run, env, settings = harness
    result = run()
    assert result.returncode == 0, result.stderr
    assert Path(env['TEST_SOURCE_ARGS']).read_bytes().split(b'\0')[:-1] == [
        b'--new-instance', b'-P', b'Profile with spaces', b'--new-window', settings.browser_url.encode()]
    assert Path(env['TEST_SOURCE_SINK']).read_text().strip() == 'loopback'
    assert Path(env['TEST_APP_ENV']).read_text().splitlines() == ['alsa_output.test', 'loopback.monitor']
    assert settings.browser_url not in result.stdout


def test_real_launcher_spotify_override(harness):
    run, env, _ = harness
    result = run('--audio-source', 'spotify-desktop')
    assert result.returncode == 0, result.stderr
    assert Path(env['TEST_SOURCE_ARGS']).read_bytes() == b'\0'


@pytest.mark.parametrize('args, message', [
    (('--browser', 'nonexistent-dreamsync-browser'), 'Audio-source executable not found'),
    (('--physical-sink', 'missing_sink'), 'Physical sink not found'),
    (('--browser-url', 'file:///tmp/test'), 'Invalid Linux launcher settings'),
])
def test_real_launcher_rejects_invalid_startup(harness, args, message):
    run, env, _ = harness
    result = run(*args)
    assert result.returncode != 0
    assert message in result.stderr
    assert not Path(env['TEST_APP_ENV']).exists()


def test_real_launcher_rejects_mismatched_monitor(harness):
    run, env, _ = harness
    env['TEST_CAPTURE_SOURCE'] = 'wrong.monitor'
    result = run()
    assert result.returncode != 0
    assert 'Queue capture must use the playback sink monitor' in result.stderr
    assert not Path(env['TEST_APP_ENV']).exists()


@pytest.mark.parametrize('source', ['browser', 'spotify-desktop'])
def test_queue_wrapper_preserves_saved_audio_source(harness, source):
    from dataclasses import replace
    run, env, settings = harness
    save_bridge(replace(settings, audio_source=source, route_mode='live-learning'),
                Path(env['XDG_CONFIG_HOME']) / 'dreamsync/linux-launcher.json')
    result = run(wrapper=True)
    assert result.returncode == 0, result.stderr
    assert f'source={source} route=spotify-queue' in result.stdout
    arguments = Path(env['TEST_SOURCE_ARGS']).read_bytes()
    assert (b'--new-instance' in arguments) == (source == 'browser')


def test_no_argument_launcher_starts_gui_with_repo_config(harness):
    run, env, _ = harness
    result = run(default_command=True)
    assert result.returncode == 0, result.stderr
    arguments = Path(env['TEST_APP_ARGS']).read_bytes().split(b'\0')[:-1]
    assert arguments[:4] == [b'-m', b'dreamsync', b'gui', b'--config']
    # Git Bash uses /c/...; either platform's path must point into this fixture.
    assert arguments[4].endswith(b'/repository with spaces/dev/devices.yaml')
    assert Path(env['TEST_APP_ENV']).read_text().splitlines() == ['alsa_output.test', 'loopback.monitor']


@pytest.mark.parametrize('device', ['7', 'pick'])
def test_queue_wrapper_rejects_portaudio_override_before_launching(harness, device):
    run, env, _ = harness
    result = run('--playback-device', device, wrapper=True)
    assert result.returncode == 2
    assert 'use --physical-sink NAME' in result.stderr
    assert not Path(env['TEST_SOURCE_ARGS']).exists()
    assert not Path(env['TEST_APP_ENV']).exists()


@pytest.mark.parametrize('process_binary', ['firefox', 'spotify'])
def test_route_watcher_tracks_recreated_streams_and_sinks_without_repeated_moves(tmp_path, process_binary):
    bash = 'C:/Program Files/Git/bin/bash.exe' if os.name == 'nt' else shutil.which('bash')
    if not bash or not Path(bash).exists():
        pytest.skip('Bash unavailable')
    # Exercise the launcher's actual routing functions synchronously, with
    # changing server snapshots instead of depending on a polling/sleep race.
    launcher = Path('dev/scripts/start_linux_dreamsync.sh').read_text()
    functions = launcher[launcher.index('sink_input_ids_for_process()'):launcher.index('start_audio_source_route_watcher()')]
    runner = tmp_path / 'watcher.sh'
    runner.write_text('''#!/usr/bin/env bash
set -euo pipefail
export PATH=/usr/bin:/bin:$PATH
browser_sink=alsa_output.loopback
target_id=42
stream_id=51
stream_sink=42
vanishing=false
pactl() {
    case "$*" in
        'list short sinks')
            [[ -z "$target_id" ]] || printf '%s\\talsa_output.loopback\\n9\\talsa_output.speakers\\n' "$target_id"
            return 0 ;;
        'list sink-inputs')
            # Include an unrelated Python playback stream. It must stay on
            # the physical sink even while multiple source streams are moved.
            printf 'Sink Input #900\\n application.process.binary = "python"\\n'
            printf 'Sink Input #%s\\n application.process.binary = "%s"\\n' "$stream_id" "$TEST_BINARY"
            printf 'Sink Input #52\\n application.process.binary = "%s"\\n' "$TEST_BINARY" ;;
        'list short sink-inputs')
            printf '900\\t9\\n%s\\t%s\\n52\\t%s\\n' "$stream_id" "$stream_sink" "${second_sink:-$target_id}" ;;
        'move-sink-input '*)
            printf 'move %s to %s\\n' "$2" "$3"
            if [[ "$vanishing" == true && "$2" == "$stream_id" ]]; then return 1; fi
            if [[ "$2" == "$stream_id" ]]; then stream_sink="$target_id"; fi
            if [[ "$2" == 52 ]]; then second_sink="$target_id"; fi ;;
        *) echo "Unexpected pactl command: $*" >&2; return 1 ;;
    esac
}
''' + functions + '''
# Correctly attached streams must remain untouched across repeated passes.
route_audio_source_streams "$TEST_BINARY"
route_audio_source_streams "$TEST_BINARY"
# A new browser/Spotify stream has a new ID and starts on the physical sink.
stream_id=61
stream_sink=9
route_audio_source_streams "$TEST_BINARY"
route_audio_source_streams "$TEST_BINARY"
# The same endpoint name now resolves to a new runtime sink ID.
target_id=84
route_audio_source_streams "$TEST_BINARY"
route_audio_source_streams "$TEST_BINARY"
# A disappeared sink is retried later, never replaced by a cached numeric ID.
target_id=''
stream_id=71
stream_sink=9
route_audio_source_streams "$TEST_BINARY"
target_id=96
route_audio_source_streams "$TEST_BINARY"
# One vanished stream must not prevent routing the remaining source streams.
stream_id=81
stream_sink=9
vanishing=true
target_id=108
second_sink=9
route_audio_source_streams "$TEST_BINARY"
''', newline='\n')
    result = subprocess.run([bash, runner.as_posix()], env={**os.environ, 'TEST_BINARY': process_binary},
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    moves = [line for line in result.stdout.splitlines() if line.startswith('move ')]
    assert moves == [f'move {stream_id} to alsa_output.loopback' for stream_id in (61, 61, 71, 81, 52)]
    assert f'Routed {process_binary} sink input 81' not in result.stdout
