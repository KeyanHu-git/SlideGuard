[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$OutputRoot = "",
    [string]$Wheelhouse = "",
    [string]$BuildRoot = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $env:LOCALAPPDATA -and -not $BuildRoot) { throw "LOCALAPPDATA is unavailable; pass -BuildRoot with a short path." }
$shortBuildRoot = if ($BuildRoot) { [IO.Path]::GetFullPath($BuildRoot) } else { Join-Path $env:LOCALAPPDATA "SlideGuard\portable-build" }
$venvRoot = Join-Path $shortBuildRoot "venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$distRoot = if ($OutputRoot) { [IO.Path]::GetFullPath($OutputRoot) } else { Join-Path $shortBuildRoot "dist" }
$workRoot = Join-Path $shortBuildRoot "pyinstaller"

& $Python -m venv --clear $venvRoot
if ($LASTEXITCODE -ne 0) { throw "Cannot create the isolated build environment." }

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Cannot prepare pip in the isolated build environment." }

$installArgs = @("-m", "pip", "install")
if ($Wheelhouse) {
    $resolvedWheelhouse = [IO.Path]::GetFullPath($Wheelhouse)
    $installArgs += @("--no-index", "--find-links", $resolvedWheelhouse)
}
$installArgs += ".[gui,portable]"
Push-Location -LiteralPath $repoRoot
try {
    & $venvPython @installArgs
    if ($LASTEXITCODE -ne 0) { throw "Cannot install SlideGuard build dependencies." }
    & $venvPython -c "from PySide6 import QtCore; print('Qt preflight:', QtCore.qVersion())"
    if ($LASTEXITCODE -ne 0) { throw "Qt cannot load in the isolated build environment." }
    & $venvPython scripts\portable_build.py --dist-root $distRoot --work-root $workRoot
    if ($LASTEXITCODE -ne 0) { throw "Portable package build failed." }

    $packageExe = Join-Path (Join-Path $distRoot "SlideGuard") "SlideGuard.exe"
    $guiStdout = Join-Path $shortBuildRoot "gui-smoke.stdout.txt"
    $guiStderr = Join-Path $shortBuildRoot "gui-smoke.stderr.txt"
    $oldQpa = $env:QT_QPA_PLATFORM
    $env:QT_QPA_PLATFORM = "offscreen"
    try {
        $guiProcess = Start-Process -FilePath $packageExe -ArgumentList @("gui") -PassThru -WindowStyle Hidden -RedirectStandardOutput $guiStdout -RedirectStandardError $guiStderr
        Start-Sleep -Seconds 3
        if ($guiProcess.HasExited) {
            $details = if (Test-Path -LiteralPath $guiStderr) { [IO.File]::ReadAllText($guiStderr) } else { "" }
            throw "Packaged GUI exited during startup with code $($guiProcess.ExitCode). $details"
        }
        Stop-Process -Id $guiProcess.Id -Force
        Write-Output "PASS: packaged GUI stayed running for the startup check"
    }
    finally {
        $env:QT_QPA_PLATFORM = $oldQpa
    }
}
finally {
    Pop-Location
}
