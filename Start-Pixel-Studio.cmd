@echo off
cd /d "%~dp0"
if exist "out\pixel-studio\PixelStudio\PixelStudio.exe" (
  start "" "out\pixel-studio\PixelStudio\PixelStudio.exe" %*
) else (
  python -m tools.pixel_studio %*
  if errorlevel 1 pause
)
