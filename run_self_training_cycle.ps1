param(
    [string]$ConfigPath = ".\scanner_config.json",
    [string]$EnvFile = ".\.env",
    [string]$BabisFile = ".\output\babis_shortlist_latest.json",
    [string]$GiannisFile = ".\output\giannis_rule_based_latest.json",
    [string]$FocusFile = ".\output\focus_basket_latest.json",
    [string]$OutputRoot = ".\output\self_training",
    [string]$PythonPath = "",
    [int]$MeetingMinutes = 30,
    [string]$OllamaHost = "",
    [string]$Model = "",
    [double]$Temperature = 0.15,
    [int]$NumPredict = 420,
    [int]$TimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

function Resolve-PythonPath {
    param([string]$PreferredPath)
    if (-not [string]::IsNullOrWhiteSpace($PreferredPath)) {
        return $PreferredPath
    }
    if (-not [string]::IsNullOrWhiteSpace($env:PYTHON_EXE)) {
        return [string]$env:PYTHON_EXE
    }
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $cmd -and -not [string]::IsNullOrWhiteSpace($cmd.Source)) {
        return $cmd.Source
    }
    try {
        $wherePaths = @(where.exe python 2>$null)
        if ($wherePaths.Count -gt 0 -and -not [string]::IsNullOrWhiteSpace([string]$wherePaths[0])) {
            return [string]$wherePaths[0]
        }
    } catch {
        # Ignore and keep trying explicit paths.
    }
    $globalPython = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    try {
        if (Test-Path $globalPython) {
            return $globalPython
        }
    } catch {
        # Ignore access errors and fallback to PATH-based python.
    }
    return "python"
}

function Resolve-PathSafe {
    param([string]$PathValue)
    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return $PathValue
    }
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return $PathValue
    }
    return (Join-Path $projectRoot $PathValue)
}

function Ensure-FileExists {
    param(
        [string]$PathValue,
        [string]$Label
    )
    if (-not (Test-Path $PathValue)) {
        throw "Missing $Label file: $PathValue"
    }
}

$configResolved = Resolve-PathSafe $ConfigPath
$envResolved = Resolve-PathSafe $EnvFile
$babisResolved = Resolve-PathSafe $BabisFile
$giannisResolved = Resolve-PathSafe $GiannisFile
$focusResolved = Resolve-PathSafe $FocusFile
$outputResolved = Resolve-PathSafe $OutputRoot

Ensure-FileExists -PathValue $configResolved -Label "config"
Ensure-FileExists -PathValue $envResolved -Label "env"
Ensure-FileExists -PathValue $babisResolved -Label "babis"
Ensure-FileExists -PathValue $giannisResolved -Label "giannis"

if (-not (Test-Path $outputResolved)) {
    New-Item -ItemType Directory -Path $outputResolved -Force | Out-Null
}

$logDir = Join-Path $projectRoot "logs"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath = Join-Path $logDir "self_training_cycle_$ts.log"

$pythonPath = Resolve-PythonPath -PreferredPath $PythonPath
$args = @(
    "run_self_training_cycle.py",
    "--config", $configResolved,
    "--env-file", $envResolved,
    "--babis-file", $babisResolved,
    "--giannis-file", $giannisResolved,
    "--focus-file", $focusResolved,
    "--output-root", $outputResolved,
    "--meeting-minutes", "$MeetingMinutes",
    "--temperature", "$Temperature",
    "--num-predict", "$NumPredict",
    "--timeout-seconds", "$TimeoutSeconds"
)

if (-not [string]::IsNullOrWhiteSpace($OllamaHost)) {
    $args += @("--host", $OllamaHost)
}
if (-not [string]::IsNullOrWhiteSpace($Model)) {
    $args += @("--model", $Model)
}

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $pythonPath @args 2>&1 | Tee-Object -FilePath $logPath
$rawExitCode = $LASTEXITCODE
$exitCode = if ($null -eq $rawExitCode) { 1 } else { [int]$rawExitCode }
$ErrorActionPreference = $previousErrorAction
if ($exitCode -ne 0) {
    throw "Self-training cycle failed with exit code $exitCode. PythonPath=$pythonPath. Log: $logPath"
}

$latestCyclePtr = Join-Path $outputResolved "latest_cycle.txt"
$latestManifestPtr = Join-Path $outputResolved "latest_manifest.json"

Write-Host "Self-training cycle completed."
Write-Host "Log: $logPath"
if (Test-Path $latestCyclePtr) {
    $cycleDir = [string](Get-Content $latestCyclePtr -Raw).Trim()
    Write-Host "Latest cycle dir: $cycleDir"
}
if (Test-Path $latestManifestPtr) {
    Write-Host "Latest manifest: $latestManifestPtr"
}
