import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from research_session_service import ResearchSessionService, atomic_json, creation_flags, process_exists


class ResearchSessionServiceTests(unittest.TestCase):
    def test_atomic_json_retries_transient_windows_replace_denial(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "state.json"
            original_replace = Path.replace
            attempts = 0

            def replace_once_denied(source, destination):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise PermissionError("transient sharing violation")
                return original_replace(source, destination)

            with patch.object(Path, "replace", replace_once_denied):
                atomic_json(target, {"status": "running"})
            self.assertEqual(attempts, 2)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")),
                             {"status": "running"})

    @staticmethod
    def wait_for(path: Path, status: str, timeout: float = 5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("status") == status:
                    return payload
            time.sleep(0.05)
        raise AssertionError(f"Timed out waiting for {status}")

    def test_windowless_creation_flags_are_used_on_windows(self):
        if sys.platform == "win32":
            import subprocess

            self.assertTrue(creation_flags() & subprocess.CREATE_NO_WINDOW)

    def test_start_health_stop_leaves_no_gateway_process(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / "data/state.json"
            stop = root / "data/stop.json"
            service = ResearchSessionService(
                project_root=root,
                state_path=state,
                stop_path=stop,
                lock_path=root / "data/session.lock",
                poll_seconds=0.05,
                health_interval_seconds=1,
                gateway_command=[sys.executable, "-c", "import time; time.sleep(60)"],
                health_runner=lambda: {"status": "healthy", "issues": []},
            )
            thread = threading.Thread(target=service.run)
            thread.start()
            running = self.wait_for(state, "running")
            gateway_pid = running["gateway_pid"]
            self.assertTrue(process_exists(gateway_pid))
            atomic_json(stop, {"stop": True})
            thread.join(timeout=8)
            self.assertFalse(thread.is_alive())
            stopped = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(stopped["status"], "stopped")
            self.assertFalse(process_exists(gateway_pid))
            self.assertFalse(stop.exists())

    def test_repeated_gateway_crash_hits_restart_limit_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / "data/state.json"
            service = ResearchSessionService(
                project_root=root,
                state_path=state,
                stop_path=root / "data/stop.json",
                lock_path=root / "data/session.lock",
                poll_seconds=0.05,
                max_restarts=1,
                gateway_command=[sys.executable, "-c", "import time; time.sleep(0.4)"],
                health_runner=lambda: {"status": "healthy", "issues": []},
            )
            exit_code = service.run()
            failed = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 1)
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["restart_count"], 1)
            self.assertIsNone(failed["gateway_pid"])


if __name__ == "__main__":
    unittest.main()
