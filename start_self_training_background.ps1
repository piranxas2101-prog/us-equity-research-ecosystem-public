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

$existingPointerPath = Join-Path $projectRoot "output\self_training\latest_background_daemon.json"
if (Test-Path $existingPointerPath) {
    try {
        $existing = Get-Content $existingPointerPath -Raw | ConvertFrom-Json
        $existingPid = [int]$existing.pid
        $existingProc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
        if ($null -ne $existingProc) {
            Write-Host "Self-training daemon already running (PID $existingPid)."
            Write-Host "Use .\check_self_training_status.ps1 or .\stop_self_training_background.ps1 first."
            exit 0
        }
    } catch {
        # Ignore malformed pointer and continue.
    }
}

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$outLog = Join-Path $logDir "self_training_daemon_$ts.out.log"
$errLog = Join-Path $logDir "self_training_daemon_$ts.err.log"

$args = @(
    "-ExecutionPolicy", "Bypass",
    "-File", ".\run_self_training_daemon.ps1",
    "-IntervalMinutes", "$IntervalMinutes",
    "-RetryDelayMinutes", "$RetryDelayMinutes",
    "-MaxRuns", "$MaxRuns",
    "-ConfigPath", $ConfigPath,
    "-EnvFile", $EnvFile,
    "-BabisFile", $BabisFile,
    "-GiannisFile", $GiannisFile,
    "-FocusFile", $FocusFile,
    "-OutputRoot", $OutputRoot
)
if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
    $args += @("-PythonPath", $PythonPath)
}
if (-not [string]::IsNullOrWhiteSpace($Model)) {
    $args += @("-Model", $Model)
}
if (-not [string]::IsNullOrWhiteSpace($OllamaHost)) {
    $args += @("-OllamaHost", $OllamaHost)
}

$proc = Start-Process `
    -FilePath "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -ArgumentList $args `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden `
    -PassThru

Start-Sleep -Seconds 2

$pointerPath = Join-Path $projectRoot "output\self_training\latest_background_daemon.json"
$pointerDir = Split-Path -Parent $pointerPath
if (-not (Test-Path $pointerDir)) {
    New-Item -ItemType Directory -Path $pointerDir -Force | Out-Null
}

$payload = [ordered]@{
    started_at = (Get-Date -Format o)
    pid = $proc.Id
    interval_minutes = $IntervalMinutes
    retry_delay_minutes = $RetryDelayMinutes
    max_runs = $MaxRuns
    python_path = $PythonPath
    out_log = $outLog
    err_log = $errLog
    state_file = ".\output\self_training\daemon_state.json"
}
$payload | ConvertTo-Json -Depth 6 | Set-Content -Path $pointerPath -Encoding UTF8

Write-Host "Self-training daemon started in background."
Write-Host "PID: $($proc.Id)"
Write-Host "Out log: $outLog"
Write-Host "Err log: $errLog"
Write-Host "Pointer: $pointerPath"
