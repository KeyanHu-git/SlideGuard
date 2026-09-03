@echo off
setlocal
set "SLIDEGUARD_ROOT=%~dp0"
set "PYTHONPATH=%SLIDEGUARD_ROOT%src"
where python.exe >nul 2>nul
if errorlevel 1 (
  echo SlideGuard requires Python 3.10 or newer.
  exit /b 20
)
if "%~1"=="" (
  python -m slideguard doctor
  echo.
  echo Usage: drag a PPTX onto this file, or run:
  echo   SlideGuard.cmd export file.pptx --slides 1 --pdf-max-bytes 2500000
  pause
  exit /b %errorlevel%
)
if /I "%~x1"==".pptx" (
  python -m slideguard export "%~1" --slides 1 --pdf-max-bytes 2500000 --svg-max-bytes 2500000
) else (
  python -m slideguard %*
)
exit /b %errorlevel%
