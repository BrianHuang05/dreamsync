#!/usr/bin/env bash
# One-terminal SSH test of the real DreamSync capture/playback audio path.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
python="${DREAMSYNC_PYTHON:-$root/.venv/bin/python}"
if [[ ! -x "$python" ]]; then
    echo "DreamSync Python not found: $python (set DREAMSYNC_PYTHON to override)." >&2
    exit 1
fi
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}"
# Audio uses the user's Pulse socket, not an X11 connection from an SSH shell.
unset DISPLAY
exec "$python" -u "$root/dev/scripts/test_linux_audio_duplex.py" "$@"
