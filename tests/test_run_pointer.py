import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"


@unittest.skipUnless(os.name == "nt" and os.path.exists(POWERSHELL), "Windows PowerShell 5.1 is required")
class RunPointerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.pointer = self.dir / "pointer.json"
        self.timings = self.dir / "timings.json"
        self.sleepers = []
        self.addCleanup(self.stop_sleepers)

    def stop_sleepers(self):
        for process in self.sleepers:
            process.kill()
            process.wait()

    def write(self, **fields):
        state = {"run_id": "run_a", "status": "running_pipeline", "pid": 999999,
                 "started_at": datetime.now().astimezone().isoformat()}
        state.update(fields)
        self.pointer.write_text(json.dumps(state), encoding="utf-8")

    def call(self, command):
        script = f". '{ROOT / 'run_pointer.ps1'}'; {command}"
        result = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def interrupt(self):
        return self.call(f"$r = Set-RunInterrupted -PointerPath '{self.pointer}' -TimingsPath '{self.timings}'; "
                         "if ($null -eq $r) { 'none' } else { $r.status }")

    def state(self):
        return json.loads(self.pointer.read_text(encoding="utf-8-sig"))

    def sleeper(self):
        process = subprocess.Popen([POWERSHELL, "-NoProfile", "-Command", "Start-Sleep 120"])
        self.sleepers.append(process)
        return process

    def test_a_run_whose_process_is_gone_is_marked_interrupted_with_its_last_step(self):
        self.timings.write_text(json.dumps({"run_id": "run_a", "current_step": "control_plane_batch"}), encoding="utf-8")
        self.write()
        self.assertEqual(self.interrupt(), "interrupted")
        state = self.state()
        self.assertEqual((state["status"], state["last_step"]), ("interrupted", "control_plane_batch"))
        self.assertIn("finished_at", state)
        self.assertIn("process is gone", state["error"])

    def test_the_last_step_is_not_taken_from_another_runs_timings(self):
        self.timings.write_text(json.dumps({"run_id": "other", "current_step": "yankos"}), encoding="utf-8")
        self.write()
        self.interrupt()
        self.assertIsNone(self.state()["last_step"])

    def test_a_live_powershell_started_with_the_run_is_left_alone(self):
        process = self.sleeper()
        self.write(pid=process.pid)  # started_at = now, i.e. just after the process started
        self.assertEqual(self.interrupt(), "none")
        self.assertEqual(self.state()["status"], "running_pipeline")

    def test_a_reused_pid_is_not_mistaken_for_the_run(self):
        process = self.sleeper()
        # the pointer claims the run began long before this PowerShell existed: the PID was reused after a restart
        self.write(pid=process.pid, started_at=(datetime.now().astimezone() - timedelta(hours=3)).isoformat())
        self.assertEqual(self.interrupt(), "interrupted")

    def test_a_live_process_that_is_not_powershell_is_not_the_run(self):
        self.write(pid=os.getpid())  # this test runs in python.exe
        self.assertEqual(self.interrupt(), "interrupted")

    def test_finished_runs_and_missing_pointers_are_never_touched(self):
        for status in ("completed", "completed_review_required", "failed", "stopped_by_owner", "interrupted"):
            self.write(status=status)
            self.assertEqual(self.interrupt(), "none", status)
            self.assertEqual(self.state()["status"], status)
        self.pointer.unlink()
        self.assertEqual(self.interrupt(), "none")


@unittest.skipUnless(os.name == "nt" and os.path.exists(POWERSHELL), "Windows PowerShell 5.1 is required")
class LauncherScriptTests(unittest.TestCase):
    def test_scripts_parse_and_stay_ascii(self):
        for name in ("nightly_research.ps1", "run_full_research_control_plane.ps1", "run_pointer.ps1"):
            raw = (ROOT / name).read_bytes()
            self.assertFalse([b for b in raw if b > 127], f"{name} must stay ASCII (PowerShell 5.1 reads it as ANSI)")
            check = ("$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile("
                     f"'{ROOT / name}',[ref]$t,[ref]$e); $e.Count")
            result = subprocess.run([POWERSHELL, "-NoProfile", "-Command", check], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            self.assertEqual(result.stdout.strip(), "0", f"{name}: {result.stdout} {result.stderr}")

    def test_keep_awake_request_can_be_made_and_released(self):
        snippet = (
            "Add-Type -Namespace Win32 -Name PowerStateProbe -MemberDefinition "
            "'[DllImport(\"kernel32.dll\")] public static extern uint SetThreadExecutionState(uint esFlags);'; "
            "$a=[Win32.PowerStateProbe]::SetThreadExecutionState(0x80000001); "
            "$b=[Win32.PowerStateProbe]::SetThreadExecutionState(0x80000000); "
            "if ($a -ne 0 -and $b -ne 0) { 'ok' } else { 'failed' }")
        result = subprocess.run([POWERSHELL, "-NoProfile", "-Command", snippet], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(result.stdout.strip(), "ok", result.stderr)

    def test_the_run_script_asks_for_keep_awake_and_releases_it_in_finally(self):
        text = (ROOT / "run_full_research_control_plane.ps1").read_text(encoding="utf-8")
        self.assertIn("SetThreadExecutionState(0x80000001)", text)
        self.assertLess(text.index("SetThreadExecutionState(0x80000001)"), text.index("} finally {"))
        self.assertIn("SetThreadExecutionState(0x80000000)", text.split("} finally {", 1)[1])


if __name__ == "__main__":
    unittest.main()
