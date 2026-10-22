"""Bounded, on-demand security checks for the local research ecosystem.

This module never changes Windows security settings, runs a service, or reads
credential values into its report. Scanners run only when explicitly requested.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import site
import subprocess
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_REPORT_DIRECTORY = ROOT / "output" / "security"
TOOL_PACKAGES = {
    "gitleaks": "Gitleaks.Gitleaks",
    "trivy": "AquaSecurity.Trivy",
    "syft": "Anchore.Syft",
    "grype": "Anchore.Grype",
    "zizmor": "zizmor.zizmor",
}
SOURCE_SUFFIXES = {".py", ".ps1", ".psm1", ".cmd", ".json", ".json5",
                   ".yaml", ".yml", ".toml", ".md", ".txt", ".ini", ".xml"}
EXCLUDED_DIRECTORIES = {".git", ".venv", ".architecture-venv", "venv", "env",
                        "data", "output", "logs", "cache", "models", "__pycache__"}
SENSITIVE_NAMES = {".env", "id_rsa", "id_ed25519", "credentials.json", "secrets.json"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".sqlite3", ".db"}


def _tool_path(name: str) -> str | None:
    executable = shutil.which(name)
    if executable:
        return executable
    if os.name == "nt" and name in TOOL_PACKAGES:
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        package_dir = local / "Microsoft" / "WinGet" / "Packages"
        for package in sorted(package_dir.glob(TOOL_PACKAGES[name] + "_*")):
            candidate = package / (name + ".exe")
            if candidate.is_file():
                return str(candidate)
    return None


def _pip_audit_path(root: Path) -> str | None:
    override = os.environ.get("ECOSYSTEM_PIP_AUDIT")
    candidates = [Path(override)] if override else []
    candidates.extend([root / ".security-venv" / "Scripts" / "pip-audit.exe",
                       root / ".security-venv" / "bin" / "pip-audit"])
    return next((str(path) for path in candidates if path.is_file()),
                shutil.which("pip-audit"))


def _run(args: list[str], *, cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          check=False, shell=False)


def _git_paths(root: Path) -> list[Path]:
    result = subprocess.run(["git", "ls-files", "-z", "--cached", "--others",
                             "--exclude-standard"], cwd=root, capture_output=True,
                            check=False, timeout=30)
    if result.returncode:
        return []
    paths: list[Path] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        relative = Path(os.fsdecode(raw))
        if relative.is_absolute() or ".." in relative.parts:
            continue
        paths.append(relative)
    return sorted(set(paths))


def _source_paths(root: Path) -> list[Path]:
    result = []
    for relative in _git_paths(root):
        if any(part.lower() in EXCLUDED_DIRECTORIES for part in relative.parts[:-1]):
            continue
        source = root / relative
        if ((relative.suffix.lower() in SOURCE_SUFFIXES
                or relative.name.lower() == ".env.example") and source.is_file()
                and source.stat().st_size <= 2_000_000):
            result.append(relative)
    return result


def _tracked_sensitive_count(root: Path) -> int:
    result = _run(["git", "ls-files", "-z"], cwd=root, timeout=30)
    if result.returncode:
        return 0
    count = 0
    for name in result.stdout.split("\0"):
        if not name:
            continue
        path = Path(name)
        if path.name.lower() in SENSITIVE_NAMES or path.suffix.lower() in SENSITIVE_SUFFIXES:
            count += 1
    return count


def _windows_state() -> dict[str, Any]:
    if os.name != "nt":
        return {"available": False, "reason": "windows_only"}
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$osInfo = Get-CimInstance Win32_OperatingSystem
$profiles = @(Get-NetFirewallProfile | ForEach-Object { [pscustomobject]@{name=$_.Name;enabled=[bool]$_.Enabled} })
$defender = Get-MpComputerStatus
$listeners = @(Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in @(11434,18789) } | ForEach-Object { [pscustomobject]@{address=$_.LocalAddress;port=$_.LocalPort} })
$agentAccount = Get-LocalUser -Name 'OpenClawAgent' -ErrorAction SilentlyContinue
$administrators = @(Get-LocalGroupMember -SID 'S-1-5-32-544' -ErrorAction SilentlyContinue | ForEach-Object { $_.SID.Value })
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$nodeVersion = if($nodeCommand){(& $nodeCommand.Source --version 2>$null | Select-Object -First 1)}else{$null}
$openclawCommand = Get-Command openclaw -ErrorAction SilentlyContinue
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
& wsl.exe --list --quiet 2>$null | Out-Null
$wslDistroAvailable = $LASTEXITCODE -eq 0
[pscustomobject]@{
  os=$osInfo.Caption
  version=$osInfo.Version
  ram_gb=[math]::Round($osInfo.TotalVisibleMemorySize / 1MB,1)
  free_ram_gb=[math]::Round($osInfo.FreePhysicalMemory / 1MB,1)
  firewall_profiles=$profiles
  defender_enabled=if($null -ne $defender){[bool]$defender.AntivirusEnabled}else{$null}
  realtime_protection=if($null -ne $defender){[bool]$defender.RealTimeProtectionEnabled}else{$null}
  selected_listeners=$listeners
  isolated_agent_account_present=[bool]$agentAccount
  isolated_agent_is_standard=if($agentAccount){$administrators -notcontains $agentAccount.SID.Value}else{$null}
  node_version=$nodeVersion
  openclaw_cli_present=[bool]$openclawCommand
  docker_command_present=[bool]$dockerCommand
  wsl_distro_available=$wslDistroAvailable
} | ConvertTo-Json -Depth 5 -Compress
"""
    try:
        result = _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                      cwd=ROOT, timeout=45)
        if result.returncode or not result.stdout.strip():
            return {"available": False, "reason": "host_probe_unavailable"}
        return {"available": True, **json.loads(result.stdout.strip())}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {"available": False, "reason": "host_probe_unavailable"}


