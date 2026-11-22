$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
Push-Location $repoRoot
try {
    python -m PyInstaller --noconfirm --windowed --onedir --name PixelStudio --paths tools --collect-submodules numpy --distpath out/pixel-studio --workpath out/pixel-studio-build --specpath out --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --exclude-module PySide6.QtPdf --exclude-module PySide6.QtQml tools/pixel_studio/launcher.py
    if ($LASTEXITCODE -ne 0) { throw 'Pixel Studio build failed.' }
} finally { Pop-Location }
