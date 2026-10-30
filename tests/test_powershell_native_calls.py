"""Scheduled PowerShell scripts must survive a warning on stderr (2026-10-13: one yfinance warning ended the afternoon
session at 16:00:01, no check, no orders, no notification).

Windows PowerShell 5.1 turns every stderr line of a native program into an error record when its output is
redirected, and with $ErrorActionPreference = "Stop" the first one ends the script. Every redirected call in the
scripts that run unattended must therefore go through Invoke-Logged, which runs it with "Continue".
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
UNATTENDED = ("nightly_research.ps1", "start_trader_afternoon_session.ps1", "hibernate_after_run.ps1",
              "hibernate_if_idle.ps1", "start_multi_day_monitor_today.ps1", "fallback_research_on_boot.ps1",
              "register_fallback_research_task.ps1")
REDIRECT = re.compile(r"(\*>&1|2>&1)")
ALLOWED = "& $Block *>&1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")


class StaticTests(unittest.TestCase):
    def test_redirected_calls_only_inside_invoke_logged(self):
        for name in UNATTENDED:
            for number, line in enumerate((ROOT / name).read_text(encoding="utf-8-sig").splitlines(), 1):
                code = line.split("#", 1)[0]
                if REDIRECT.search(code):
                    with self.subTest(script=name, line=number):
                        self.assertIn(ALLOWED, code, f"{name}:{number} redirects a call outside Invoke-Logged")

    def test_scripts_that_redirect_define_invoke_logged_with_continue(self):
        for name in ("nightly_research.ps1", "start_trader_afternoon_session.ps1"):
            text = (ROOT / name).read_text(encoding="utf-8-sig")
            body = text[text.index("function Invoke-Logged"):]
            self.assertIn('$ErrorActionPreference = "Continue"', body.split("}", 1)[0], name)

    def test_unattended_scripts_are_ascii(self):
        for name in UNATTENDED:
            self.assertTrue(all(b < 128 for b in (ROOT / name).read_bytes()), f"{name} has non-ASCII bytes")


@unittest.skipUnless(POWERSHELL and sys.platform == "win32", "needs Windows PowerShell")
class ExecutionTests(unittest.TestCase):
    def test_scripts_parse(self):
        for name in UNATTENDED:
            command = ("$e=$null; [System.Management.Automation.Language.Parser]::ParseFile("
                       f"'{ROOT / name}', [ref]$null, [ref]$e) | Out-Null; $e.Count")
            out = subprocess.run([POWERSHELL, "-NoProfile", "-Command", command], capture_output=True, text=True,
                                 timeout=60)
            with self.subTest(script=name):
                self.assertEqual(out.stdout.strip(), "0", out.stdout + out.stderr)

    def test_invoke_logged_survives_stderr_and_returns_the_exit_code(self):
        text = (ROOT / "start_trader_afternoon_session.ps1").read_text(encoding="utf-8-sig")
        start = text.index("function Invoke-Logged")
        function = text[start:text.index("\n}\n", start) + 3]
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "noisy.py").write_text("import sys\nprint('out')\nsys.stderr.write('warning\\n')\nsys.exit(3)\n",
                                             encoding="utf-8")
            log = folder / "run.log"
            (folder / "harness.ps1").write_text(
                f'$ErrorActionPreference = "Stop"\n$log = "{log}"\n{function}\n'
                f'$code = Invoke-Logged {{ & "{sys.executable}" "{folder / "noisy.py"}" }}\n'
                '"after $code" | Add-Content -LiteralPath $log\n', encoding="utf-8")
            result = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                     str(folder / "harness.ps1")], capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            logged = log.read_text(encoding="utf-8-sig")
            self.assertIn("warning", logged)
            self.assertIn("after 3", logged)

    def test_after_close_steps_retry_only_a_failed_start(self):
        # 2026-10-19: the report, ledger and evaluation all ended with 0xC0000142 (Windows could not start them)
        text = (ROOT / "start_trader_afternoon_session.ps1").read_text(encoding="utf-8-sig")
        for step in ("paper_session_report.py", "paper_trade_ledger.py", "paper_learning.py"):
            line = next(x for x in text.splitlines() if step in x and "& $python" in x)
            self.assertIn("Invoke-Retried", line, step)
        pieces = []
        for marker in ("function Write-Log", "function Invoke-Logged", "$startFailure =", "function Invoke-Retried"):
            start = text.index(marker)
            end = text.index("\n", start) + 1 if marker.startswith("$") else text.index("\n}\n", start) + 3
            pieces.append(text[start:end])
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            counter = folder / "count.txt"
            (folder / "flaky.py").write_text(
                "import ctypes, pathlib, sys\n"
                f"p = pathlib.Path(r'{counter}')\n"
                "n = int(p.read_text()) + 1 if p.exists() else 1\n"
                "p.write_text(str(n))\n"
                "ctypes.windll.kernel32.ExitProcess(0xC0000142) if n < 3 else sys.exit(0)\n", encoding="utf-8")
            (folder / "other.py").write_text("import sys\nsys.exit(2)\n", encoding="utf-8")
            log = folder / "run.log"
            (folder / "harness.ps1").write_text(
                f'$ErrorActionPreference = "Stop"\n$log = "{log}"\nfunction Start-Sleep {{ }}\n' + "".join(pieces) +
                f'$a = Invoke-Retried "Flaky" {{ & "{sys.executable}" "{folder / "flaky.py"}" }}\n'
                f'$b = Invoke-Retried "Other" {{ & "{sys.executable}" "{folder / "other.py"}" }}\n'
                '"result $a $b" | Add-Content -LiteralPath $log\n', encoding="utf-8")
            result = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                     str(folder / "harness.ps1")], capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            logged = log.read_text(encoding="utf-8-sig")
            self.assertIn("result 0 2", logged)                  # third attempt worked; another code is final
            self.assertEqual(counter.read_text(), "3")
            self.assertEqual(logged.count("could not start (0xC0000142)"), 2)


CHILD = """param([string]$PythonExe, [int]$PollSeconds = 30)
Set-Content -Path (Join-Path $PSScriptRoot "seen.txt") -Value "$PythonExe|$PollSeconds"
"""
PARENT = """$PythonExe = Join-Path $PSScriptRoot ".venv-next\\Scripts\\python.exe"
$list = @("-ExecutionPolicy", "Bypass", "-File", ".\\child.ps1", "-PythonExe", ('"{0}"' -f $PythonExe))
$p = Start-Process -FilePath "powershell.exe" -ArgumentList $list -WorkingDirectory $PSScriptRoot -PassThru -Wait -WindowStyle Hidden
exit $p.ExitCode
"""


class StartProcessPathTests(unittest.TestCase):
    """2026-10-27: Start-Process joins -ArgumentList with spaces, so a path with a space ("New project") passed
    unquoted to a child PowerShell script split into two arguments - the reconciliation waiter failed every session."""

    def test_python_path_handed_to_a_child_script_is_quoted(self):
        for path in ROOT.glob("*.ps1"):
            for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
                if re.search(r'"-PythonExe",\s*\$PythonExe', line.split("#", 1)[0]):
                    with self.subTest(script=path.name, line=number):
                        self.fail(f"{path.name}:{number} passes $PythonExe unquoted through Start-Process")

    @unittest.skipUnless(POWERSHELL, "Windows PowerShell not available")
    def test_quoted_path_with_a_space_reaches_the_child_as_one_parameter(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder) / "New project"
            base.mkdir()
            (base / "child.ps1").write_text(CHILD, encoding="utf-8")
            (base / "parent.ps1").write_text(PARENT, encoding="utf-8")
            done = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                   str(base / "parent.ps1")], capture_output=True, text=True, timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr)
            seen = (base / "seen.txt").read_text(encoding="utf-8-sig").strip()
        self.assertEqual(seen, str(base / ".venv-next" / "Scripts" / "python.exe") + "|30")



@unittest.skipUnless(POWERSHELL and sys.platform == "win32", "needs Windows PowerShell")
class FallbackSessionTests(unittest.TestCase):
    """2026-10-30 (owner): when today's nightly run did not complete, the fallback research run stands in for it in
    the afternoon session; a fallback still going, failed, from another day or switched off never does."""

    def pointer(self, state: dict | None, *, record_date_offset: int = 0, eastern_hour: int = 9,
                nightly_done: bool = False, switch: str = "on") -> dict:
        text = (ROOT / "start_trader_afternoon_session.ps1").read_text(encoding="utf-8-sig")
        wait = text[text.index("    # 0b. Fallback research"):text.index("    # 1. Pre-open check")]
        late = "$recordDeadline = (Get-Date).AddMinutes(10)"
        self.assertIn(late, wait)
        wait = wait.replace(late, "$recordDeadline = (Get-Date).AddSeconds(-1)")  # no record: the 10 minutes are over
        start = text.index("\n    if ($null -ne $fallbackState -and [string]") + 1
        stand_in = text[start:text.index("\n    }\n", start) + 7]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "output").mkdir()
            (root / "scanner_config.json").write_text(
                json.dumps({"paper_session": {"fallback_research": switch}}), encoding="utf-8")
            (root / "output" / "nightly.json").write_text(json.dumps(
                {"run_id": "nightly", "status": "completed_review_required" if nightly_done else "failed",
                 "finished_at": "FINISHED"}), encoding="utf-8")
            (root / "output" / "fallback_research_latest.json").write_text(json.dumps(
                {"date": "DATE", "run_id": "run_x", "reason": "test", "status": "running"}), encoding="utf-8")
            if state is not None:
                (root / "output" / "pipeline_runs" / "run_x").mkdir(parents=True)
                (root / "output" / "pipeline_runs" / "run_x" / "pipeline_state.json").write_text(
                    json.dumps({"run_id": "run_x", **state}), encoding="utf-8")
            harness = (
                '$ErrorActionPreference = "Stop"\n'
                f'$projectRoot = "{root}"\n'
                '$log = Join-Path $projectRoot "run.log"\n'
                '$pointerPath = Join-Path $projectRoot "output/nightly.json"\n'
                'function Write-Log([string]$Text) { $Text | Add-Content -LiteralPath $log }\n'
                'function Show-Notice($a, $b) { }\n'
                '$script:sleeps = 0\n'
                'function Start-Sleep { $script:sleeps++; if ($script:sleeps -gt 20) { throw "waited too long" } }\n'
                f'function Get-Eastern {{ (Get-Date).Date.AddHours({eastern_hour}) }}\n'
                '$r = Join-Path $projectRoot "output/fallback_research_latest.json"\n'
                f'(Get-Content $r -Raw).Replace("DATE", (Get-Date).AddDays({record_date_offset}).ToString("yyyy-MM-dd")) | Set-Content $r\n'
                '(Get-Content $pointerPath -Raw).Replace("FINISHED", (Get-Date).ToString("o")) | Set-Content $pointerPath\n'
                '$pointer = [pscustomobject]@{ run_id = "nightly"; status = "failed"; finished_at = "" }\n'
                + wait + stand_in +
                '$pointer | ConvertTo-Json -Compress\n')
            (root / "h.ps1").write_text(harness, encoding="utf-8")
            done = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / "h.ps1")],
                                  capture_output=True, text=True, errors="replace", timeout=120)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            return json.loads(done.stdout.strip().splitlines()[-1])

    def test_finished_fallback_stands_in_for_the_nightly_pointer(self):
        got = self.pointer({"status": "completed_with_warnings", "finished_at": "2026-10-30T12:00:00+00:00"})
        self.assertEqual((got["run_id"], got["status"]), ("run_x", "completed_review_required"))

    def test_a_failed_or_still_running_fallback_keeps_orders_off(self):
        self.assertEqual(self.pointer({"status": "failed"})["status"], "failed")
        self.assertEqual(self.pointer({"status": "running"}, eastern_hour=14)["status"], "running")

    def test_completed_nightly_another_day_or_switch_off_leave_the_nightly_pointer(self):
        done = {"status": "completed"}
        self.assertEqual(self.pointer(done, nightly_done=True)["run_id"], "nightly")
        self.assertEqual(self.pointer(done, record_date_offset=-1)["run_id"], "nightly")
        self.assertEqual(self.pointer(done, switch="off")["run_id"], "nightly")

if __name__ == "__main__":
    unittest.main()