def _node_compatible(version: str | None) -> bool:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(version or "").strip())
    if not match:
        return False
    major, minor, _ = map(int, match.groups())
    return (major == 24 and minor >= 16) or (major == 26 and minor >= 1)


def preflight(root: Path) -> dict[str, Any]:
    host = _windows_state()
    unsafe = [item for item in host.get("selected_listeners", [])
              if item.get("address") in {"0.0.0.0", "::", "*"}]
    host_controls_present = bool(
        host.get("available") and host.get("defender_enabled")
        and host.get("realtime_protection")
        and host.get("firewall_profiles")
        and all(profile.get("enabled") for profile in host["firewall_profiles"])
        and not unsafe
    )
    native_prerequisites = bool(
        host_controls_present and host.get("isolated_agent_account_present")
        and host.get("isolated_agent_is_standard")
        and _node_compatible(host.get("node_version"))
    )
    wsl_prerequisites = bool(host_controls_present and host.get("wsl_distro_available"))
    return {
        "schema": "ecosystem.security_preflight.v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "host": host,
        "tool_available": {name: bool(_tool_path(name)) for name in TOOL_PACKAGES},
        "node_compatible_with_openclaw_cli": _node_compatible(host.get("node_version")),
        "tracked_sensitive_filename_count": _tracked_sensitive_count(root),
        "wildcard_local_agent_listeners": unsafe,
        "native_cli_prerequisites_present": native_prerequisites,
        "wsl_gateway_prerequisites_present": wsl_prerequisites,
        "openclaw_prerequisites_present": native_prerequisites or wsl_prerequisites,
        "openclaw_isolation_ready": False,
        "limitations": [
            "OpenClaw isolation cannot be declared ready until account privileges and workspace ACLs are verified.",
            "A WSL2 Gateway or Windows Hub may have different runtime requirements; this Node check applies to the CLI.",
            "Tool availability does not imply that a scan passed.",
            "Only selected local service ports are checked; this is not a full port audit.",
        ],
    }


def _parse_findings(output: str) -> list[dict[str, Any]]:
    value = json.loads(output.strip() or "[]")
    if not isinstance(value, list):
        raise ValueError("Expected a JSON array of scanner findings")
    return [item for item in value if isinstance(item, dict)]


