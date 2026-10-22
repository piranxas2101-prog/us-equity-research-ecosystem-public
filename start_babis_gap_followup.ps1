$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$logRoot = Join-Path $project 'data\babis_gap_followup'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$log = Join-Path $logRoot 'scheduled_followup.log'
Set-Location -LiteralPath $project
& '.\run_py.cmd' 'us_equity_monitor.py' 'babis-gaps' 'run' '--provider' 'fmp' *>> $log
exit $LASTEXITCODE
