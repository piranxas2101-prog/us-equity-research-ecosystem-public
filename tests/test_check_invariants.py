from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".claude" / "skills" / "invariants" / "check_invariants.py"
spec = importlib.util.spec_from_file_location("check_invariants", SCRIPT)
assert spec and spec.loader
ci = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ci
spec.loader.exec_module(ci)


def diff_for(path: str, *added: str) -> str:
    body = "".join(f"+{line}\n" for line in added)
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1,{len(added)} @@\n{body}"


def run_check(*diffs: str) -> list:
    return ci.check(ci.parse_diff("".join(diffs)))


def levels(findings: list, rule: str) -> list[str]:
    return [f.level for f in findings if f.rule == rule]


class ParseDiffTests(unittest.TestCase):
    def test_added_lines_carry_new_line_numbers(self) -> None:
        files = ci.parse_diff(diff_for("a.py", "x = 1", "y = 2"))
        self.assertEqual(files[0].path, "a.py")
        self.assertEqual(files[0].added, [(1, "x = 1"), (2, "y = 2")])

    def test_deleted_file_is_flagged(self) -> None:
        text = "diff --git a/alpaca_x.py b/alpaca_x.py\n--- a/alpaca_x.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
        files = ci.parse_diff(text)
        self.assertTrue(files[0].deleted)
        self.assertEqual(levels(ci.check(files), "alpaca-execution"), ["STOP"])


class StopRuleTests(unittest.TestCase):
    def test_protected_execution_files_stop(self) -> None:
        for path in (
            "alpaca_paper_trader.py",
            "koulis_risk_gate.py",
            "thymios_order_intent.py",
            "start_trader_paper_trading_today.ps1",
        ):
            with self.subTest(path=path):
                self.assertIn("STOP", levels(run_check(diff_for(path, "x = 1")), "alpaca-execution"))

    def test_hosted_llm_stops(self) -> None:
        for line in ("import anthropic", "from openai import OpenAI", 'URL = "https://api.anthropic.com/v1"'):
            with self.subTest(line=line):
                self.assertIn("STOP", levels(run_check(diff_for("babis_x.py", line)), "hosted-llm"))

    def test_launcher_with_process_spawn_stops_but_plain_mention_warns(self) -> None:
        stop = run_check(
            diff_for("a.py", "import subprocess", 'subprocess.run(["start_full_research_with_notify.ps1"])')
        )
        self.assertEqual(levels(stop, "long-run-launcher"), ["STOP"])
        warn = run_check(diff_for("a.py", 'HELP = "see start_control_center.ps1"'))
        self.assertEqual(levels(warn, "long-run-launcher"), ["WARN"])

    def test_secrets_and_runtime_state_stop(self) -> None:
        self.assertIn("STOP", levels(run_check(diff_for(".env", "KEY=1")), "secrets"))
        self.assertEqual(levels(run_check(diff_for(".env.example", "KEY=")), "secrets"), [])
        self.assertIn("STOP", levels(run_check(diff_for("output/run.csv", "a,b")), "runtime-state"))

    def test_ollama_app_start_stops(self) -> None:
        self.assertIn("STOP", levels(run_check(diff_for("x.ps1", "Start-Process ollama")), "ollama-app"))


class WarnRuleTests(unittest.TestCase):
    def test_silent_zero_defaults_warn(self) -> None:
        for line in ('v = row.get("revenue", 0)', "v = value or 0", "df = df.fillna(0)"):
            with self.subTest(line=line):
                self.assertEqual(levels(run_check(diff_for("giannis_x.py", line)), "silent-default"), ["WARN"])

    def test_explicit_unknown_does_not_warn(self) -> None:
        self.assertEqual(
            levels(
                run_check(diff_for("giannis_x.py", 'v = row.get("revenue")', 'w = row.get("x", None)')),
                "silent-default",
            ),
            [],
        )

    def test_threshold_file_warns_and_mentions_missing_tests(self) -> None:
        findings = run_check(diff_for("risk_limits.py", "MAX = 5"))
        message = next(f.message for f in findings if f.rule == "threshold-gate")
        self.assertIn("no tests/ change", message)

    def test_code_without_tests_warns_and_with_tests_does_not(self) -> None:
        self.assertEqual(levels(run_check(diff_for("giannis_x.py", "x = 1")), "missing-tests"), ["WARN"])
        both = run_check(diff_for("giannis_x.py", "x = 1"), diff_for("tests/test_giannis_x.py", "pass"))
        self.assertEqual(levels(both, "missing-tests"), [])

    def test_tests_and_docs_are_not_content_scanned(self) -> None:
        findings = run_check(
            diff_for("tests/test_a.py", 'v = {}.get("a", 0)'), diff_for("CLAUDE.md", "Never use Anthropic")
        )
        self.assertEqual(levels(findings, "silent-default"), [])
        self.assertEqual(levels(findings, "hosted-llm"), [])


class OutputTests(unittest.TestCase):
    def test_review_items_always_present_and_verdicts(self) -> None:
        clean = run_check(diff_for("README.md", "text"))
        self.assertTrue(levels(clean, "judgment"))
        self.assertIn("Verdict: PASS", ci.render(clean, 1))
        stop = run_check(diff_for("alpaca_paper_trader.py", "x"))
        self.assertIn("Verdict: STOP", ci.render(stop, 1))


class GitIntegrationTests(unittest.TestCase):
    def test_collect_diff_sees_modified_and_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def git(*args: str) -> None:
                subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

            git("init", "-q")
            git("config", "user.email", "t@example.com")
            git("config", "user.name", "t")
            (root / "a.py").write_text("x = 1\n", encoding="utf-8")
            git("add", ".")
            git("commit", "-qm", "init")
            (root / "a.py").write_text("x = 1\ny = z or 0\n", encoding="utf-8")
            (root / "alpaca_new.py").write_text("pass\n", encoding="utf-8")
            files = {f.path: f for f in ci.collect_diff(root, None)}
            self.assertEqual(files["a.py"].added, [(2, "y = z or 0")])
            self.assertIn("alpaca_new.py", files)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ci.main(["--root", str(root)]), 1)


if __name__ == "__main__":
    unittest.main()
