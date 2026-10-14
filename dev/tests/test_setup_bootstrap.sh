#!/usr/bin/env bash
# Offline regression: an existing non-default Python must not be replaced.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT
fixture="$test_root/existing checkout"
mkdir -p "$fixture/scripts" "$fixture/.venv/bin" "$fixture/.bootstrap/bin"
cp "$repo_root/scripts/setup.sh" "$fixture/scripts/"
cp "$repo_root/.python-version" "$fixture/"
cat > "$fixture/.venv/bin/python" <<'PYTHON'
#!/usr/bin/env bash
[[ "$*" == '--version' ]] || exit 1
echo 'Python 3.11.9'
PYTHON
cat > "$fixture/.bootstrap/bin/uv" <<'UV'
#!/usr/bin/env bash
set -eu
# Reject attempts to recreate the environment or install a different Python.
[[ "$1" == pip ]] || exit 1
[[ "$2" == install || "$2" == check ]] || exit 1
[[ "$3" == --python && "$4" == "$PWD/.venv/bin/python" ]] || exit 1
echo "$2" >> "$PWD/uv-calls"
UV
chmod +x "$fixture/.venv/bin/python" "$fixture/.bootstrap/bin/uv"
bash "$fixture/scripts/setup.sh"
[[ "$(cat "$fixture/uv-calls")" == $'install\ncheck' ]]

# A broken interpreter must stop setup before touching dependencies.
printf '#!/usr/bin/env bash\nexit 1\n' > "$fixture/.venv/bin/python"
if bash "$fixture/scripts/setup.sh"; then
    echo 'Expected broken environment to fail' >&2
    exit 1
fi
[[ "$(cat "$fixture/uv-calls")" == $'install\ncheck' ]]
echo 'Bootstrap regression checks passed'
