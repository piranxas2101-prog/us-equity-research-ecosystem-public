param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("giannis", "babis", "petros")]
    [string]$Agent,

    [Parameter(Mandatory = $true)]
    [string]$Message,

    [string]$ConfigPath = ".\scanner_config.json",
    [string]$OllamaHost = "http://127.0.0.1:11434",
    [string]$Model = "",
    [double]$Temperature = 0.2,
    [int]$NumPredict = 240,
    [int]$TimeoutSec = 240,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

function Resolve-PythonPath {
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }
    $globalPython = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    try {
        if (Test-Path $globalPython) {
            return $globalPython
        }
    } catch {
        # Fallback to PATH-based python when AppData access is restricted.
    }
    return "python"
}

if ($Agent -eq "petros") {
    $pythonPath = Resolve-PythonPath
    if ([string]::IsNullOrWhiteSpace($OutputPath)) {
        $ts = Get-Date -Format "yyyyMMdd_HHmmss"
        $OutputPath = ".\output\agent_chat_petros_$ts.md"
    }

    $previousPythonIoEncoding = $env:PYTHONIOENCODING
    $env:PYTHONIOENCODING = "utf-8"
    try {
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    } catch {
        # Older hosts may not allow changing the console encoding.
    }
    $text = (& $pythonPath "petros_agent.py" "chat" $Message 2>&1 | Out-String).Trim()
    $env:PYTHONIOENCODING = $previousPythonIoEncoding
    if ([string]::IsNullOrWhiteSpace($text)) {
        $text = "[EMPTY_RESPONSE]"
    }

    Write-Host ""
    Write-Host "=== petros ==="
    Write-Host $text
    Write-Host ""

    $outDir = Split-Path -Parent $OutputPath
    if (-not [string]::IsNullOrWhiteSpace($outDir) -and -not (Test-Path $outDir)) {
        New-Item -ItemType Directory -Path $outDir | Out-Null
    }

    $lines = @(
        "# Agent Chat",
        "",
        "- Timestamp: $(Get-Date -Format o)",
        "- Agent: petros",
        "- Engine: rule-based local memory",
        "",
        "## User",
        "",
        $Message,
        "",
        "## Agent",
        "",
        $text
    )

    $lines | Set-Content -Path $OutputPath -Encoding UTF8
    Write-Host "Saved: $OutputPath"
    exit 0
}

if (-not (Test-Path $ConfigPath)) {
    throw "Missing config file: $ConfigPath"
}

$config = Get-Content $ConfigPath -Raw | ConvertFrom-Json

if ([string]::IsNullOrWhiteSpace($Model)) {
    $Model = [string]$config.ollama.model
}
if ([string]::IsNullOrWhiteSpace($Model)) {
    $Model = "gemma:7b"
}

$promptRel = ""
if ($Agent -eq "giannis") {
    $promptRel = [string]$config.agents.agent_1.prompt_file
} else {
    $promptRel = [string]$config.agents.agent_2.prompt_file
}

if ([string]::IsNullOrWhiteSpace($promptRel)) {
    $promptRel = [string]$config.ollama.agent_prompt_file
}
if ([string]::IsNullOrWhiteSpace($promptRel)) {
    throw "No prompt file configured for agent '$Agent'."
}

$promptPath = $promptRel
if (-not [System.IO.Path]::IsPathRooted($promptPath)) {
    $promptPath = Join-Path $projectRoot $promptRel
}
if (-not (Test-Path $promptPath)) {
    throw "Prompt file not found: $promptPath"
}

$systemPrompt = Get-Content $promptPath -Raw
if ([string]::IsNullOrWhiteSpace($systemPrompt)) {
    throw "Prompt file is empty: $promptPath"
}

$payload = @{
    model = $Model
    system = $systemPrompt
    prompt = $Message
    stream = $false
    options = @{
        temperature = $Temperature
        num_predict = $NumPredict
    }
} | ConvertTo-Json -Depth 8

$response = Invoke-RestMethod -Uri "$OllamaHost/api/generate" -Method Post -ContentType "application/json" -Body $payload -TimeoutSec $TimeoutSec
$text = [string]$response.response

if ([string]::IsNullOrWhiteSpace($text)) {
    $text = "[EMPTY_RESPONSE]"
}

Write-Host ""
Write-Host "=== $Agent ==="
Write-Host $text
Write-Host ""

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $ts = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputPath = ".\output\agent_chat_${Agent}_$ts.md"
}

$outDir = Split-Path -Parent $OutputPath
if (-not [string]::IsNullOrWhiteSpace($outDir) -and -not (Test-Path $outDir)) {
    New-Item -ItemType Directory -Path $outDir | Out-Null
}

$lines = @(
    "# Agent Chat",
    "",
    "- Timestamp: $(Get-Date -Format o)",
    "- Agent: $Agent",
    "- Model: $Model",
    "- Prompt file: $promptPath",
    "",
    "## User",
    "",
    $Message,
    "",
    "## Agent",
    "",
    $text
)

$lines | Set-Content -Path $OutputPath -Encoding UTF8
Write-Host "Saved: $OutputPath"
