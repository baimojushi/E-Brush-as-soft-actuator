# Install the E-Brush Studio environment without modifying system Python.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    & py -3 -m venv (Join-Path $Root '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create local virtualenv: check Python 3.10+.' }
}
& $Python -m pip install -r (Join-Path $Root 'studio\requirements-ui.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Host 'E-Brush environment ready. Start START_UI_PREVIEW.ps1 for hardware-free inspection.'
