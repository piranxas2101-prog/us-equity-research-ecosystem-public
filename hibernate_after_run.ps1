# Hibernate the computer after the nightly research run (owner's request 2026-10-13). Called by
# nightly_research.ps1 after it launches the run; switch: scanner_config.json -> power.hibernate_after_nightly.
# 1. Keep the computer awake while the run's process lives (a run started by a wake timer would otherwise be
#    put back to sleep after a few unattended minutes).
# 2. When it ends and the run reached a final state, hibernate_if_idle.ps1 decides: mains power and nobody
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

Add-Type -Namespace Ecosystem -Name Power -MemberDefinition @"
[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);
"@

$config = Get-Content -LiteralPath (Join-Path $projectRoot "scanner_config.json") -Raw | ConvertFrom-Json
if ($null -eq $config.power -or -not $config.power.hibernate_after_nightly) {
    Write-Log "Switch power.hibernate_after_nightly is off; nothing to do."
    exit 0
}

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
$final = Get-Content -LiteralPath $PointerPath -Raw | ConvertFrom-Json
if ($final.status -notin @("completed", "completed_review_required", "failed", "interrupted")) {
    Write-Log "Run $($final.run_id) is '$($final.status)', not final; staying on."
    exit 0
}
& (Join-Path $projectRoot "hibernate_if_idle.ps1") -LogPath $LogPath -Switch "hibernate_after_nightly" `
    -Reason "Run $($final.run_id) finished ($($final.status))" -DryRun:$DryRun
