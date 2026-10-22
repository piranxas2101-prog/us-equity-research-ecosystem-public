param(
    [string]$Config = "scanner_config.json",
    [int]$Stage1Limit = 20,
    [int]$Stage2Limit = 8,
    [int]$Stage2LlmLimit = 0,
    [switch]$UseLlm,
    [switch]$NoRespectUniverseCap
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$outputDir = Join-Path $projectRoot "output"
$logDir = Join-Path $projectRoot "logs"
if (-not (Test-Path -LiteralPath $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
}
if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

$pointerPath = Join-Path $outputDir "controlled_pipeline_latest_background.json"
if (Test-Path -LiteralPath $pointerPath) {
    try {
        $existing = Get-Content -LiteralPath $pointerPath -Raw | ConvertFrom-Json
        $existingPid = [int]$existing.pid
        $existingProc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
        if ($null -ne $existingProc) {
            Write-Host "Controlled pipeline already active (PID $existingPid)."
            Write-Host "Use .\check_controlled_pipeline_status.ps1 for status."
            exit 0
        }
    } catch {
        # Ignore stale or malformed pointer and continue.
    }
}

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$outLog = Join-Path $logDir "controlled_pipeline_$ts.out.log"
$errLog = Join-Path $logDir "controlled_pipeline_$ts.err.log"

$pipelineArgs = @(
    "/c",
    ".\run_py.cmd",
    "us_equity_monitor.py",
    "--config", $Config,
    "pipeline",
    "--stage1-limit", "$Stage1Limit",
    "--stage2-limit", "$Stage2Limit",
    "--stage2-llm-limit", "$Stage2LlmLimit"
)

if (-not $UseLlm) {
    $pipelineArgs += "--no-stage1-llm"
    $pipelineArgs += "--no-stage2-llm"
}
if (-not $NoRespectUniverseCap) {
    $pipelineArgs += "--respect-universe-cap"
}

$proc = Start-Process `
    -FilePath "cmd.exe" `
    -ArgumentList $pipelineArgs `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden `
    -PassThru

Start-Sleep -Seconds 2

$payload = [ordered]@{
    started_at = (Get-Date -Format o)
    pid = $proc.Id
    config = $Config
    stage1_limit = $Stage1Limit
    stage2_limit = $Stage2Limit
    stage2_llm_limit = $Stage2LlmLimit
    use_llm = [bool]$UseLlm
    respect_universe_cap = -not [bool]$NoRespectUniverseCap
    broker_actions = "disabled"
    out_log = $outLog
    err_log = $errLog
    command = ($pipelineArgs -join " ")
}
$payload | ConvertTo-Json -Depth 6 | Set-Content -Path $pointerPath -Encoding UTF8

Write-Host "Controlled pipeline started in background."
Write-Host "PID: $($proc.Id)"
Write-Host "Out log: $outLog"
Write-Host "Err log: $errLog"
Write-Host "Pointer: $pointerPath"
