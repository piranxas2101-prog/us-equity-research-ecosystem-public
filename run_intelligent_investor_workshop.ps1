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

function Resolve-PathSafe {
    param([string]$PathValue)
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return $PathValue
    }
    return (Join-Path $projectRoot $PathValue)
}

function Load-ConfigJson {
    param([string]$PathValue)
    if (-not (Test-Path $PathValue)) {
        throw "Missing config file: $PathValue"
    }
    return (Get-Content $PathValue -Raw | ConvertFrom-Json)
}

function Load-TextFile {
    param([string]$PathValue, [string]$ErrorLabel)
    if (-not (Test-Path $PathValue)) {
        throw "Missing $ErrorLabel file: $PathValue"
    }
    return [string](Get-Content $PathValue -Raw)
}

function Invoke-AgentResponse {
    param(
        [string]$AgentName,
        [string]$SystemPrompt,
        [string]$UserPrompt
    )

    $body = @{
        model = $Model
        system = $SystemPrompt
        prompt = $UserPrompt
        stream = $false
        options = @{
            temperature = $Temperature
            num_predict = $NumPredict
        }
    } | ConvertTo-Json -Depth 8

    try {
        $resp = Invoke-RestMethod -Uri "$OllamaHost/api/generate" -Method Post -ContentType "application/json" -Body $body -TimeoutSec $TimeoutSec
        $txt = [string]$resp.response
        if ([string]::IsNullOrWhiteSpace($txt)) {
            return "[${AgentName}_ERROR] empty response"
        }
        return $txt
    } catch {
        return "[${AgentName}_ERROR] $($_.Exception.Message)"
    }
}

function Save-StageReport {
    param(
        [string]$PathValue,
        [string]$Title,
        [string]$AgentName,
        [string]$PromptText,
        [string]$ResponseText
    )
    $lines = @(
        "# $Title",
        "",
        "- Timestamp: $(Get-Date -Format o)",
        "- Agent: $AgentName",
        "",
        "## Prompt",
        "",
        $PromptText,
        "",
        "## Response",
        "",
        $ResponseText
    )
    $lines | Set-Content -Path $PathValue -Encoding UTF8
}

$configPathResolved = Resolve-PathSafe $ConfigPath
$sourcePathResolved = Resolve-PathSafe $SourceFile
$outputRootResolved = Resolve-PathSafe $OutputRoot

$config = Load-ConfigJson -PathValue $configPathResolved
$sourceText = Load-TextFile -PathValue $sourcePathResolved -ErrorLabel "source"

if ($sourceText.Length -lt 400) {
    throw "Source text is too short. Add real notes before running."
}
if ($sourceText -match "Source Notes") {
    throw "Source file still looks like template. Replace with your real reading notes first."
}

if ([string]::IsNullOrWhiteSpace($Model)) {
    $Model = [string]$config.ollama.model
}
if ([string]::IsNullOrWhiteSpace($Model)) {
    $Model = "gemma:7b"
}

$giannisPromptRel = [string]$config.agents.agent_1.prompt_file
$babisPromptRel = [string]$config.agents.agent_2.prompt_file
if ([string]::IsNullOrWhiteSpace($giannisPromptRel)) { $giannisPromptRel = [string]$config.ollama.agent_prompt_file }
if ([string]::IsNullOrWhiteSpace($babisPromptRel)) { $babisPromptRel = [string]$config.ollama.agent_prompt_file }

$giannisPromptPath = Resolve-PathSafe $giannisPromptRel
$babisPromptPath = Resolve-PathSafe $babisPromptRel

$giannisSystem = Load-TextFile -PathValue $giannisPromptPath -ErrorLabel "giannis prompt"
$babisSystem = Load-TextFile -PathValue $babisPromptPath -ErrorLabel "babis prompt"

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$runDir = Join-Path $outputRootResolved "workshop_$ts"
New-Item -ItemType Directory -Path $runDir -Force | Out-Null

$sourceForPrompt = $sourceText
if ($sourceForPrompt.Length -gt 18000) {
    $sourceForPrompt = $sourceForPrompt.Substring(0, 18000) + "`n`n[NOTE] source truncated for context window safety."
}

$promptInitial = @"
Read the training material below and write your summary in Greek.
Operational deadline: $DeadlineText.

Required structure:
1) 7 key ideas you understood
2) 3 ideas often misunderstood by investors
3) 5 rules we should apply in our pipeline
4) 3 open questions that need more study
5) Bottom line in 5 lines

Training material:
$sourceForPrompt
"@

$giannisInitial = Invoke-AgentResponse -AgentName "giannis" -SystemPrompt $giannisSystem -UserPrompt $promptInitial
$babisInitial = Invoke-AgentResponse -AgentName "babis" -SystemPrompt $babisSystem -UserPrompt $promptInitial