def scan_secrets(root: Path) -> dict[str, Any]:
    """Scan committed history and current source; never persist raw findings."""
    executable = _tool_path("gitleaks")
    if not executable:
        return {"status": "unavailable", "tool": "gitleaks"}
    common = [executable, "--no-banner", "--redact=100", "--report-format", "json",
              "--report-path", "-", "--timeout", "300"]
    git_result = _run([executable, "git", *common[1:], str(root)], cwd=root, timeout=330)
    if git_result.returncode not in {0, 1}:
        return {"status": "error", "stage": "history", "exit_code": git_result.returncode}
    try:
        git_findings = _parse_findings(git_result.stdout)
    except (json.JSONDecodeError, ValueError):
        return {"status": "error", "stage": "history", "reason": "invalid_report"}

    source_paths = _source_paths(root)
    with tempfile.TemporaryDirectory(prefix="ecosystem_security_") as temp:
        staging = Path(temp)
        for relative in source_paths:
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / relative, target)
        working_result = _run([executable, "dir", *common[1:], str(staging)],
                              cwd=root, timeout=330)
    if working_result.returncode not in {0, 1}:
        return {"status": "error", "stage": "working_tree",
                "exit_code": working_result.returncode}
    try:
        working_findings = _parse_findings(working_result.stdout)
    except (json.JSONDecodeError, ValueError):
        return {"status": "error", "stage": "working_tree", "reason": "invalid_report"}
    rules = Counter(str(item.get("RuleID") or "unknown")
                    for item in git_findings + working_findings)
    return {"status": "findings" if rules else "pass", "tool": "gitleaks",
            "history_finding_count": len(git_findings),
            "working_tree_finding_count": len(working_findings),
            "source_files_checked": len(source_paths),
            "finding_rules": dict(sorted(rules.items())),
            "raw_secrets_persisted": False}


def scan_dependencies(root: Path) -> dict[str, Any]:
    executable = _tool_path("trivy")
    if not executable:
        return {"status": "unavailable", "tool": "trivy"}
    command = [executable, "fs", "--scanners", "vuln,misconfig", "--format", "json",
               "--quiet", "--no-progress", "--timeout", "10m"]
    for directory in sorted(EXCLUDED_DIRECTORIES):
        command.extend(["--skip-dirs", str(root / directory)])
    result = _run([*command, str(root)], cwd=root, timeout=660)
    if result.returncode:
        return {"status": "error", "tool": "trivy", "exit_code": result.returncode,
                "reason": "scan_or_database_unavailable"}
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"status": "error", "tool": "trivy", "reason": "invalid_report"}
    severities: Counter[str] = Counter()
    misconfigurations = 0
    targets = report.get("Results") or []
    for target in targets:
        for finding in target.get("Vulnerabilities") or []:
            severities[str(finding.get("Severity") or "UNKNOWN")] += 1
        misconfigurations += len(target.get("Misconfigurations") or [])
    status = ("no_inventory" if not targets else
              "findings" if severities or misconfigurations else "pass")
    return {"status": status,
            "tool": "trivy", "vulnerability_count": sum(severities.values()),
            "vulnerabilities_by_severity": dict(sorted(severities.items())),
            "misconfiguration_count": misconfigurations, "target_count": len(targets)}


def scan_python_runtime(root: Path) -> dict[str, Any]:
    """Audit installed packages of the invoking Python, without changing them."""
    executable = _pip_audit_path(root)
    if not executable:
        return {"status": "unavailable", "tool": "pip-audit"}
    package_paths = [Path(path) for path in site.getsitepackages()
                     if Path(path).name.lower() == "site-packages" and Path(path).is_dir()]
    if not package_paths:
        return {"status": "no_inventory", "tool": "pip-audit"}
    command = [executable]
    for path in package_paths:
        command.extend(["--path", str(path)])
    command.extend(["--format", "json", "--desc", "off",
                    "--progress-spinner", "off", "--timeout", "15"])
    try:
        result = _run(command, cwd=root, timeout=900)
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "error", "tool": "pip-audit", "reason": "audit_unavailable"}
    if result.returncode not in {0, 1}:
        return {"status": "error", "tool": "pip-audit", "exit_code": result.returncode}
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"status": "error", "tool": "pip-audit", "reason": "invalid_report"}
    packages = report.get("dependencies") or []
    affected = sorted(({"name": str(package.get("name") or "unknown"),
                        "version": str(package.get("version") or "unknown"),
                        "vulnerability_count": len(package.get("vulns") or [])}
                       for package in packages if package.get("vulns")),
                      key=lambda item: (-item["vulnerability_count"], item["name"]))
    finding_count = sum(item["vulnerability_count"] for item in affected)
    return {"status": "findings" if affected else "pass", "tool": "pip-audit",
            "python_executable": str(Path(os.sys.executable).resolve()),
            "package_count": len(packages), "affected_package_count": len(affected),
            "vulnerability_count": finding_count, "affected_packages": affected,
            "raw_descriptions_persisted": False}


