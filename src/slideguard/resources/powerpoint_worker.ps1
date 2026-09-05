param(
    [Parameter(Mandatory = $true)]
    [string]$JobJson
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$job = Get-Content -LiteralPath $JobJson -Raw | ConvertFrom-Json
$resultPath = [IO.Path]::GetFullPath([string]$job.resultPath)
$statusPath = [IO.Path]::GetFullPath([string]$job.statusPath)
$cancelPath = [IO.Path]::GetFullPath([string]$job.cancelPath)
$nonce = [string]$job.nonce
$result = [ordered]@{
    nonce = $nonce
    ok = $false
    mode = [string]$job.mode
    powerpoint = $null
    export = $null
    error = $null
}

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class SlideGuardWindowProcess {
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
}
"@

$workerState = [ordered]@{
    schemaVersion = "1.0"
    nonce = $nonce
    workerPid = [int]$PID
    phase = "starting"
    cancelObserved = $false
    cleanupComplete = $false
    cleanupErrors = @()
    powerpoint = $null
    comScratch = $null
}

function Write-WorkerState {
    try {
        $directory = [IO.Path]::GetDirectoryName($statusPath)
        if ($directory) { [IO.Directory]::CreateDirectory($directory) | Out-Null }
        $temporary = $statusPath + ".tmp-" + [Guid]::NewGuid().ToString("N")
        $workerState | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding UTF8
        Move-Item -LiteralPath $temporary -Destination $statusPath -Force
    }
    catch {
        # Status reporting must never replace the export's real result.
    }
}

function Test-SlideGuardCancellation {
    if (-not (Test-Path -LiteralPath $cancelPath)) { return $false }
    try {
        $request = Get-Content -LiteralPath $cancelPath -Raw | ConvertFrom-Json
        if ([string]$request.nonce -eq $nonce) {
            $workerState.cancelObserved = $true
            Write-WorkerState
            return $true
        }
    }
    catch { }
    return $false
}

function Assert-NotCancelled {
    if (Test-SlideGuardCancellation) {
        throw [OperationCanceledException]::new("SlideGuard PowerPoint operation was cancelled")
    }
}

Write-WorkerState

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
    $mutexDeadline = [DateTime]::UtcNow.AddMinutes(10)
    while (-not $mutexHeld -and [DateTime]::UtcNow -lt $mutexDeadline) {
        Assert-NotCancelled
        try {
            $mutexHeld = $mutex.WaitOne([TimeSpan]::FromMilliseconds(250))
        }
        catch [System.Threading.AbandonedMutexException] {
            $mutexHeld = $true
        }
    }
    if (-not $mutexHeld) { throw "Timed out waiting for the SlideGuard PowerPoint worker lock" }
    $workerState.phase = "activating-powerpoint"
    Write-WorkerState
    Assert-NotCancelled

    $existingPowerPointPids = @(Get-Process -Name "POWERPNT" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
    $activationStarted = [DateTime]::UtcNow
    $ppt = New-Object -ComObject PowerPoint.Application
    $windowPid = [uint32]0
    $windowProperty = $ppt.PSObject.Properties["HWND"]
    if ($null -ne $windowProperty -and [int64]$windowProperty.Value -ne 0) {
        $windowHandle = [IntPtr]::new([int64]$windowProperty.Value)
        [void][SlideGuardWindowProcess]::GetWindowThreadProcessId($windowHandle, [ref]$windowPid)
    }
    $activationCandidates = @()
    foreach ($candidate in @(Get-Process -Name "POWERPNT" -ErrorAction SilentlyContinue)) {
        if ($existingPowerPointPids -contains $candidate.Id) { continue }
        $candidateCim = Get-CimInstance Win32_Process -Filter "ProcessId = $($candidate.Id)" -ErrorAction SilentlyContinue
        $candidateStart = $candidate.StartTime.ToUniversalTime()
        $automationCommand = (
            $null -ne $candidateCim -and
            [string]$candidateCim.CommandLine -match '(?i)(^|\s)/AUTOMATION(\s|$)' -and
            [string]$candidateCim.CommandLine -match '(?i)(^|\s)-Embedding(\s|$)'
        )
        if (
            $null -ne $candidateCim -and
            $automationCommand -and
            $candidateStart -ge $activationStarted.AddSeconds(-1) -and
            $candidateStart -le [DateTime]::UtcNow.AddSeconds(1)
        ) {
            $activationCandidates += $candidate
        }
    }
    if ($windowPid -gt 0) {
        $powerPointPid = [int]$windowPid
        $identityMethod = "application-window"
    }
    elseif ($activationCandidates.Count -eq 1) {
        $powerPointPid = [int]$activationCandidates[0].Id
        $identityMethod = "unique-automation-activation"
    }
    else {
        $powerPointPid = 0
        $identityMethod = "unresolved"
    }
    $powerPointProcess = if ($powerPointPid -gt 0) { Get-Process -Id $powerPointPid -ErrorAction SilentlyContinue } else { $null }
    $powerPointCim = if ($powerPointPid -gt 0) {
        Get-CimInstance Win32_Process -Filter "ProcessId = $powerPointPid" -ErrorAction SilentlyContinue
    } else { $null }
    $powerPointStartTime = if ($powerPointProcess) {
        $powerPointProcess.StartTime.ToUniversalTime()
    } else { $null }
    $parentPid = if ($powerPointCim) { [int]$powerPointCim.ParentProcessId } else { 0 }
    $automationCommandLine = (
        $null -ne $powerPointCim -and
        [string]$powerPointCim.CommandLine -match '(?i)(^|\s)/AUTOMATION(\s|$)' -and
        [string]$powerPointCim.CommandLine -match '(?i)(^|\s)-Embedding(\s|$)'
    )
    $absentBeforeActivation = ($powerPointPid -gt 0 -and $existingPowerPointPids -notcontains $powerPointPid)
    $parentIsWorker = ($parentPid -eq [int]$PID)
    $startedDuringActivation = (
        $null -ne $powerPointStartTime -and
        $powerPointStartTime -ge $activationStarted.AddSeconds(-1) -and
        $powerPointStartTime -le [DateTime]::UtcNow.AddSeconds(1)
    )
    $windowPidMatches = ($windowPid -gt 0 -and $powerPointPid -eq [int]$windowPid)
    $identityProven = (
        ($identityMethod -eq "application-window" -and $windowPidMatches) -or
        (
            $identityMethod -eq "unique-automation-activation" -and
            $activationCandidates.Count -eq 1 -and
            $automationCommandLine
        )
    )
    $ownsPowerPoint = ($absentBeforeActivation -and $startedDuringActivation -and $identityProven)
    $ownership = if ($ownsPowerPoint) { "slideguard-owned" } else { "reused-or-unproven" }
    $sessionMode = if ($ownsPowerPoint) {
        "isolated"
    } elseif (
        $existingPowerPointPids -contains $powerPointPid -or
        ($identityMethod -eq "unresolved" -and $existingPowerPointPids.Count -gt 0 -and $activationCandidates.Count -eq 0)
    ) {
        "reused-existing-safe"
    } else {
        "unproven-do-not-terminate"
    }
    $workerState.powerpoint = [ordered]@{
        pid = $powerPointPid
        parentPid = $parentPid
        startTimeUtc = $(if ($powerPointStartTime) { $powerPointStartTime.ToString("o") } else { $null })
        ownership = $ownership
        sessionMode = $sessionMode
        proof = [ordered]@{
            absentBeforeActivation = $absentBeforeActivation
            parentIsWorker = $parentIsWorker
            startedDuringActivation = $startedDuringActivation
            windowPidMatches = $windowPidMatches
            identityMethod = $identityMethod
            automationCommandLine = $automationCommandLine
            uniqueActivationCandidate = ($activationCandidates.Count -eq 1)
        }
    }
    $workerState.phase = "powerpoint-ready"
    Write-WorkerState
    Assert-NotCancelled

    if ($ownsPowerPoint) { $ppt.Visible = -1 }
    $result.powerpoint = [ordered]@{
        version = [string]$ppt.Version
        build = [string]$ppt.Build
        productCode = [string]$ppt.ProductCode
        path = [string]$ppt.Path
        processId = $powerPointPid
        isolatedWorker = $ownsPowerPoint
        sessionMode = $sessionMode
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
        $workerState.comScratch = $comScratch
        $workerState.phase = "opening-presentation"
        Write-WorkerState
        Assert-NotCancelled
        $comPdf = Join-Path $comScratch "n.pdf"
        $comPng = Join-Path $comScratch "r.png"

        # msoAutomationSecurityForceDisable.  The source is read-only and external
        # links are never refreshed by this worker.
        $previousAutomationSecurity = $ppt.AutomationSecurity
        $ppt.AutomationSecurity = 3
        $presentation = $ppt.Presentations.Open($pptxPath, $true, $false, $false)
        $workerState.phase = "presentation-open"
        Write-WorkerState
        Assert-NotCancelled
        if ($slide -lt 1 -or $slide -gt $presentation.Slides.Count) {
            throw "Slide $slide is outside 1..$($presentation.Slides.Count)"
        }
        $slideObject = $presentation.Slides.Item($slide)
        $referenceHeight = [Math]::Round(
            $referenceWidth * $presentation.PageSetup.SlideHeight / $presentation.PageSetup.SlideWidth
        )
        $workerState.phase = "exporting-png"
        Write-WorkerState
        $slideObject.Export($comPng, "PNG", $referenceWidth, $referenceHeight)
        Assert-NotCancelled
        if ([string]$job.mode -eq "export") {
            $workerState.phase = "exporting-pdf"
            Write-WorkerState
            $printRange = $presentation.PrintOptions.Ranges.Add($slide, $slide)
            $presentation.ExportAsFixedFormat(
                # PpPrintRangeType.ppPrintSlideRange = 4.  Value 3 means
                # ppPrintCurrent and silently repeats PowerPoint's active slide.
                $comPdf, 2, 2, 0, 1, 1, 0, $printRange, 4, "",
                $true, $true, $true, $true, $false
            )
            Assert-NotCancelled
            if (-not (Test-Path -LiteralPath $comPdf)) {
                throw "PowerPoint did not create the PDF reference"
            }
            Copy-Item -LiteralPath $comPdf -Destination $nativePdf -Force
        }
        if (-not (Test-Path -LiteralPath $comPng)) {
            throw "PowerPoint did not create the PNG reference"
        }
        Copy-Item -LiteralPath $comPng -Destination $referencePng -Force
        Assert-NotCancelled
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
    $cleanupErrors = @()
    if ($presentation) {
        try { $presentation.Close() } catch { $cleanupErrors += "presentation-close:$($_.Exception.GetType().Name)" }
    }
    if ($ppt -and -not $ownsPowerPoint -and $null -ne $previousAutomationSecurity) {
        try { $ppt.AutomationSecurity = $previousAutomationSecurity } catch { $cleanupErrors += "automation-security-restore:$($_.Exception.GetType().Name)" }
    }
    if ($ppt -and $ownsPowerPoint) {
        try { $ppt.Quit() } catch { $cleanupErrors += "powerpoint-quit:$($_.Exception.GetType().Name)" }
    }
    foreach ($object in @($printRange, $slideObject, $presentation, $ppt)) {
        if ($object) {
            try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($object) } catch { $cleanupErrors += "com-release:$($_.Exception.GetType().Name)" }
        }
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    if ($comScratch -and (Test-Path -LiteralPath $comScratch)) {
        $resolvedScratch = [IO.Path]::GetFullPath($comScratch)
        $resolvedTemp = [IO.Path]::GetFullPath($env:TEMP)
        if ($resolvedScratch.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase) -and
            ([IO.Path]::GetFileName($resolvedScratch) -like "sg-*")) {
            try { Remove-Item -LiteralPath $resolvedScratch -Recurse -Force } catch { $cleanupErrors += "scratch-remove:$($_.Exception.GetType().Name)" }
        }
    }
    if ($mutexHeld -and $mutex) {
        try { $mutex.ReleaseMutex() } catch { $cleanupErrors += "mutex-release:$($_.Exception.GetType().Name)" }
    }
    if ($mutex) {
        try { $mutex.Dispose() } catch { $cleanupErrors += "mutex-dispose:$($_.Exception.GetType().Name)" }
    }
    $workerState.phase = "finished"
    $workerState.cleanupErrors = $cleanupErrors
    $workerState.cleanupComplete = ($cleanupErrors.Count -eq 0)
    Write-WorkerState
    if ($result.ok -and $cleanupErrors.Count -gt 0) {
        $result.ok = $false
        $result.error = [ordered]@{
            message = "PowerPoint export completed, but worker cleanup was incomplete"
            type = "SlideGuard.PowerPointCleanupIncomplete"
            cleanupErrors = $cleanupErrors
        }
    }
    $resultDir = [IO.Path]::GetDirectoryName($resultPath)
    if ($resultDir) { [IO.Directory]::CreateDirectory($resultDir) | Out-Null }
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $resultPath -Encoding UTF8
}

if (-not $result.ok) { exit 40 }
