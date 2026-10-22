# Helpers for the run pointer (output/full_research_control_plane_background_latest.json).
# Dot-source this file; it only defines functions and never starts a run.
#
# A run whose process has disappeared (computer shut down or restarted, the run was killed) leaves the
# pointer in "running_pipeline" for ever. Set-RunInterrupted marks it "interrupted" with the step it was in,
# so the dashboard, the daily report and the next nightly start can tell what happened.

$script:TerminalRunStates = @("completed", "completed_review_required", "failed", "stopped_by_owner", "interrupted")

function Get-RunPointer {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json) } catch { return $null }
}

function Test-RunProcessAlive {
    # The pointer holds the PID of the run's PowerShell process. A PID can be reused by an unrelated program
    # after a restart, so the process must also be a PowerShell that started no later than the run began.
    param($Pointer)
    if ($null -eq $Pointer -or $null -eq $Pointer.pid) { return $false }
    try {
        $process = Get-Process -Id ([int]$Pointer.pid) -ErrorAction Stop
        if ($process.ProcessName -notlike "powershell*") { return $false }
        $startedAt = [datetime]::Parse([string]$Pointer.started_at)
        return ($process.StartTime -le $startedAt.AddMinutes(1))
    } catch {
        return $false
    }
}

function Set-RunInterrupted {
    # Returns the updated pointer when it marked an interrupted run, otherwise $null.
    param([string]$PointerPath, [string]$TimingsPath)
    $pointer = Get-RunPointer -Path $PointerPath
    if ($null -eq $pointer -or $pointer.status -in $script:TerminalRunStates) { return $null }
    if (Test-RunProcessAlive -Pointer $pointer) { return $null }
    $lastStep = $null
    try {
        $timings = Get-Content -LiteralPath $TimingsPath -Raw | ConvertFrom-Json
        if ($timings.run_id -eq $pointer.run_id) { $lastStep = $timings.current_step }
    } catch { }
    $pointer.status = "interrupted"
    $pointer | Add-Member -NotePropertyName finished_at -NotePropertyValue (Get-Date -Format o) -Force
    $pointer | Add-Member -NotePropertyName last_step -NotePropertyValue $lastStep -Force
    $pointer | Add-Member -NotePropertyName error -NotePropertyValue "The run process is gone (computer shut down or restarted, or the run was killed). finished_at is when this was detected." -Force
    $pointer | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $PointerPath -Encoding UTF8
    return $pointer
}