def scan_workflows(root: Path) -> dict[str, Any]:
    """Run zizmor offline; return counts without raw workflow findings."""
    executable = _tool_path("zizmor")
    if not executable:
        return {"status": "unavailable", "tool": "zizmor"}
    workflow_dir = root / ".github" / "workflows"
    if not workflow_dir.is_dir():
        return {"status": "no_inventory", "tool": "zizmor"}
    result = _run([executable, "--offline", "--format", "json", "--no-progress",
                   "--no-exit-codes", str(workflow_dir)], cwd=root, timeout=120)
    if result.returncode:
        return {"status": "error", "tool": "zizmor", "exit_code": result.returncode}
    try:
        findings = _parse_findings(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return {"status": "error", "tool": "zizmor", "reason": "invalid_report"}
    by_rule = Counter(str(item.get("ident") or "unknown") for item in findings)
    by_severity = Counter(str((item.get("determinations") or {}).get("severity") or "Unknown")
                          for item in findings)
    return {"status": "findings" if findings else "pass", "tool": "zizmor",
            "finding_count": len(findings), "by_rule": dict(sorted(by_rule.items())),
            "by_severity": dict(sorted(by_severity.items()))}


def create_sbom(root: Path, report_dir: Path) -> dict[str, Any]:
    syft = _tool_path("syft")
    grype = _tool_path("grype")
    if not syft or not grype:
        return {"status": "unavailable", "tools": ["syft", "grype"]}
    report_dir.mkdir(parents=True, exist_ok=True)
    sbom = report_dir / "ecosystem.sbom.syft.json"
    command = [syft, "scan", "dir:" + str(root), "--quiet", "--parallelism", "2",
               "--output", "syft-json=" + str(sbom)]
    for directory in sorted(EXCLUDED_DIRECTORIES):
        command.extend(["--exclude", f"./{directory}/**"])
    generated = _run(command, cwd=root, timeout=600)
    if generated.returncode or not sbom.is_file():
        return {"status": "error", "stage": "sbom_generation",
                "exit_code": generated.returncode}
    try:
        package_count = len(json.loads(sbom.read_text(encoding="utf-8")).get("artifacts") or [])
    except (OSError, json.JSONDecodeError):
        return {"status": "error", "stage": "sbom_generation",
                "reason": "invalid_report"}
    scanned = _run([grype, "sbom:" + str(sbom), "--output", "json", "--quiet"],
                   cwd=root, timeout=600)
    if scanned.returncode:
        return {"status": "error", "stage": "sbom_vulnerability_scan",
                "exit_code": scanned.returncode, "sbom_path": str(sbom)}
    try:
        report = json.loads(scanned.stdout)
    except json.JSONDecodeError:
        return {"status": "error", "stage": "sbom_vulnerability_scan",
                "reason": "invalid_report", "sbom_path": str(sbom)}
    severities = Counter(str(match.get("vulnerability", {}).get("severity") or "Unknown")
                         for match in report.get("matches") or [])
    status = "no_inventory" if package_count == 0 else "findings" if severities else "pass"
    return {"status": status, "tools": ["syft", "grype"],
            "sbom_path": str(sbom), "package_count": package_count,
            "vulnerability_count": sum(severities.values()),
            "vulnerabilities_by_severity": dict(sorted(severities.items()))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One-shot local security checks")
    parser.add_argument("command", choices=("preflight", "secrets", "dependencies",
                                            "python-runtime", "workflows", "sbom", "all"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve(strict=True)
    report = (args.report or DEFAULT_REPORT_DIRECTORY / f"{args.command}_latest.json").resolve()
    result: dict[str, Any] = {"schema": "ecosystem.security_audit.v1",
                              "generated_at": datetime.now(timezone.utc).isoformat(),
                              "root": str(root), "checks": {}}
    if args.command in {"preflight", "all"}:
        result["checks"]["preflight"] = preflight(root)
    if args.command in {"secrets", "all"}:
        result["checks"]["secrets"] = scan_secrets(root)
    if args.command in {"dependencies", "all"}:
        result["checks"]["dependencies"] = scan_dependencies(root)
    if args.command in {"python-runtime", "all"}:
        result["checks"]["python_runtime"] = scan_python_runtime(root)
    if args.command in {"workflows", "all"}:
        result["checks"]["workflows"] = scan_workflows(root)
    if args.command in {"sbom", "all"}:
        result["checks"]["sbom"] = create_sbom(root, report.parent)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    statuses = [check.get("status") for check in result["checks"].values()]
    if any(status in {"error", "unavailable", "no_inventory", "findings"}
           for status in statuses):
        return 1
    if ("preflight" in result["checks"]
            and not result["checks"]["preflight"]["openclaw_isolation_ready"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
