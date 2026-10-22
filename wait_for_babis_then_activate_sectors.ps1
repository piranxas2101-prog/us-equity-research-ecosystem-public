param(
    [Parameter(Mandatory = $true)][int]$BabisPid,
    [Parameter(Mandatory = $true)][string]$BabisJob
)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$logRoot = Join-Path $project 'data\sector_ecosystem'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$log = Join-Path $logRoot 'post_babis_activation.log'
Wait-Process -Id $BabisPid -ErrorAction SilentlyContinue
$statePath = Join-Path $BabisJob 'state.json'
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
if ($state.status -ne 'finished_with_review_required') {
    "Sector activation blocked: Babis ended as $($state.status)." | Add-Content -LiteralPath $log
    exit 3
}
Set-Location -LiteralPath $project
& '.\run_py.cmd' 'us_equity_monitor.py' 'sector-ecosystem' 'activate' '--babis-job' $BabisJob *>> $log
$exitCode = $LASTEXITCODE
if ($exitCode -eq 0) {
    & '.\run_py.cmd' 'ecosystem_post_babis_report.py' '--babis-job' $BabisJob *>> $log
    $exitCode = $LASTEXITCODE
}
if ($exitCode -eq 0) {
    & 'msg.exe' '*' 'Babis completed and all 11 sector agents were integrated into the ecosystem.' 2>$null
}
exit $exitCode
