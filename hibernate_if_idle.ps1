# Hibernate the computer if nobody is using it - the one rule shared by the nightly run and the afternoon session.
# Hibernates only if: the switch scanner_config.json -> power.<Switch> is on, no process matching -WaitForPattern
# is still running (waits up to -WaitMinutes for them), the computer is on mains power, and nobody used keyboard or
# mouse for power.hibernate_idle_minutes. Otherwise it stays on and says why in the log. The scheduled tasks
# "Ecosystem Nightly Research" (01:30) and "Ecosystem Preopen Check" (16:00) wake the computer again.
param(
    [Parameter(Mandatory = $true)] [string]$LogPath,
    [Parameter(Mandatory = $true)] [string]$Reason,
    [string]$Switch = "hibernate_after_nightly",
    [string]$WaitForPattern = "",
    [int]$WaitMinutes = 60,
    [switch]$DryRun  # decide and log, but do not hibernate (testing)
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Log([string]$Text) {
    "$((Get-Date).ToString('s')) [hibernate] $Text" | Add-Content -LiteralPath $LogPath -Encoding UTF8
}

if (-not ("Ecosystem.Idle" -as [type])) {
    Add-Type -Namespace Ecosystem -Name Idle -MemberDefinition @"
[StructLayout(LayoutKind.Sequential)] public struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
[DllImport("user32.dll")] public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
public static double Minutes() {
    LASTINPUTINFO info = new LASTINPUTINFO();
    info.cbSize = (uint)Marshal.SizeOf(info);
    if (!GetLastInputInfo(ref info)) { return -1; }
    return ((uint)Environment.TickCount - info.dwTime) / 60000.0;
}
"@
}

$config = Get-Content -LiteralPath (Join-Path $projectRoot "scanner_config.json") -Raw | ConvertFrom-Json
$power = $config.power
if ($null -eq $power -or -not $power.$Switch) {
    Write-Log "Switch power.$Switch is off; staying on ($Reason)."
    exit 0
}
$idleNeeded = 15
if ($null -ne $power.hibernate_idle_minutes) { $idleNeeded = [double]$power.hibernate_idle_minutes }

# Never while a research run is active (2026-10-14: the afternoon would otherwise have hibernated a re-run).
try {
    $run = Get-Content -LiteralPath (Join-Path $projectRoot "output\full_research_control_plane_background_latest.json") -Raw | ConvertFrom-Json
    $terminal = @("completed", "completed_review_required", "failed", "stopped_by_owner", "interrupted")
    if ($run.status -notin $terminal -and $run.pid -and (Get-Process -Id ([int]$run.pid) -ErrorAction SilentlyContinue)) {
        Write-Log "Research run $($run.run_id) is still active (PID $($run.pid)); staying on ($Reason)."
        exit 0
    }
} catch { }

if ($WaitForPattern) {
    $deadline = (Get-Date).AddMinutes($WaitMinutes)
    while ($true) {
        $busy = @(Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match $WaitForPattern })
        if ($busy.Count -eq 0) { break }
        if ((Get-Date) -gt $deadline) {
            Write-Log "$($busy.Count) process(es) matching '$WaitForPattern' still running after $WaitMinutes minute(s); staying on."
            exit 0
        }
        Start-Sleep -Seconds 60
    }
}

Add-Type -AssemblyName System.Windows.Forms
$line = [System.Windows.Forms.SystemInformation]::PowerStatus.PowerLineStatus
if ($line -ne [System.Windows.Forms.PowerLineStatus]::Online) {
    Write-Log "Not on mains power ($line); staying on (the scheduled tasks only start on mains power)."
    exit 0
}
$idle = [Ecosystem.Idle]::Minutes()
if ($idle -lt $idleNeeded) {
    Write-Log ("Someone used the computer {0:N0} minute(s) ago (need {1}); staying on." -f $idle, $idleNeeded)
    exit 0
}
Write-Log ("$Reason; idle {0:N0} minute(s). Hibernating." -f $idle)
if ($DryRun) { Write-Log "Dry run: not hibernating."; exit 0 }
& shutdown.exe /h
