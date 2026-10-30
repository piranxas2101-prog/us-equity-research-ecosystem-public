# Hibernate the computer after the nightly research run (owner's request 2026-10-13). Called by
# nightly_research.ps1 after it launches the run; switch: scanner_config.json -> power.hibernate_after_nightly.
# 1. Keep the computer awake while the run's process lives (a run started by a wake timer would otherwise be
#    put back to sleep after a few unattended minutes).
# 2. If the run failed or was interrupted, fallback_research.py runs the research-only fallback on the last good
#    shortlist and the held positions in its place (owner 2026-10-30) and keeps the computer awake meanwhile.
# 3. When it ends and the run reached a final state, hibernate_if_idle.ps1 decides: switch on, mains power and nobody
#    using keyboard or mouse for power.hibernate_idle_minutes, otherwise it stays on and says why in the log.
param(
    [Parameter(Mandatory = $true)] [string]$PointerPath,
    [Parameter(Mandatory = $true)] [string]$LogPath,
    # The run started by this launch: its pointer must show a start at or after this moment. The background run
    # writes the pointer a moment after the launcher returns; reading it at once watched the previous run's
    # finished process (2026-10-14).
    [datetime]$LaunchedAfter = [datetime]::MinValue,
    [switch]$DryRun  # decide and log, but do not hibernate (testing)
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Log([string]$Text) {
    "$((Get-Date).ToString('s')) [hibernate] $Text" | Add-Content -LiteralPath $LogPath -Encoding UTF8
}

# stderr of a native program must not end the script under Windows PowerShell 5.1 (tests/test_powershell_native_calls.py)
function Invoke-Logged([scriptblock]$Block) {
    $ErrorActionPreference = "Continue"
    & $Block *>&1 | ForEach-Object { "$_" } | Add-Content -LiteralPath $LogPath -Encoding UTF8
    return $LASTEXITCODE
}

Add-Type -Namespace Ecosystem -Name Power -MemberDefinition @"
[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);
"@

# The switch power.hibernate_after_nightly is read by hibernate_if_idle.ps1: with it off the run is still waited for,
# so a failed run still gets its fallback research.
$pointer = $null
$deadline = (Get-Date).AddMinutes(10)
while ($true) {
    try { $pointer = Get-Content -LiteralPath $PointerPath -Raw | ConvertFrom-Json } catch { $pointer = $null }
    $started = $null
    if ($null -ne $pointer) { try { $started = [datetime]::Parse([string]$pointer.started_at) } catch { } }
    if ($null -ne $started -and $started -ge $LaunchedAfter.AddSeconds(-5)) { break }
    if ((Get-Date) -gt $deadline) {
        Write-Log "No run started after $($LaunchedAfter.ToString('s')) appeared in the pointer within 10 minutes; staying on."
        exit 0
    }
    Start-Sleep -Seconds 5
}
$runPid = [int]$pointer.pid
$ES_CONTINUOUS = [uint32]2147483648
$ES_SYSTEM_REQUIRED = [uint32]1
[void][Ecosystem.Power]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED)
Write-Log "Keeping the computer awake while run $($pointer.run_id) (PID $runPid) is active."
try { Wait-Process -Id $runPid -ErrorAction Stop } catch { }
[void][Ecosystem.Power]::SetThreadExecutionState($ES_CONTINUOUS)

if (-not $DryRun) { Start-Sleep -Seconds 90 }  # let the end-of-run notification and report finish
# a run whose process ended without writing a final state (killed, crashed) is marked "interrupted" here
. (Join-Path $projectRoot "run_pointer.ps1")
try { [void](Set-RunInterrupted -PointerPath $PointerPath -TimingsPath (Join-Path $projectRoot "output\pipeline_step_timings_latest.json")) } catch { }
$final =Get-Content -LiteralPath $PointerPath -Raw | ConvertFrom-Json
if ($final.status -notin @("completed", "completed_review_required", "failed", "interrupted")) {
    Write-Log "Run $($final.run_id) is '$($final.status)', not final; staying on."
    exit 0
}
if ($final.status -in @("failed", "interrupted")) {
    Write-Log "Run $($final.run_id) is '$($final.status)': fallback research on the shortlist and the held positions..."
    $python = Join-Path $projectRoot ".venv-next\Scripts\python.exe"
    $fallbackScript = Join-Path $projectRoot "fallback_research.py"
    if ($DryRun) { Write-Log "Dry run: fallback research not started." }
    else {
        $fallbackExit = Invoke-Logged { & $python -W ignore $fallbackScript "ensure" "--trigger" "nightly" "--wait" }
        Write-Log "Fallback research step ended with exit code $fallbackExit."
    }
}
& (Join-Path $projectRoot "hibernate_if_idle.ps1") -LogPath $LogPath -Switch "hibernate_after_nightly" `
    -Reason "Run $($final.run_id) finished ($($final.status))" -DryRun:$DryRun
