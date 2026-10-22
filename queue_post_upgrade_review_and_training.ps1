param(
    [string]$PointerPath = ".\output\dual_watchlist_latest_background.json",
    [string]$FollowupQueuePath = ".\output\queued_followup_dual_watchlist_rerun_latest.json",
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
$logPath = Join-Path $logDir "post_upgrade_review_training_$stamp.log"
$markerPath = Join-Path $outputDir "queued_post_upgrade_review_and_training_latest.json"
$compareJson = Join-Path $outputDir "scan_compare_strategy_upgrade_latest.json"
$compareMd = Join-Path $outputDir "scan_compare_strategy_upgrade_latest.md"
$petrosAnnaPath = Join-Path $outputDir "petros_anna_followup_latest.md"

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
$queuePayload = [ordered]@{
    queued_at = (Get-Date -Format o)
    waiting_for_pid = $pidToWatch
    pointer_path = $PointerPath
    followup_queue_path = $FollowupQueuePath
    log_path = $logPath
}
$queuePayload | ConvertTo-Json -Depth 5 | Set-Content -Path $markerPath -Encoding UTF8

Log-Line "Queued post-upgrade review and training."
Log-Line "Watching PID $pidToWatch"

while ($true) {
    $proc = Get-Process -Id $pidToWatch -ErrorAction SilentlyContinue
    if ($null -eq $proc) {
        break
    }
    Start-Sleep -Seconds $PollSeconds
}

Log-Line "Watched post-upgrade run completed."

$baselineDir = $null
if (Test-Path $FollowupQueuePath) {
    try {
        $queueInfo = Get-Content $FollowupQueuePath -Raw | ConvertFrom-Json
        $baselineDir = [string]$queueInfo.baseline_dir
    } catch {
        Log-Line "Could not parse follow-up queue info: $($_.Exception.Message)"
    }
}

if ((-not (Test-Path $compareJson)) -and $baselineDir) {
    $pythonPath = Resolve-PythonPath
    $compareArgs = @(
        "compare_agent_runs.py",
        "--old-babis", (Join-Path $baselineDir "babis_shortlist_latest.json"),
        "--new-babis", "output/babis_shortlist_latest.json",
        "--old-giannis", (Join-Path $baselineDir "giannis_rule_based_latest.json"),
        "--new-giannis", "output/giannis_rule_based_latest.json",
        "--old-yankos", (Join-Path $baselineDir "yankos_strategy_latest.json"),
        "--new-yankos", "output/yankos_strategy_latest.json",
        "--old-petros", (Join-Path $baselineDir "petros_review_latest.json"),
        "--new-petros", "output/petros_review_latest.json",
        "--baseline-label", "before_strategy_library_upgrade",
        "--current-label", "after_strategy_library_upgrade",
        "--out-json", $compareJson,
        "--out-md", $compareMd
    )
    $oldPeriklis = Join-Path $baselineDir "periklis_strategy_latest.json"
    if (Test-Path $oldPeriklis) {
        $compareArgs += @(
            "--old-periklis", $oldPeriklis,
            "--new-periklis", "output/periklis_strategy_latest.json"
        )
    }
    & $pythonPath @compareArgs | ForEach-Object {
        Log-Line $_
    }
    Log-Line "Fallback compare generation completed."
}

$waitCount = 0
while ((-not (Test-Path $compareJson)) -and ($waitCount -lt 30)) {
    Start-Sleep -Seconds 10
    $waitCount += 1
}

if (-not (Test-Path $compareJson)) {
    throw "Missing compare file after waiting: $compareJson"
}

$pythonPath = Resolve-PythonPath
& $pythonPath "strategy_backtest.py" "build" "--out-prefix" "output/strategy_backtest" | ForEach-Object {
    Log-Line $_
}
& $pythonPath "strategy_agent_training.py" "build" "--compare" $compareJson "--yankos" "output/yankos_strategy_latest.json" "--periklis" "output/periklis_strategy_latest.json" "--backtest" "output/strategy_backtest_latest.json" "--output-dir" "output" | ForEach-Object {
    Log-Line $_
}

$annaText = (& $pythonPath "petros_agent.py" "ticker" "ANNA" "--petros" "output/petros_review_latest.json" 2>&1 | Out-String).Trim()
if ([string]::IsNullOrWhiteSpace($annaText)) {
    $annaText = "[EMPTY_RESPONSE]"
}
$annaLines = @(
    "# Petros Follow-Up - ANNA",
    "",
    "- Generated at: $(Get-Date -Format o)",
    "- Trigger: user requested deeper follow-up after ANNA had a strong day.",
    "",
    "## Petros",
    "",
    $annaText
)
$annaLines | Set-Content -Path $petrosAnnaPath -Encoding UTF8
Log-Line "Petros ANNA follow-up written: $petrosAnnaPath"

Log-Line "Post-upgrade review and training completed."
