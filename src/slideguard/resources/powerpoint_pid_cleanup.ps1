param(
    [Parameter(Mandatory = $true)]
    [int]$ProcessId,
    [Parameter(Mandatory = $true)]
    [int]$ExpectedParentPid,
    [Parameter(Mandatory = $true)]
    [string]$ExpectedStartTimeUtc,
    [Parameter(Mandatory = $true)]
    [ValidateSet("application-window", "unique-automation-activation")]
    [string]$IdentityMethod
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$result = [ordered]@{
    status = "not-found"
    processId = $ProcessId
    verified = $false
    stopped = $false
}

try {
    # Query and stop one exact PID. Name-based termination and enumerate-then-
    # stop patterns are deliberately absent.
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -ne $process) {
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        $actualStart = $process.StartTime.ToUniversalTime().ToString("o")
        $sameName = [string]::Equals($process.ProcessName, "POWERPNT", [StringComparison]::OrdinalIgnoreCase)
        $sameParent = ([int]$cim.ParentProcessId -eq $ExpectedParentPid)
        $sameStart = [string]::Equals($actualStart, $ExpectedStartTimeUtc, [StringComparison]::Ordinal)
        $automationCommand = (
            [string]$cim.CommandLine -match '(?i)(^|\s)/AUTOMATION(\s|$)' -and
            [string]$cim.CommandLine -match '(?i)(^|\s)-Embedding(\s|$)'
        )
        $sameIdentity = (
            ($IdentityMethod -eq "application-window" -and $sameParent) -or
            ($IdentityMethod -eq "unique-automation-activation" -and $automationCommand)
        )
        if ($sameName -and $sameStart -and $sameIdentity) {
            $result.verified = $true
            Stop-Process -Id $ProcessId -Force -ErrorAction Stop
            $result.stopped = $true
            $result.status = "stopped-proven-owned-process"
        }
        else {
            $result.status = "not-stopped-proof-mismatch"
            $result.proof = [ordered]@{
                processNameMatches = $sameName
                parentMatches = $sameParent
                startTimeMatches = $sameStart
                automationCommandLine = $automationCommand
                identityMethod = $IdentityMethod
            }
        }
    }
}
catch {
    $result.status = "cleanup-check-failed"
    $result.reason = $_.Exception.GetType().Name
}

$result | ConvertTo-Json -Depth 5 -Compress
if (-not $result.stopped -and $result.status -ne "not-found") { exit 2 }
