# Start E-Brush Studio + Agent acquisition. C3 BLE is the hardware default;
# S3 COM remains opt-in for compatibility and A/B comparison.
param(
    [ValidateSet('ble','wifi','serial')]
    [string]$BrushTransport = 'ble',
    [string]$BrushPort = 'COM7',
    [string]$BleName = 'EBrush-C3',
    [string]$BleAddress = '',
    [ValidateSet('none','shm','direct')]
    [string]$CameraBackend = 'none'
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'Run SETUP_STUDIO.ps1 first.' }
$Args = @('-u', 'main.py', '--camera-backend', $CameraBackend,
          '--no-camera-supervisor', '--brush-transport', $BrushTransport,
          '--agent-host', '127.0.0.1', '--agent-port', '8765')
if ($BrushTransport -eq 'serial') { $Args += @('--port', $BrushPort) }
if ($BrushTransport -eq 'ble') {
    $Args += @('--ble-name', $BleName)
    if ($BleAddress) { $Args += @('--ble-address', $BleAddress) }
}
Push-Location (Join-Path $Root 'studio')
try {
    & $Python @Args
    if ($LASTEXITCODE -ne 0) { throw "E-Brush Studio exited with code $LASTEXITCODE" }
}
finally { Pop-Location }
