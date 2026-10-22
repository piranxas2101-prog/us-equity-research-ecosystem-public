param(
    [string]$CheckpointPath = ".\output\babis_stage1_checkpoint.json",
    [string]$OutputDir = ".\output",
    [int]$PollSeconds = 30
)

$ErrorActionPreference = "Stop"

function Write-DoneFile {
    param(
        [pscustomobject]$Checkpoint,
        [string]$Dir
    )

    if (-not (Test-Path $Dir)) {
        New-Item -ItemType Directory -Path $Dir | Out-Null
    }

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $donePath = Join-Path $Dir "pipeline_done_$timestamp.txt"

    $lines = @(
        "Pipeline completed",
        "completed_at: $(Get-Date -Format o)",
        "checkpoint_timestamp: $($Checkpoint.run_timestamp)",
        "processed: $($Checkpoint.processed_count)/$($Checkpoint.symbols_total)",
        "success: $($Checkpoint.success_count)",
        "errors: $($Checkpoint.error_count)"
    )
    Set-Content -Path $donePath -Value $lines -Encoding UTF8
    return $donePath
}

function Try-NotifyUser {
    param(
        [string]$Message
    )

    try {
        msg.exe $env:USERNAME $Message | Out-Null
    } catch {
        # Best-effort notification only.
    }
}

while ($true) {
    if (-not (Test-Path $CheckpointPath)) {
        Start-Sleep -Seconds $PollSeconds
        continue
    }

    try {
        $checkpoint = Get-Content $CheckpointPath -Raw | ConvertFrom-Json
    } catch {
        Start-Sleep -Seconds $PollSeconds
        continue
    }

    if (
        $checkpoint.status -eq "complete" -and
        [int]$checkpoint.processed_count -ge [int]$checkpoint.symbols_total
    ) {
        $donePath = Write-DoneFile -Checkpoint $checkpoint -Dir $OutputDir
        Try-NotifyUser -Message "Pipeline completed. Done file: $donePath"
        break
    }

    Start-Sleep -Seconds $PollSeconds
}
