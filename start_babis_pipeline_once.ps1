$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$logRoot = Join-Path $project 'data\babis_pipeline'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$log = Join-Path $logRoot 'scheduled_start.log'
Set-Location -LiteralPath $project
& '.\run_py.cmd' 'us_equity_monitor.py' 'babis' 'start' *>> $log
exit $LASTEXITCODE
