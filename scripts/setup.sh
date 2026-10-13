#!/usr/bin/env bash
# Bootstrap without a system Python. Run with: bash scripts/setup.sh
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
version="$(tr -d '\r\n' < .python-version)"
bootstrap="$repo_root/.bootstrap"
uv="$bootstrap/bin/uv"
python="$repo_root/.venv/bin/python"

# Never replace an existing environment or silently switch its interpreter.
if [[ -e .venv ]]; then
    if [[ ! -x "$python" ]] || ! "$python" -c "import sys; sys.exit(0 if '.'.join(map(str, sys.version_info[:2])) == '$version' else 1)"; then
        echo "The existing .venv must be a working Python $version environment. Rename it, then rerun setup." >&2
        exit 1
    fi
fi

export UV_UNMANAGED_INSTALL="$bootstrap/bin"
export UV_PYTHON_INSTALL_DIR="$bootstrap/python"
export UV_CACHE_DIR="$bootstrap/cache"
if [[ ! -x "$uv" ]]; then
    mkdir -p "$bootstrap"
    echo 'Downloading uv from https://astral.sh/uv/install.sh ...'
    if command -v curl >/dev/null 2>&1; then
        curl --fail --location --silent --show-error https://astral.sh/uv/install.sh -o "$bootstrap/install-uv.sh"
    elif command -v wget >/dev/null 2>&1; then
        wget -q https://astral.sh/uv/install.sh -O "$bootstrap/install-uv.sh"
    else
        echo 'Install curl or wget using your Linux package manager, then rerun setup.' >&2
        exit 1
    fi
    sh "$bootstrap/install-uv.sh"
    if [[ ! -x "$uv" ]]; then
        echo 'uv installation failed. Check internet access and rerun setup.' >&2
        exit 1
    fi
fi

if [[ ! -e .venv ]]; then
    "$uv" venv --python "$version" --managed-python --seed "$repo_root/.venv"
fi
"$uv" pip install --python "$python" --editable '.[gui,session,spotify]'
"$uv" pip check --python "$python"
echo 'DreamSync setup complete. From the repository root, launch with:'
echo '.venv/bin/python -m dreamsync gui --config dev/devices-dummy.yaml'
