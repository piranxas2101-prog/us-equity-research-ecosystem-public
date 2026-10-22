param(
    [switch]$Check,
    [int]$Workers = 2
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$argsList = @("slack_agent_gateway.py", "--workers", [string]$Workers)
if ($Check) {
    $argsList += "--check"
}

& (Join-Path $projectRoot "run_py.ps1") @argsList
exit $LASTEXITCODE
