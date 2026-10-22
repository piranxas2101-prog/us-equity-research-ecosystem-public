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
$logPath = Join-Path $logDir "scan_$timestamp.log"

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath "us_equity_monitor.py" "--config" "scanner_config.json" "scan" 2>&1 | Tee-Object -FilePath $logPath
$scanExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction

if ($scanExitCode -ne 0) {
    throw "Scan failed with exit code $scanExitCode. Check log: $logPath"
}

Write-Host "Scan completed. Log: $logPath"
