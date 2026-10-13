# Run from any directory: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1
# Python and uv stay in this checkout; no administrator access is required.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$bootstrap = Join-Path $repoRoot '.bootstrap'
$uv = Join-Path $bootstrap 'bin\uv.exe'
$venv = Join-Path $repoRoot '.venv'
$python = Join-Path $venv 'Scripts\python.exe'
$version = (Get-Content (Join-Path $repoRoot '.python-version') -Raw).Trim()
$savedEnvironment = @{}
foreach ($name in @('UV_UNMANAGED_INSTALL', 'UV_PYTHON_INSTALL_DIR', 'UV_CACHE_DIR')) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

function Invoke-Uv {
    & $uv @args
    if ($LASTEXITCODE -ne 0) { throw "uv failed (exit $LASTEXITCODE). Fix the error above and rerun setup." }
}

Push-Location $repoRoot
try {
    # Never replace an existing environment or silently switch its interpreter.
    if (Test-Path $venv) {
        if (-not (Test-Path $python)) {
            throw 'The existing .venv is incomplete. Rename it, then rerun setup.'
        }
        & $python -c "import sys; sys.exit(0 if '.'.join(map(str, sys.version_info[:2])) == '$version' else 1)"
        if ($LASTEXITCODE -ne 0) {
            throw "The existing .venv must use Python $version. Rename it, then rerun setup."
        }
    }

    $env:UV_UNMANAGED_INSTALL = Join-Path $bootstrap 'bin'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $bootstrap 'python'
    $env:UV_CACHE_DIR = Join-Path $bootstrap 'cache'
    if (-not (Test-Path $uv)) {
        Write-Host 'Downloading uv from https://astral.sh/uv/install.ps1 ...'
        New-Item -ItemType Directory -Force -Path $bootstrap | Out-Null
        $installer = Join-Path $bootstrap 'install-uv.ps1'
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/install.ps1' -OutFile $installer
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $uv)) {
            throw 'uv installation failed. Check internet access and rerun setup.'
        }
    }

    if (-not (Test-Path $venv)) {
        # uv downloads a managed interpreter even if Python is absent from PATH.
        Invoke-Uv venv --python $version --managed-python --seed $venv
    }
    Invoke-Uv pip install --python $python --editable '.[gui,session,spotify]'
    Invoke-Uv pip check --python $python
    Write-Host 'DreamSync setup complete. From the repository root, launch with:'
    Write-Host '.\.venv\Scripts\python.exe -m dreamsync gui --config dev/devices-dummy.yaml'
} finally {
    Pop-Location
    foreach ($name in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
    }
}
