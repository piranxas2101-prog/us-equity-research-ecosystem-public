$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$logRoot = Join-Path $projectRoot "logs"
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
Set-Location $projectRoot

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -LiteralPath (Join-Path $logRoot "slack_research_health.log") -Value "[$timestamp] checking"
& (Join-Path $projectRoot "run_py.ps1") "slack_research_health.py" "--notify" *>> (Join-Path $logRoot "slack_research_health.log")
exit $LASTEXITCODE
