param(
    [int]$IntervalMinutes = 30,
    [int]$MaxRuns = 6,
    [string]$OutputRoot = ".\output\strategy_workshop",
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
    [string]$CompareFile = ".\output\scan_compare_strategy_upgrade_latest.json",
    [string]$YankosFile = ".\output\yankos_strategy_latest.json",
    [string]$PeriklisFile = ".\output\periklis_strategy_latest.json"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$logDir = Join-Path $projectRoot "logs"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

$statePath = Join-Path $projectRoot "output\strategy_workshop\daemon_state.json"
$stateDir = Split-Path -Parent $statePath
if (-not (Test-Path $stateDir)) {
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
}

function Save-State {
    param(
        [string]$Status,
        [int]$RunsCompleted,
        [string]$LastLog,
        [string]$LastError,
        [datetime]$NextRunAt
    )
    $state = [ordered]@{
        updated_at = (Get-Date -Format o)
        pid = $PID
        status = $Status
        runs_completed = $RunsCompleted
        max_runs = $MaxRuns
        interval_minutes = $IntervalMinutes
        last_log = $LastLog
        last_error = $LastError
        next_run_at = $NextRunAt.ToString("o")
    }
    $state | ConvertTo-Json -Depth 6 | Set-Content -Path $statePath -Encoding UTF8
}

$runs = 0
while ($runs -lt $MaxRuns) {
    $loopTs = Get-Date -Format "yyyyMMdd_HHmmss"
    $loopLog = Join-Path $logDir "strategy_workshop_loop_$loopTs.log"

    try {
        $nextAt = (Get-Date).AddMinutes($IntervalMinutes)
        Save-State -Status "running_cycle" -RunsCompleted $runs -LastLog $loopLog -LastError "" -NextRunAt $nextAt

        & $PythonPath "strategy_workshop_cycle.py" `
            "--compare" $CompareFile `
            "--yankos" $YankosFile `
            "--periklis" $PeriklisFile `
            "--output-root" $OutputRoot 2>&1 | Tee-Object -FilePath $loopLog

        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            throw "strategy_workshop_cycle.py failed with exit code $exitCode"
        }

        $runs += 1
        if ($runs -ge $MaxRuns) {
            break
        }

        $nextAt = (Get-Date).AddMinutes($IntervalMinutes)
        Save-State -Status "sleeping_ok" -RunsCompleted $runs -LastLog $loopLog -LastError "" -NextRunAt $nextAt
        Start-Sleep -Seconds ($IntervalMinutes * 60)
    } catch {
        $nextAt = (Get-Date).AddMinutes($IntervalMinutes)
        Save-State -Status "sleeping_retry" -RunsCompleted $runs -LastLog $loopLog -LastError ([string]$_.Exception.Message) -NextRunAt $nextAt
        Start-Sleep -Seconds ($IntervalMinutes * 60)
    }
}

Save-State -Status "completed" -RunsCompleted $runs -LastLog "" -LastError "" -NextRunAt (Get-Date)
