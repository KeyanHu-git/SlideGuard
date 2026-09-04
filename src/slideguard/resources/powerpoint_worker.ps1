param(
    [Parameter(Mandatory = $true)]
    [string]$JobJson
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$job = Get-Content -LiteralPath $JobJson -Raw | ConvertFrom-Json
$resultPath = [IO.Path]::GetFullPath([string]$job.resultPath)
$result = [ordered]@{
    ok = $false
    mode = [string]$job.mode
    powerpoint = $null
    export = $null
    error = $null
}

$ppt = $null
$presentation = $null
$slideObject = $null
$printRange = $null
$comScratch = $null
$mutex = $null
$mutexHeld = $false
$ownsPowerPoint = $false
$powerPointPid = 0
$previousAutomationSecurity = $null
try {
    # Serialize every SlideGuard PowerPoint worker in the current Windows
    # session. This prevents preview and export jobs from racing over COM.
    $mutex = New-Object System.Threading.Mutex($false, "Local\SlideGuard-PowerPoint-Worker-v1")
    try {
        $mutexHeld = $mutex.WaitOne([TimeSpan]::FromMinutes(10))
    }
    catch [System.Threading.AbandonedMutexException] {
        $mutexHeld = $true
    }
    if (-not $mutexHeld) { throw "Timed out waiting for the SlideGuard PowerPoint worker lock" }
    $existingPowerPointPids = @(Get-Process -Name "POWERPNT" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
    $ppt = New-Object -ComObject PowerPoint.Application
    $newPowerPointPids = @()
    for ($attempt = 0; $attempt -lt 30 -and $newPowerPointPids.Count -eq 0; $attempt++) {
        $newPowerPointPids = @(
            Get-Process -Name "POWERPNT" -ErrorAction SilentlyContinue |
                Where-Object { $existingPowerPointPids -notcontains $_.Id } |
                Select-Object -ExpandProperty Id
        )
        if ($newPowerPointPids.Count -eq 0) { Start-Sleep -Milliseconds 100 }
    }
    if ($newPowerPointPids.Count -gt 1) {
        throw "PowerPoint worker ownership is ambiguous; no presentation was opened"
    }
    if ($newPowerPointPids.Count -eq 1) {
        $powerPointPid = [int]$newPowerPointPids[0]
        $ownsPowerPoint = $true
    }
    if ($ownsPowerPoint) { $ppt.Visible = -1 }
    $result.powerpoint = [ordered]@{
        version = [string]$ppt.Version
        build = [string]$ppt.Build
        productCode = [string]$ppt.ProductCode
        path = [string]$ppt.Path
        processId = $powerPointPid
        isolatedWorker = $ownsPowerPoint
        sessionMode = $(if ($ownsPowerPoint) { "isolated" } else { "reused-existing-safe" })
    }

    if ([string]$job.mode -eq "probe") {
        $result.ok = $true
    }
    elseif ([string]$job.mode -eq "export" -or [string]$job.mode -eq "preview") {
        $pptxPath = (Resolve-Path -LiteralPath ([string]$job.pptxPath)).Path
        $slide = [int]$job.slide
        $referenceWidth = [int]$job.referenceWidth
        $referencePng = [IO.Path]::GetFullPath([string]$job.referencePng)
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($referencePng)) | Out-Null
        $nativePdf = $null
        if ([string]$job.mode -eq "export") {
            $nativePdf = [IO.Path]::GetFullPath([string]$job.nativePdf)
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($nativePdf)) | Out-Null
        }

        # Slide.Export has a legacy path parser and can reject otherwise valid
        # long paths. Export through a very short ASCII path and copy afterward.
        $comScratch = Join-Path $env:TEMP ("sg-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
        [IO.Directory]::CreateDirectory($comScratch) | Out-Null
        $comPdf = Join-Path $comScratch "n.pdf"
        $comPng = Join-Path $comScratch "r.png"

        # msoAutomationSecurityForceDisable.  The source is read-only and external
        # links are never refreshed by this worker.
        $previousAutomationSecurity = $ppt.AutomationSecurity
        $ppt.AutomationSecurity = 3
        $presentation = $ppt.Presentations.Open($pptxPath, $true, $false, $false)
        if ($slide -lt 1 -or $slide -gt $presentation.Slides.Count) {
            throw "Slide $slide is outside 1..$($presentation.Slides.Count)"
        }
        $slideObject = $presentation.Slides.Item($slide)
        $referenceHeight = [Math]::Round(
            $referenceWidth * $presentation.PageSetup.SlideHeight / $presentation.PageSetup.SlideWidth
        )
        $slideObject.Export($comPng, "PNG", $referenceWidth, $referenceHeight)
        if ([string]$job.mode -eq "export") {
            $printRange = $presentation.PrintOptions.Ranges.Add($slide, $slide)
            $presentation.ExportAsFixedFormat(
                # PpPrintRangeType.ppPrintSlideRange = 4.  Value 3 means
                # ppPrintCurrent and silently repeats PowerPoint's active slide.
                $comPdf, 2, 2, 0, 1, 1, 0, $printRange, 4, "",
                $true, $true, $true, $true, $false
            )
            if (-not (Test-Path -LiteralPath $comPdf)) {
                throw "PowerPoint did not create the PDF reference"
            }
            Copy-Item -LiteralPath $comPdf -Destination $nativePdf -Force
        }
        if (-not (Test-Path -LiteralPath $comPng)) {
            throw "PowerPoint did not create the PNG reference"
        }
        Copy-Item -LiteralPath $comPng -Destination $referencePng -Force
        $result.export = [ordered]@{
            slide = $slide
            slideCount = [int]$presentation.Slides.Count
            slideWidthPt = [double]$presentation.PageSetup.SlideWidth
            slideHeightPt = [double]$presentation.PageSetup.SlideHeight
            referenceWidth = $referenceWidth
            referenceHeight = $referenceHeight
            nativePdf = $nativePdf
            referencePng = $referencePng
        }
        $result.ok = $true
    }
    else {
        throw "Unknown worker mode: $($job.mode)"
    }
}
catch {
    $result.error = [ordered]@{
        message = $_.Exception.Message
        type = $_.Exception.GetType().FullName
        scriptStackTrace = $_.ScriptStackTrace
    }
}
finally {
    if ($presentation) { $presentation.Close() }
    if ($ppt -and -not $ownsPowerPoint -and $null -ne $previousAutomationSecurity) {
        try { $ppt.AutomationSecurity = $previousAutomationSecurity } catch { }
    }
    if ($ppt -and $ownsPowerPoint) { $ppt.Quit() }
    foreach ($object in @($printRange, $slideObject, $presentation, $ppt)) {
        if ($object) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($object) }
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    if ($comScratch -and (Test-Path -LiteralPath $comScratch)) {
        $resolvedScratch = [IO.Path]::GetFullPath($comScratch)
        $resolvedTemp = [IO.Path]::GetFullPath($env:TEMP)
        if ($resolvedScratch.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase) -and
            ([IO.Path]::GetFileName($resolvedScratch) -like "sg-*")) {
            Remove-Item -LiteralPath $resolvedScratch -Recurse -Force
        }
    }
    if ($mutexHeld -and $mutex) {
        try { $mutex.ReleaseMutex() } catch { }
    }
    if ($mutex) { $mutex.Dispose() }
    $resultDir = [IO.Path]::GetDirectoryName($resultPath)
    if ($resultDir) { [IO.Directory]::CreateDirectory($resultDir) | Out-Null }
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $resultPath -Encoding UTF8
}

if (-not $result.ok) { exit 40 }
