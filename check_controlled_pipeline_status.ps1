$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pointerPath = Join-Path $projectRoot "output\controlled_pipeline_latest_background.json"

if (-not (Test-Path -LiteralPath $pointerPath)) {
    Write-Host "No controlled pipeline pointer found."
    exit 1
}

$state = Get-Content -LiteralPath $pointerPath -Raw | ConvertFrom-Json
$pidValue = [int]$state.pid
$proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue

Write-Host "Controlled pipeline"
Write-Host "Started: $($state.started_at)"
Write-Host "PID: $pidValue"
Write-Host "Running: $([bool]($null -ne $proc))"
Write-Host "Config: $($state.config)"
Write-Host "Stage1 limit: $($state.stage1_limit)"
Write-Host "Stage2 limit: $($state.stage2_limit)"
Write-Host "LLM: $($state.use_llm)"
Write-Host "Broker actions: $($state.broker_actions)"
Write-Host "Out log: $($state.out_log)"
Write-Host "Err log: $($state.err_log)"

if (Test-Path -LiteralPath $state.out_log) {
    Write-Host ""
    Write-Host "Last output lines:"
    Get-Content -LiteralPath $state.out_log -Tail 40
}

if (Test-Path -LiteralPath $state.err_log) {
    $errInfo = Get-Item -LiteralPath $state.err_log
    if ($errInfo.Length -gt 0) {
        Write-Host ""
        Write-Host "Last error lines:"
        Get-Content -LiteralPath $state.err_log -Tail 40
    }
}
