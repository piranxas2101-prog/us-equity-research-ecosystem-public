param(
    [int]$IntervalMinutes = 180,
    [int]$RetryDelayMinutes = 10,
    [int]$MaxRuns = 0,
    [string]$Model = "",
    [string]$OllamaHost = "",
    [string]$ConfigPath = ".\scanner_config.json",
    [string]$EnvFile = ".\.env",
    [string]$BabisFile = ".\output\babis_shortlist_latest.json",
    [string]$GiannisFile = ".\output\giannis_rule_based_latest.json",
    [string]$FocusFile = ".\output\focus_basket_latest.json",
    [string]$OutputRoot = ".\output\self_training",
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$logDir = Join-Path $projectRoot "logs"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

$statePath = Join-Path $projectRoot "output\self_training\daemon_state.json"
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
        retry_delay_minutes = $RetryDelayMinutes
        last_log = $LastLog
        last_error = $LastError
        next_run_at = $NextRunAt.ToString("o")
    }
    $state | ConvertTo-Json -Depth 6 | Set-Content -Path $statePath -Encoding UTF8
}

$runs = 0
while ($true) {
    if ($MaxRuns -gt 0 -and $runs -ge $MaxRuns) {
        Save-State -Status "completed" -RunsCompleted $runs -LastLog "" -LastError "" -NextRunAt (Get-Date)
        break
    }

    $loopTs = Get-Date -Format "yyyyMMdd_HHmmss"
    $loopLog = Join-Path $logDir "self_training_daemon_loop_$loopTs.log"
    $lastError = ""

    try {
        $runArgs = @(
            "-ExecutionPolicy", "Bypass",
            "-File", ".\run_self_training_cycle.ps1",
            "-ConfigPath", $ConfigPath,
            "-EnvFile", $EnvFile,
            "-BabisFile", $BabisFile,
            "-GiannisFile", $GiannisFile,
            "-FocusFile", $FocusFile,
            "-OutputRoot", $OutputRoot
        )
        if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
            $runArgs += @("-PythonPath", $PythonPath)
        }
        if (-not [string]::IsNullOrWhiteSpace($OllamaHost)) {
            $runArgs += @("-OllamaHost", $OllamaHost)
        }
        if (-not [string]::IsNullOrWhiteSpace($Model)) {
            $runArgs += @("-Model", $Model)
        }

        $nextAt = (Get-Date).AddMinutes($IntervalMinutes)
        Save-State -Status "running_cycle" -RunsCompleted $runs -LastLog $loopLog -LastError "" -NextRunAt $nextAt

        $previousErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" @runArgs 2>&1 | Tee-Object -FilePath $loopLog
        $cycleExitCode = $LASTEXITCODE
        $ErrorActionPreference = $previousErrorAction

        if ($cycleExitCode -ne 0) {
            throw "Cycle runner returned exit code $cycleExitCode."
        }

        $runs += 1
        $nextAt = (Get-Date).AddMinutes($IntervalMinutes)
        Save-State -Status "sleeping_ok" -RunsCompleted $runs -LastLog $loopLog -LastError "" -NextRunAt $nextAt
        Start-Sleep -Seconds ($IntervalMinutes * 60)
    } catch {
        $lastError = [string]$_.Exception.Message
        $nextAt = (Get-Date).AddMinutes($RetryDelayMinutes)
        Save-State -Status "sleeping_retry" -RunsCompleted $runs -LastLog $loopLog -LastError $lastError -NextRunAt $nextAt
        Start-Sleep -Seconds ($RetryDelayMinutes * 60)
    }
}
