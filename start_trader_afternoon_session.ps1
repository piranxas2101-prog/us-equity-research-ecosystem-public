# Afternoon paper session (owner's decision 2026-10-13), started by the scheduled task "Ecosystem Preopen Check"
# Mon-Fri at 16:00 Athens. The nightly research run stays research-only; this is the separate paper workflow.
# 1. Pre-open check (09:00 New York): pre-market gap and new 8-Ks, candidate -> watch only (preopen_check.py).
# 2. Paper orders only if today's nightly run completed, the traders' files come from it and no session started
#    today: at 09:35 New York, start_trader_paper_trading_today.ps1 on the checked files (preflight, Koulis,
#    Thymios, Alpaca market clock, traders, Miltos 15Min confirmation, position monitors, same-day flat).
#    Limits are the launcher's defaults; paper account only.
# 3. After the close (16:10 New York): paper_session_report.py and a notification with the day's result.
# 4. Rest (owner's decision 2026-10-14): the computer is kept awake only while there is work. With nothing to
#    trade, after the day's report, or after an error (always with a notification), hibernate_if_idle.ps1
#    hibernates it if nobody is using it (switch power.hibernate_after_afternoon); the 01:30 nightly wakes it.
param(
    [switch]$DryRun  # testing: the rest step decides and logs but does not hibernate
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$logDir = Join-Path $projectRoot "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$log = Join-Path $logDir ("afternoon_session_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
$python = Join-Path $projectRoot ".venv-next\Scripts\python.exe"
# processes that must finish before the computer may rest (entry stalker, position monitors, exit manager)
$sessionWorkers = "miltos_entry_stalker|alpaca_paper_position_monitor|alpaca_paper_exit_manager"

function Write-Log([string]$Text) {
    "$((Get-Date).ToString('s')) $Text" | Add-Content -LiteralPath $log -Encoding UTF8
}

# Windows PowerShell 5.1 turns every line a native program writes to stderr into an error record, and with
# $ErrorActionPreference = "Stop" the first one (a yfinance warning, say) ends this whole script (it did on
# 2026-10-13). Native calls therefore run with "Continue" here; their exit code decides, and their stderr goes
# to the log as text. tests/test_powershell_native_calls.py keeps every redirected call inside this function.
function Invoke-Logged([scriptblock]$Block) {
    $ErrorActionPreference = "Continue"
    & $Block *>&1 | ForEach-Object { "$_" } | Add-Content -LiteralPath $log -Encoding UTF8
    return $LASTEXITCODE
}

# On 2026-10-19 every program started after the close failed with 0xC0000142 (STATUS_DLL_INIT_FAILED: Windows
# could not start it from this hidden console) and ran fine when started again later. The after-close steps are
# therefore retried on that code only: up to 3 attempts, 60 seconds apart. Any other exit code is final.
$startFailure = -1073741502
function Invoke-Retried([string]$Name, [scriptblock]$Block) {
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $code = Invoke-Logged $Block
        if ($code -ne $startFailure) { return $code }
        Write-Log "$Name could not start (0xC0000142), attempt $attempt of 3."
        if ($attempt -lt 3) { Start-Sleep -Seconds 60 }
    }
    return $code
}

function Show-Notice([string]$Title, [string]$Text) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $n = New-Object System.Windows.Forms.NotifyIcon
        $n.Icon = [System.Drawing.SystemIcons]::Information
        $n.Visible = $true
        $n.BalloonTipTitle = $Title
        $n.BalloonTipText = $Text
        $n.ShowBalloonTip(30000)
        Start-Sleep -Seconds 31
        $n.Dispose()
    } catch { }
}

Add-Type -Namespace Ecosystem -Name Awake -MemberDefinition @"
[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);
"@

