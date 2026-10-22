"""Tests for the research-only Python daily pipeline runner."""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

import daily_pipeline as dp


def full_market_context() -> dp.Context:
    ctx = dp.Context(options=dp.Options(run_id="run_test", full_market=True, python="python"), timestamp="20260928_120000")
    ctx.baseline = {key: f"output/baseline/{key}.json" for key in ("babis", "giannis", "yankos", "periklis", "petros")}
    return ctx


def scripts_in_order(argvs: list[list[str]]) -> list[str]:
    names = [next((Path(arg).name for arg in argv if arg.endswith(".py")), "") for argv in argvs]
    collapsed: list[str] = []
    for name in names:
        if name and (not collapsed or collapsed[-1] != name):
            collapsed.append(name)
    return collapsed


class ParityTests(unittest.TestCase):
    def test_same_python_scripts_in_same_order_as_powershell(self) -> None:
        ps1 = (Path(dp.ROOT) / "run_daily_dual_watchlist.ps1").read_text(encoding="utf-8-sig")
        powershell = []
        for name in re.findall(r'"([a-z_]+\.py)"', ps1):
            if not powershell or powershell[-1] != name:
                powershell.append(name)
        ctx = full_market_context()
        runner = scripts_in_order([stage.command(ctx) for stage in dp.build_stages() if stage.command])
        self.assertEqual(runner, powershell)

    def test_no_trading_or_order_path(self) -> None:
        ctx = full_market_context()
        text = " ".join(" ".join(stage.command(ctx)) for stage in dp.build_stages() if stage.command).lower()
        for forbidden in ("alpaca", "koulis", "thymios", "miltos", "start_trader", "submitpaper", "--submit"):
            self.assertNotIn(forbidden, text)

    def test_babis_uses_full_universe_without_llm(self) -> None:
        argv = next(stage for stage in dp.build_stages() if stage.name == "babis").command(full_market_context())
        self.assertIn("--full-universe", argv)
        self.assertIn("--no-stage1-llm", argv)
        self.assertIn("--no-stage2-llm", argv)

    def test_plan_never_writes_the_locked_watchlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "never_created.json"
            ctx = dp.Context(options=dp.Options(run_id="run_test", locked_watchlist=str(missing)),
                             timestamp="t", dry_run=True)
            lines = dp.describe(dp.build_stages(), ctx)
            self.assertFalse(missing.exists())
            self.assertTrue(any("cannot build command yet" in line for line in lines))

    def test_petros_runs_without_model(self) -> None:
        argv = next(stage for stage in dp.build_stages() if stage.name == "petros").command(full_market_context())
        self.assertIn("--no-model", argv)


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state_path = self.root / "runs" / "run_test" / "pipeline_state.json"
        self.calls: list[str] = []
        self.fail: set[str] = set()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def executor(self, argv: list[str], log: Path, env: dict[str, str]) -> int:
        name = argv[0]
        self.calls.append(name)
        assert env["ECOSYSTEM_RUN_ID"] == "run_test"
        (self.root / f"{name}.out").write_text(f"output of {name}", encoding="utf-8")
        return 1 if name in self.fail else 0

    def stages(self) -> list[dp.Stage]:
        def cmd(name: str):
            return lambda ctx: [name]
        return [
            dp.Stage("a", True, cmd("a"), outputs=("a.out",)),
            dp.Stage("b", False, cmd("b"), outputs=("b.out",)),
            dp.Stage("c", True, cmd("c"), outputs=("c.out",)),
            dp.Stage("d", False, cmd("d"), skip_reason=lambda ctx: "not needed"),
            dp.Stage("e", True, cmd("e"), outputs=("e.out",)),
        ]

    def context(self) -> dp.Context:
        return dp.Context(options=dp.Options(run_id="run_test"), timestamp="20260928_120000")

    def run_once(self, previous=None) -> dict:
        return dp.run_pipeline(self.stages(), self.context(), state_path=self.state_path,
                               executor=self.executor, previous=previous, root=self.root)

    def test_all_stages_run_in_order(self) -> None:
        state = self.run_once()
        self.assertEqual(self.calls, ["a", "b", "c", "e"])
        self.assertEqual(state["status"], "completed")
        self.assertEqual([item["status"] for item in state["stages"]],
                         ["completed", "completed", "completed", "skipped", "completed"])
        self.assertTrue(state["research_only"])
        self.assertFalse(state["paper_submission"])

    def test_warning_stage_failure_continues(self) -> None:
        self.fail = {"b"}
        state = self.run_once()
        self.assertEqual(self.calls, ["a", "b", "c", "e"])
        self.assertEqual(state["status"], "completed_with_warnings")
        self.assertEqual(len(state["warnings"]), 1)

    def test_critical_failure_stops_and_saves_state(self) -> None:
        self.fail = {"c"}
        with self.assertRaises(dp.PipelineFailed):
            self.run_once()
        self.assertEqual(self.calls, ["a", "b", "c"])
        saved = dp.load_json(self.state_path)
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["failed_stage"], "c")

    def test_resume_skips_finished_stages(self) -> None:
        self.fail = {"c"}
        with self.assertRaises(dp.PipelineFailed):
            self.run_once()
        self.fail, self.calls = set(), []
        state = self.run_once(previous=dp.load_json(self.state_path))
        self.assertEqual(self.calls, ["c", "e"])
        self.assertEqual(state["status"], "completed")
        self.assertTrue(state["stages"][0]["reused_on_resume"])

    def test_resume_refused_when_outputs_changed(self) -> None:
        self.fail = {"c"}
        with self.assertRaises(dp.PipelineFailed):
            self.run_once()
        (self.root / "a.out").write_text("overwritten by another run", encoding="utf-8")
        with self.assertRaises(dp.ResumeConflict):
            self.run_once(previous=dp.load_json(self.state_path))

    def test_resume_retries_failed_warning_stage_and_drops_its_old_warning(self) -> None:
        self.fail = {"b", "c"}
        with self.assertRaises(dp.PipelineFailed):
            self.run_once()
        self.fail, self.calls = set(), []
        state = self.run_once(previous=dp.load_json(self.state_path))
        self.assertEqual(self.calls, ["b", "c", "e"])
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["warnings"], [])

    def test_state_never_lives_in_the_manifest_folder(self) -> None:
        # run_manifest.py must create output/runs/<run_id>/ itself and refuses an existing one.
        self.assertNotEqual(dp.STATE_DIR, dp.OUTPUT / "runs")
        self.assertFalse(str(dp.STATE_DIR).startswith(str(dp.OUTPUT / "runs")))

    def test_lock_blocks_a_second_run(self) -> None:
        lock_path = self.root / "pipeline.lock"
        with dp.RunLock(lock_path):
            with self.assertRaises(dp.PipelineFailed):
                with dp.RunLock(lock_path):
                    pass
        with dp.RunLock(lock_path):  # released after the first run ends
            pass

    def test_compare_is_skipped_without_baseline(self) -> None:
        stage = next(stage for stage in dp.build_stages() if stage.name == "compare")
        ctx = dp.Context(options=dp.Options(run_id="run_test"), timestamp="t")
        ctx.baseline = {"babis": None, "giannis": "x", "yankos": "x", "petros": "x"}
        self.assertIsNotNone(stage.skip_reason(ctx))


if __name__ == "__main__":
    unittest.main()
