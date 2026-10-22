$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonPath)) {
    $globalPython = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    if (Test-Path $globalPython) {
        $pythonPath = $globalPython
    } else {
        $pythonPath = "python"
    }
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logDir = Join-Path $projectRoot "logs"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}
$logPath = Join-Path $logDir "pipeline_$timestamp.log"

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath "us_equity_monitor.py" "--config" "scanner_config.json" "pipeline" "--stage1-limit" "120" "--stage2-limit" "60" "--stage2-llm-limit" "20" "--no-stage1-llm" 2>&1 | Tee-Object -FilePath $logPath
$pipelineExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction

if ($pipelineExitCode -ne 0) {
    throw "Pipeline failed with exit code $pipelineExitCode. Check log: $logPath"
}

Write-Host "Pipeline completed. Log: $logPath"
