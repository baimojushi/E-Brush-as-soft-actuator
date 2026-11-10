# Native Material 3 UI preview; no camera and no brush serial ownership.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'Please run SETUP_STUDIO.ps1 first.' }
Push-Location (Join-Path $Root 'studio')
try {
    & $Python -u main.py --camera-backend none --no-brush --agent-host 127.0.0.1 --agent-port 8765
    if ($LASTEXITCODE -ne 0) { throw "Studio exited with code $LASTEXITCODE" }
}
finally { Pop-Location }
