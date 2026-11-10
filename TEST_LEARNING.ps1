param([switch]$CoreOnly)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    throw 'Project virtualenv not found. Run the original setup script first.'
}
Set-Location $Root
& $Python -c "import pytest"
if ($LASTEXITCODE -ne 0) {
    throw 'pytest is missing. Run: .\.venv\Scripts\python.exe -m pip install pytest'
}
if (-not $CoreOnly) {
    & $Python -c "import PySide6"
    if ($LASTEXITCODE -ne 0) {
        throw 'Native GUI acceptance needs PySide6. Install project GUI requirements, or use -CoreOnly.'
    }
}
$PreviousPlatform = $env:QT_QPA_PLATFORM
$env:QT_QPA_PLATFORM = 'offscreen'
try {
    if ($CoreOnly) {
        & $Python -m pytest -q -p no:cacheprovider learning_tests --ignore=learning_tests/test_learning_qt.py
    } else {
        & $Python -m pytest -q -p no:cacheprovider learning_tests
    }
    if ($LASTEXITCODE -ne 0) { throw 'Learning acceptance failed. Keep the complete test output.' }
} finally {
    if ($null -eq $PreviousPlatform) { Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue }
    else { $env:QT_QPA_PLATFORM = $PreviousPlatform }
}
Write-Host 'Tests complete. Native window size/DPI/keyboard/hardware checks are still required.'
