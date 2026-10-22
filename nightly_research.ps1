# Nightly research run, started by the Windows scheduled task "Ecosystem Nightly Research".
# 1. If a full research run is still active, do nothing tonight.
# 2. Run the refresh gate (reads the SEC filing calendar, compares stored evidence with
#    fresh answers, may move a store between populate and reuse). It never blocks.
# 3. Start the research-only full run (no traders, no orders), which notifies on end.
# 4. Keep the computer awake while the run lives, then hibernate it when idle (hibernate_after_run.ps1).
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$logDir = Join-Path $projectRoot "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$log = Join-Path $logDir ("nightly_research_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
$python = Join-Path $projectRoot ".venv-next\Scripts\python.exe"

function Write-Log([string]$Text) {
    "$((Get-Date).ToString('s')) $Text" | Add-Content -LiteralPath $log -Encoding UTF8
}

# Windows PowerShell 5.1 turns every line a native program writes to stderr into an error record, and with
# $ErrorActionPreference = "Stop" the first one would end the step (it ended the afternoon session on
# 2026-10-13). Redirected calls therefore run with "Continue" inside this function; the exit code decides.
# tests/test_powershell_native_calls.py keeps every redirected call inside it.
function Invoke-Logged([scriptblock]$Block) {
    $ErrorActionPreference = "Continue"
    & $Block *>&1 | ForEach-Object { "$_" } | Add-Content -LiteralPath $log -Encoding UTF8
    return $LASTEXITCODE
}

. (Join-Path $projectRoot "run_pointer.ps1")
$pointer = Join-Path $projectRoot "output\full_research_control_plane_background_latest.json"
try {
    # A run whose process is gone (computer shut down or restarted, or killed) is marked "interrupted" with the
    # step it was in, so the dashboard and the report show it. Tonight's run then starts normally and Babis
    # resumes from its own checkpoint when the universe is unchanged.
    $interrupted = Set-RunInterrupted -PointerPath $pointer -TimingsPath (Join-Path $projectRoot "output\pipeline_step_timings_latest.json")
    if ($null -ne $interrupted) {
        Write-Log "Previous run $($interrupted.run_id) was INTERRUPTED at step [$($interrupted.last_step)]: its process is gone. Marked as interrupted."
        [void](Invoke-Logged { & $python "run_performance_log.py" "record" "--run-id" $interrupted.run_id })
    }
    $existing = Get-RunPointer -Path $pointer
    if ($null -ne $existing -and $existing.status -notin $script:TerminalRunStates -and (Test-RunProcessAlive -Pointer $existing)) {
        Write-Log "Run $($existing.run_id) still active (PID $($existing.pid)); nothing started tonight."
        exit 0
    }
} catch {
    Write-Log "Could not check the run pointer: $($_.Exception.Message)"
}

# A run started by an older launcher may have finished without its daily report.
try {
    $previous = Get-Content -LiteralPath $pointer -Raw | ConvertFrom-Json
    if ($previous.run_id -and -not (Test-Path -LiteralPath (Join-Path $projectRoot "output\daily_reports\$($previous.run_id).md"))) {
        Write-Log "Writing the missing daily report of $($previous.run_id)..."
        [void](Invoke-Logged { & $python "daily_agent_report.py" "--run-id" $previous.run_id })
    }
} catch {
    Write-Log "Previous run report skipped: $($_.Exception.Message)"
}

# Learning reports due tonight (week on Saturday; month, quarter and year on their first day). Never blocks.
$reportExit = Invoke-Logged { & $python -W ignore "paper_learning.py" "report" "--auto" }
if ($reportExit -ne 0) { Write-Log "Learning reports ended with exit code $reportExit (run continues)." }

Write-Log "Refresh gate..."
try {
    $gateExit = Invoke-Logged { & $python "refresh_gate.py" }
    if ($gateExit -ne 0) { Write-Log "Refresh gate ended with exit code $gateExit (run continues)." }
} catch {
    Write-Log "Refresh gate failed (run continues): $($_.Exception.Message)"
}

Write-Log "Starting the research-only full run..."
$launchedAt = Get-Date
$launchExit = Invoke-Logged { & ".\start_full_research_with_notify.ps1" }
Write-Log "Launcher finished with exit code $launchExit."

# 4. Keep the computer awake during the run, then hibernate it if nobody is using it (power.hibernate_after_nightly).
if ($launchExit -eq 0) {
    try {
        & ".\hibernate_after_run.ps1" -PointerPath $pointer -LogPath $log -LaunchedAfter $launchedAt
    } catch {
        Write-Log "Hibernate step failed (computer stays on): $($_.Exception.Message)"
    }
}
