$ErrorActionPreference = "Stop"

$venvPython = Join-Path $PSScriptRoot ".build-venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    python -m venv (Join-Path $PSScriptRoot ".build-venv")
}

$pyInstallerAvailable = $false
try {
    & $venvPython -c "import PyInstaller, tkinterdnd2" *> $null
    $pyInstallerAvailable = $LASTEXITCODE -eq 0
}
catch {
    $pyInstallerAvailable = $false
}

if (-not $pyInstallerAvailable) {
    & $venvPython -m pip install pyinstaller tkinterdnd2
}

& $venvPython -m PyInstaller --noconsole --onefile --name VideoFixer --icon assets\videofixer.ico --add-data "assets;assets" --collect-all tkinterdnd2 main.py

if (-not (Test-Path (Join-Path $PSScriptRoot "dist\VideoFixer.exe"))) {
    throw "Build failed: dist\VideoFixer.exe was not created."
}

Write-Host ""
Write-Host "Built: dist\VideoFixer.exe"
