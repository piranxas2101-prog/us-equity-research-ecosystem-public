param(
    [string]$SourceFile = ".\education\intelligent_investor_source.md",
    [string]$ConfigPath = ".\scanner_config.json",
    [string]$OutputRoot = ".\output\education_workshop",
    [string]$OllamaHost = "http://127.0.0.1:11434",
    [string]$Model = "",
    [double]$Temperature = 0.15,
    [int]$NumPredict = 380,
    [int]$TimeoutSec = 600,
    [int]$MeetingMinutes = 30,
    [string]$DeadlineText = "Today 18:00 (Europe/Athens)"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$logDir = Join-Path $projectRoot "logs"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$outLog = Join-Path $logDir "intelligent_workshop_$ts.out.log"
$errLog = Join-Path $logDir "intelligent_workshop_$ts.err.log"

$args = @(
    "-ExecutionPolicy", "Bypass",
    "-File", ".\run_intelligent_investor_workshop.ps1",
    "-SourceFile", $SourceFile,
    "-ConfigPath", $ConfigPath,
    "-OutputRoot", $OutputRoot,
    "-OllamaHost", $OllamaHost,
    "-Temperature", "$Temperature",
    "-NumPredict", "$NumPredict",
    "-TimeoutSec", "$TimeoutSec",
    "-MeetingMinutes", "$MeetingMinutes",
    "-DeadlineText", $DeadlineText
)

if (-not [string]::IsNullOrWhiteSpace($Model)) {
    $args += @("-Model", $Model)
}

$proc = Start-Process `
    -FilePath "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -ArgumentList $args `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden `
    -PassThru

Start-Sleep -Seconds 2

$info = [ordered]@{
    started_at = (Get-Date -Format o)
    pid = $proc.Id
    source_file = $SourceFile
    deadline = $DeadlineText
    out_log = $outLog
    err_log = $errLog
}

$pointer = Join-Path $projectRoot "output\education_workshop\latest_background_run.json"
$pointerDir = Split-Path -Parent $pointer
if (-not (Test-Path $pointerDir)) {
    New-Item -ItemType Directory -Path $pointerDir | Out-Null
}
$info | ConvertTo-Json -Depth 5 | Set-Content -Path $pointer -Encoding UTF8

Write-Host "Background workshop started."
Write-Host "PID: $($proc.Id)"
Write-Host "Out log: $outLog"
Write-Host "Err log: $errLog"
Write-Host "Pointer: $pointer"
