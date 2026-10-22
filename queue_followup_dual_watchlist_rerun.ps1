param(
    [string]$PointerPath = ".\output\dual_watchlist_latest_background.json",
    [switch]$FullMarket,
    [int]$PollSeconds = 20
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$outputDir = Join-Path $projectRoot "output"
$logDir = Join-Path $projectRoot "logs"
if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
}
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath = Join-Path $logDir "followup_dual_watchlist_rerun_$stamp.log"
$markerPath = Join-Path $outputDir "queued_followup_dual_watchlist_rerun_latest.json"
$strategyCompareJson = Join-Path $outputDir "scan_compare_strategy_upgrade_latest.json"
$strategyCompareMd = Join-Path $outputDir "scan_compare_strategy_upgrade_latest.md"

function Log-Line {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format o), $Message
    Add-Content -Path $logPath -Value $line -Encoding UTF8
}

function Resolve-PythonPath {
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }
    $globalPython = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    try {
        if (Test-Path $globalPython) {
            return $globalPython
        }
    } catch {
    }
    return "python"
}

if (-not (Test-Path $PointerPath)) {
    throw "Missing pointer file: $PointerPath"
}

$pointer = Get-Content $PointerPath -Raw | ConvertFrom-Json
$pidToWatch = [int]$pointer.pid
$watchedCommand = [string]$pointer.command
$baselineDir = Get-ChildItem $outputDir -Directory -Filter "baseline_compare_*" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

$queuePayload = [ordered]@{
    queued_at = (Get-Date -Format o)
    waiting_for_pid = $pidToWatch
    pointer_path = $PointerPath
    watched_command = $watchedCommand
    followup_mode = if ($FullMarket) { "full_market" } else { "locked_watchlist" }
    log_path = $logPath
    baseline_dir = if ($baselineDir) { $baselineDir.FullName } else { $null }
}
$queuePayload | ConvertTo-Json -Depth 5 | Set-Content -Path $markerPath -Encoding UTF8

Log-Line "Queued follow-up rerun."
Log-Line "Watching PID $pidToWatch from $PointerPath"

while ($true) {
    $proc = Get-Process -Id $pidToWatch -ErrorAction SilentlyContinue
    if ($null -eq $proc) {
        break
    }
    Start-Sleep -Seconds $PollSeconds
}

Log-Line "Watched process completed. Starting follow-up dual watchlist run."

$args = @(
    "-ExecutionPolicy", "Bypass",
    "-File", ".\start_dual_watchlist_background.ps1"
)
if ($FullMarket) {
    $args += "-FullMarket"
}

& "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" @args | ForEach-Object {
    Log-Line $_
}

Start-Sleep -Seconds 2
$newPointer = Get-Content $PointerPath -Raw | ConvertFrom-Json
$followupPid = [int]$newPointer.pid
Log-Line "Follow-up PID detected: $followupPid"

while ($true) {
    $proc = Get-Process -Id $followupPid -ErrorAction SilentlyContinue
    if ($null -eq $proc) {
        break
    }
    Start-Sleep -Seconds $PollSeconds
}

Log-Line "Follow-up dual watchlist run completed."

if ($baselineDir) {
    $pythonPath = Resolve-PythonPath
    $compareArgs = @(
        "compare_agent_runs.py",
        "--old-babis", (Join-Path $baselineDir.FullName "babis_shortlist_latest.json"),
        "--new-babis", "output/babis_shortlist_latest.json",
        "--old-giannis", (Join-Path $baselineDir.FullName "giannis_rule_based_latest.json"),
        "--new-giannis", "output/giannis_rule_based_latest.json",
        "--old-yankos", (Join-Path $baselineDir.FullName "yankos_strategy_latest.json"),
        "--new-yankos", "output/yankos_strategy_latest.json",
        "--old-petros", (Join-Path $baselineDir.FullName "petros_review_latest.json"),
        "--new-petros", "output/petros_review_latest.json",
        "--baseline-label", "before_strategy_library_upgrade",
        "--current-label", "after_strategy_library_upgrade",
        "--out-json", $strategyCompareJson,
        "--out-md", $strategyCompareMd
    )
    $oldPeriklis = Join-Path $baselineDir.FullName "periklis_strategy_latest.json"
    if (Test-Path $oldPeriklis) {
        $compareArgs += @(
            "--old-periklis", $oldPeriklis,
            "--new-periklis", "output/periklis_strategy_latest.json"
        )
    }
    & $pythonPath @compareArgs | ForEach-Object {
        Log-Line $_
    }
    Log-Line "Strategy-upgrade compare report written."
}

Log-Line "Follow-up rerun launch attempt completed."