$r1 = Join-Path $runDir "01_giannis_initial_summary.md"
$r2 = Join-Path $runDir "02_babis_initial_summary.md"
Save-StageReport -PathValue $r1 -Title "Giannis - Initial Summary" -AgentName "giannis" -PromptText $promptInitial -ResponseText $giannisInitial
Save-StageReport -PathValue $r2 -Title "Babis - Initial Summary" -AgentName "babis" -PromptText $promptInitial -ResponseText $babisInitial

$promptGiannisAfter = @"
You just had a $MeetingMinutes-minute exchange of views.
Write your answer in Greek.
Operational deadline: $DeadlineText.

Your initial summary:
$giannisInitial

Babis initial summary:
$babisInitial

Required structure:
1) Where you agree
2) Where you disagree and why
3) What changed in your own view after discussion
4) What did not change
5) 5 shared action decisions for the pipeline
"@

$promptBabisAfter = @"
You just had a $MeetingMinutes-minute exchange of views.
Write your answer in Greek.
Operational deadline: $DeadlineText.

Your initial summary:
$babisInitial

Giannis initial summary:
$giannisInitial

Required structure:
1) Where you agree
2) Where you disagree and why
3) What changed in your own view after discussion
4) What did not change
5) 5 shared action decisions for the pipeline
"@

$giannisAfter = Invoke-AgentResponse -AgentName "giannis" -SystemPrompt $giannisSystem -UserPrompt $promptGiannisAfter
$babisAfter = Invoke-AgentResponse -AgentName "babis" -SystemPrompt $babisSystem -UserPrompt $promptBabisAfter

$r3 = Join-Path $runDir "03_giannis_post_discussion_report.md"
$r4 = Join-Path $runDir "04_babis_post_discussion_report.md"
Save-StageReport -PathValue $r3 -Title "Giannis - Post Discussion Report" -AgentName "giannis" -PromptText $promptGiannisAfter -ResponseText $giannisAfter
Save-StageReport -PathValue $r4 -Title "Babis - Post Discussion Report" -AgentName "babis" -PromptText $promptBabisAfter -ResponseText $babisAfter

$jointLines = @(
    "# Joint Meeting Report (Training Workshop)",
    "",
    "- Run timestamp: $(Get-Date -Format o)",
    "- Meeting duration (simulated): $MeetingMinutes minutes",
    "- Deadline: $DeadlineText",
    "- Model: $Model",
    "- Source file: $sourcePathResolved",
    "",
    "## Initial summaries",
    "",
    "- [Giannis initial](./01_giannis_initial_summary.md)",
    "- [Babis initial](./02_babis_initial_summary.md)",
    "",
    "## Post-discussion reports",
    "",
    "- [Giannis post discussion](./03_giannis_post_discussion_report.md)",
    "- [Babis post discussion](./04_babis_post_discussion_report.md)",
    "",
    "## What changed (self-reported)",
    "",
    "### Giannis",
    "",
    $giannisAfter,
    "",
    "### Babis",
    "",
    $babisAfter
)

$r5 = Join-Path $runDir "05_joint_meeting_report.md"
$jointLines | Set-Content -Path $r5 -Encoding UTF8

$indexLines = @(
    "# Workshop Reports Index",
    "",
    "- Run folder: $runDir",
    "- Generated: $(Get-Date -Format o)",
    "",
    "1. [01_giannis_initial_summary.md](./01_giannis_initial_summary.md)",
    "2. [02_babis_initial_summary.md](./02_babis_initial_summary.md)",
    "3. [03_giannis_post_discussion_report.md](./03_giannis_post_discussion_report.md)",
    "4. [04_babis_post_discussion_report.md](./04_babis_post_discussion_report.md)",
    "5. [05_joint_meeting_report.md](./05_joint_meeting_report.md)"
)
$indexPath = Join-Path $runDir "reports_index.md"
$indexLines | Set-Content -Path $indexPath -Encoding UTF8

$manifest = [ordered]@{
    generated_at = (Get-Date -Format o)
    run_dir = $runDir
    source_file = $sourcePathResolved
    model = $Model
    reports = @(
        $r1, $r2, $r3, $r4, $r5, $indexPath
    )
}
$manifestPath = Join-Path $runDir "reports_manifest.json"
$manifest | ConvertTo-Json -Depth 8 | Set-Content -Path $manifestPath -Encoding UTF8

$latestPtr = Join-Path $outputRootResolved "latest_run.txt"
$runDir | Set-Content -Path $latestPtr -Encoding UTF8

Write-Host "Workshop completed."
Write-Host "Run folder: $runDir"
Write-Host "Index: $indexPath"
Write-Host "Manifest: $manifestPath"
