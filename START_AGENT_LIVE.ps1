# Start the desktop UI with real electronic-brush acquisition enabled.
# E-Brush remains the sole owner of the serial port.
param(
    [string]$BrushPort = 'COM7',
    [ValidateSet('none','shm','direct')]
    [string]$CameraBackend = 'none'
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'Run SETUP_STUDIO.ps1 first.' }
Push-Location (Join-Path $Root 'studio')
try {
    & $Python -u main.py --camera-backend $CameraBackend --no-camera-supervisor --port $BrushPort --agent-host 127.0.0.1 --agent-port 8765
    if ($LASTEXITCODE -ne 0) { throw "E-Brush Studio exited with code $LASTEXITCODE" }
}
finally { Pop-Location }
