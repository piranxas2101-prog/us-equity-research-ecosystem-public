$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$pythonPath = Join-Path $projectRoot ".venv-next\Scripts\python.exe"
$statePath = Join-Path $projectRoot "output\technical_validation_state_latest.json"
try {
    & $pythonPath ".\run_technical_validation.py"
    $workerExit = $LASTEXITCODE
    if ($workerExit -ne 0 -and (Test-Path -LiteralPath $statePath)) {
        $current = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        if ($current.status -eq "running") {
            $current.status = "failed"
            $current | Add-Member -NotePropertyName finished_at -NotePropertyValue (Get-Date -Format o) -Force
            $current | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $statePath -Encoding UTF8
        }
    }
} catch {
    $workerExit = 1
    [ordered]@{
        schema = "ecosystem.technical_validation_run.v1"
        status = "failed"
        finished_at = (Get-Date -Format o)
        reason = $_.Exception.Message
        research_only = $true
        paper_submission = $false
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding UTF8
}
try {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $notification = New-Object System.Windows.Forms.NotifyIcon
    $notification.Icon = [System.Drawing.SystemIcons]::Information
    $notification.Visible = $true
    $notification.BalloonTipTitle = "Technical validation finished"
    $notification.BalloonTipText = "Status: $($state.status). Report: output/technical_validation_latest.md"
    $notification.ShowBalloonTip(12000)
    Start-Sleep -Seconds 14
    $notification.Dispose()
} catch { try { [console]::beep(988, 700) } catch {} }
exit $workerExit