function Invoke-Rest([string]$Reason, [string]$WaitFor = "") {
    [void][Ecosystem.Awake]::SetThreadExecutionState([uint32]2147483648)  # ES_CONTINUOUS: release the awake hold
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        try {
            & (Join-Path $projectRoot "hibernate_if_idle.ps1") -LogPath $log -Switch "hibernate_after_afternoon" `
                -Reason $Reason -WaitForPattern $WaitFor -DryRun:$DryRun
            return
        } catch {
            Write-Log "Rest step failed (attempt $attempt of 2): $($_.Exception.Message)"
            if ($attempt -lt 2) { Start-Sleep -Seconds 60 }
        }
    }
    Write-Log "Rest step gave up; the computer stays on."
}

$eastern = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
function Get-Eastern { [System.TimeZoneInfo]::ConvertTime((Get-Date), $eastern) }
function Wait-Eastern([int]$Hour, [int]$Minute) {
    $now = Get-Eastern
    $target = $now.Date.AddHours($Hour).AddMinutes($Minute)
    if ($now -lt $target) { Start-Sleep -Seconds ([int][Math]::Ceiling(($target - $now).TotalSeconds)) }
}

if ((Get-Eastern).DayOfWeek -in @([DayOfWeek]::Saturday, [DayOfWeek]::Sunday)) {
    Write-Log "Weekend in New York; nothing to do."
    exit 0
}
[void][Ecosystem.Awake]::SetThreadExecutionState([uint32]2147483649)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED

try {
    # 0. One-off, owner's choice (not daily): only on the date in paper_session.wait_for_late_run_date
    #    (scanner_config.json) a research run still active is waited for until 14:00 New York, so its traders'
    #    files are the ones checked and traded. On every other day a run not finished by 16:00 means no orders.
    $pointerPath = Join-Path $projectRoot "output\full_research_control_plane_background_latest.json"
    $terminalStates = @("completed", "completed_review_required", "failed", "stopped_by_owner", "interrupted")
    $waitDate = ""
    try { $waitDate = [string](Get-Content -LiteralPath (Join-Path $projectRoot "scanner_config.json") -Raw | ConvertFrom-Json).paper_session.wait_for_late_run_date } catch { }
    $waited = $false
    while ($waitDate -eq (Get-Date -Format "yyyy-MM-dd")) {
        $active = $null
        try { $active = Get-Content -LiteralPath $pointerPath -Raw | ConvertFrom-Json } catch { }
        $alive = $null -ne $active -and $active.status -notin $terminalStates -and $active.pid -and
            (Get-Process -Id ([int]$active.pid) -ErrorAction SilentlyContinue)
        if (-not $alive) { break }
        if ((Get-Eastern) -ge (Get-Eastern).Date.AddHours(14)) {
            Write-Log "Research run $($active.run_id) still active at 14:00 New York; no more waiting."
            break
        }
        if (-not $waited) {
            Write-Log "Research run $($active.run_id) is still active; waiting for it (until 14:00 New York)."
            Show-Notice "Paper trading" "Perimeno na teleiosei to run $($active.run_id) prin ton elegxo."
            $waited = $true
        }
        Start-Sleep -Seconds 300
    }
    if ($waited) { Write-Log "Done waiting for the research run." }

    # 1. Pre-open check
    Write-Log "Pre-open check..."
    $checkExit = Invoke-Logged { & $python -W ignore "preopen_check.py" }
    Write-Log "Pre-open check finished with exit code $checkExit."

    # 2. Paper orders, only on today's completed research run
    $reason = $null
    $pointer = $null
    try { $pointer = Get-Content -LiteralPath (Join-Path $projectRoot "output\full_research_control_plane_background_latest.json") -Raw | ConvertFrom-Json } catch { }
    if ($checkExit -ne 0) { $reason = "the pre-open check failed (exit $checkExit)" }
    elseif ($null -eq $pointer) { $reason = "no research run pointer" }
    elseif ($pointer.status -notin @("completed", "completed_review_required")) { $reason = "the research run $($pointer.run_id) is '$($pointer.status)'" }
    else {
        $finished = $null
        try { $finished = [datetime]::Parse([string]$pointer.finished_at) } catch { }
        if ($null -eq $finished -or $finished.Date -ne (Get-Date).Date) { $reason = "no research run finished today (last: $($pointer.run_id))" }
    }
    $checked = @{}
    if ($null -eq $reason) {
        foreach ($agent in @("yankos", "periklis")) {
            $path = Join-Path $projectRoot "output\preopen\${agent}_strategy_preopen_latest.json"
            $runId = $null
            try { $runId = [string](Get-Content -LiteralPath $path -Raw | ConvertFrom-Json).run_id } catch { }
            if ($runId -ne $pointer.run_id) { $reason = "$agent checked signals are from '$runId', not from $($pointer.run_id)"; break }
            $checked[$agent] = "output\preopen\${agent}_strategy_preopen_latest.json"
        }
    }
    if ($null -eq $reason) {
        try {
            $previous = Get-Content -LiteralPath (Join-Path $projectRoot "output\trader_paper_trading_run_latest.json") -Raw | ConvertFrom-Json
            if ([datetime]::Parse([string]$previous.started_at).Date -eq (Get-Date).Date) { $reason = "paper trading already started today" }
        } catch { }
    }
    if ($null -eq $reason -and (Get-Eastern) -gt (Get-Eastern).Date.AddHours(15)) { $reason = "after the 15:00 New York entry cutoff" }

    if ($null -ne $reason) {
        Write-Log "No paper orders today: $reason"
        Show-Notice "Paper trading" "Xoris entoles simera: $reason"
        Invoke-Rest "No paper orders today ($reason)"
        exit 0
    }

    # 1b. Recheck at the open (owner 2026-10-22, the VRTX case): the ideas taken down at 09:00 are judged again with
    #     the opening price. Switch preopen.recheck_at_open: off / log (decisions written only) / on (restored ideas
    #     go back into the checked files). A failure leaves the checked files as they are.
    Wait-Eastern 9 32
    $recheckExit = Invoke-Retried "Open recheck" { & $python -W ignore "preopen_open_recheck.py" }
    Write-Log "Open recheck finished with exit code $recheckExit."

    Wait-Eastern 9 35
    Write-Log "Starting paper trading for $($pointer.run_id) on the pre-open checked files..."
    $env:ECOSYSTEM_RUN_ID = [string]$pointer.run_id
    $tradeExit = Invoke-Logged { & "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass `
        -File ".\start_trader_paper_trading_today.ps1" -PythonExe $python -YankosStrategy $checked["yankos"] `
        -PeriklisStrategy $checked["periklis"] }
    Write-Log "Paper trading launcher finished with exit code $tradeExit."
    $summary = "exit $tradeExit"
    $approved = $null
    try {
        $started = Get-Content -LiteralPath (Join-Path $projectRoot "output\trader_paper_trading_run_latest.json") -Raw | ConvertFrom-Json
        if ([datetime]::Parse([string]$started.started_at).Date -eq (Get-Date).Date) {
            $approved = [int]$started.approved_intent_count
            $summary = "egkrithikan $($started.approved_intent_count), stalthikan $($started.submitted_total)"
        }
    } catch { }
    Show-Notice "Paper trading" "Xekinise: $summary"

    # 3. After the close (at once when nothing was approved: no position can open)
    if ($approved -ne 0) { Wait-Eastern 16 10 }
    Write-Log "Session report..."
    $reportExit = Invoke-Retried "Session report" { & $python -W ignore "paper_session_report.py" }
    $day = $null
    try { $day = Get-Content -LiteralPath (Join-Path $projectRoot "output\paper_session\session_report_latest.json") -Raw | ConvertFrom-Json } catch { }
    if ($null -ne $day -and [string]$day.session_date -ne (Get-Eastern).ToString("yyyy-MM-dd")) { $day = $null }
    if ($null -eq $day) {
        Write-Log "Session report for today is missing (exit $reportExit); it can be written later with paper_session_report.py."
        Show-Notice "Paper trading - anafora" "I anafora tis imeras den grafitike (exit $reportExit). Log: $log"
    }
    if ($null -ne $day) {
        $average = if ($null -eq $day.day_result.average_result_pct) { "agnosto" } else { "$($day.day_result.average_result_pct)%" }
        Show-Notice "Paper trading - telos imeras" "Theseis: $($day.day_result.positions), meso apotelesma: $average. Anafora: output\paper_session\session_report_latest.md"
    }

    # Learning from the session (owner 2026-10-15): the ledger, then the evaluation of all sessions so far.
    Write-Log "Paper trade ledger and learning evaluation..."
    $ledgerExit = Invoke-Retried "Ledger" { & $python -W ignore "paper_trade_ledger.py" }
    $learnExit = Invoke-Retried "Learning evaluation" { & $python -W ignore "paper_learning.py" "evaluate" }
    Write-Log "Ledger exit $ledgerExit, evaluation exit $learnExit."

    # 4. Rest, once the session's workers are done
    Invoke-Rest "Paper session finished" $sessionWorkers
} catch {
    $message = $_.Exception.Message
    Write-Log "Afternoon session stopped by an error: $message"
    Show-Notice "Paper trading: sfalma" "To apogevmatino stamatise: $message. Log: $log"
    Invoke-Rest "Afternoon session stopped by an error" $sessionWorkers
    exit 1
}
