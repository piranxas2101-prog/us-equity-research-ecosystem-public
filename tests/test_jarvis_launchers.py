"""Jarvis runs at the end of the existing scheduled work: before every rest/hibernate step, through Invoke-Logged (or
Invoke-Retried), so a Jarvis failure can never end a launcher or keep the computer from resting."""
from __future__ import annotations

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
NIGHTLY_CALL = re.compile(r'jarvis\.py"\)?\s+"after-nightly"')
SCRIPTS = ("hibernate_after_run.ps1", "nightly_research.ps1", "start_trader_afternoon_session.ps1")


def lines(name: str) -> list[str]:
    return (ROOT / name).read_text(encoding="utf-8-sig").splitlines()


class LauncherTests(unittest.TestCase):
    def test_each_rest_call_is_preceded_by_jarvis(self):
        text = lines("start_trader_afternoon_session.ps1")
        rests = [n for n, line in enumerate(text) if line.strip().startswith("Invoke-Rest ")]
        self.assertEqual(len(rests), 3)
        for index in rests:
            window = text[max(0, index - 8):index]
            with self.subTest(line=index + 1):
                self.assertTrue(any('"jarvis.py" "after-session"' in line for line in window))

    def test_nightly_paths_call_jarvis_before_hibernate(self):
        for name in ("hibernate_after_run.ps1", "nightly_research.ps1"):
            text = "\n".join(lines(name))
            with self.subTest(script=name):
                calls = list(NIGHTLY_CALL.finditer(text))
                self.assertTrue(calls)
                self.assertLess(calls[-1].start(), text.rindex("hibernate_if_idle.ps1"))

    def test_nightly_success_path_does_not_call_jarvis_twice(self):
        # nightly_research.ps1 calls Jarvis only on its fallback branch; hibernate_after_run.ps1 covers the normal run
        self.assertEqual(sum('jarvis.py"' in line for line in lines("nightly_research.ps1")), 1)
        # the normal end, and the exit when no run started within 10 minutes (review finding 2)
        self.assertEqual(sum('jarvis.py"' in line for line in lines("hibernate_after_run.ps1")), 2)

    def test_jarvis_calls_go_through_invoke_logged_or_retried(self):
        for name in SCRIPTS:
            for number, line in enumerate(lines(name), 1):
                if 'jarvis.py"' in line:
                    with self.subTest(script=name, line=number):
                        self.assertRegex(line, r"Invoke-(Logged|Retried)")

    def test_error_branch_jarvis_cannot_skip_rest(self):
        text = lines("start_trader_afternoon_session.ps1")
        index = next(n for n, line in enumerate(text) if 'Invoke-Rest "Afternoon session stopped by an error"' in line)
        call = next(line for line in reversed(text[:index]) if 'jarvis.py"' in line)
        self.assertIn("try {", call)
        self.assertIn("catch {", call)

    def test_jarvis_lines_are_ascii(self):
        for name in SCRIPTS:
            for line in lines(name):
                if "jarvis" in line.lower():
                    line.encode("ascii")


if __name__ == "__main__":
    unittest.main()
