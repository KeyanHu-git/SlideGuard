[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PackageRoot,
    [string]$Fixture = "",
    [switch]$CoreOnly,
    [switch]$KeepOutput
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath($PackageRoot)
$exe = Join-Path $root "SlideGuard.exe"
$sums = Join-Path $root "SHA256SUMS"
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "SlideGuard.exe is missing: $exe" }
if (-not (Test-Path -LiteralPath $sums -PathType Leaf)) { throw "SHA256SUMS is missing: $sums" }

$listed = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($line in [IO.File]::ReadAllLines($sums)) {
    if ($line -notmatch '^([0-9a-f]{64}) \*(.+)$') { throw "Invalid SHA256SUMS row: $line" }
    $expected = $Matches[1]
    $listedRelative = $Matches[2]
    if (-not $listed.Add($listedRelative)) { throw "Duplicate SHA256SUMS row: $listedRelative" }
    $relative = $listedRelative.Replace('/', [IO.Path]::DirectorySeparatorChar)
    $target = [IO.Path]::GetFullPath((Join-Path $root $relative))
    if (-not $target.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Checksum path leaves the package: $relative"
    }
    if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "Package file is missing: $relative" }
    $actual = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw "Checksum mismatch: $relative" }
}
foreach ($file in Get-ChildItem -LiteralPath $root -File -Recurse) {
    $relative = [IO.Path]::GetRelativePath($root, $file.FullName).Replace([IO.Path]::DirectorySeparatorChar, '/')
    if ($relative -ne "SHA256SUMS" -and -not $listed.Contains($relative)) {
        throw "Package contains an unlisted file: $relative"
    }
}

$env:PIP_NO_INDEX = "1"
$version = & $exe --version
if ($LASTEXITCODE -ne 0 -or $version -notmatch '^SlideGuard ') { throw "Version command failed." }

$missingRequest = Join-Path $root "__missing-request__.json"
$machineResult = & $exe job $missingRequest 2>$null
if ($LASTEXITCODE -ne 30) { throw "Machine error exit code changed: $LASTEXITCODE" }
$parsed = $machineResult | ConvertFrom-Json
if ($parsed.status -ne "failed" -or $parsed.error.code -ne "INPUT_INVALID") {
    throw "Machine error document changed."
}

if ($CoreOnly) {
    Write-Output "PASS: checksums, CLI startup and stable machine failure"
    exit 0
}

$doctorText = & $exe doctor --json
if ($LASTEXITCODE -ne 0) { throw "Doctor failed. Install licensed PowerPoint and provide Poppler before the full smoke test.`n$doctorText" }

if (-not $Fixture) { throw "The full smoke test needs -Fixture with a local PPTX difficulty sample." }
$fixturePath = [IO.Path]::GetFullPath($Fixture)
if (-not (Test-Path -LiteralPath $fixturePath -PathType Leaf)) { throw "Fixture is missing: $fixturePath" }

$smokeRoot = Join-Path ([IO.Path]::GetTempPath()) ("SlideGuard-Portable-Smoke-" + [Guid]::NewGuid().ToString("N"))
[IO.Directory]::CreateDirectory($smokeRoot) | Out-Null
try {
    & $exe export $fixturePath --slides 1 --out $smokeRoot
    if ($LASTEXITCODE -ne 0) { throw "Fixture export failed with exit code $LASTEXITCODE." }
    $manifest = Get-ChildItem -LiteralPath $smokeRoot -Filter manifest.json -File -Recurse | Select-Object -First 1
    if (-not $manifest) { throw "Fixture export did not create manifest.json." }
    & $exe verify $manifest.FullName
    if ($LASTEXITCODE -ne 0) { throw "Exported package verification failed." }
    Write-Output "PASS: checksums, CLI, doctor, fixture export and package verification"
}
finally {
    if (-not $KeepOutput -and (Test-Path -LiteralPath $smokeRoot)) {
        [IO.Directory]::Delete($smokeRoot, $true)
    }
}
