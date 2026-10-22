import json
import tempfile
import unittest
from pathlib import Path

from ecosystem_coordination import build_event, write_once


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class EcosystemCoordinationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_build_event_summarizes_run_and_handoffs(self) -> None:
        run_id = "run_test"
        _write(
            self.root / "output/babis_shortlist_latest.json",
            {
                "run_id": run_id,
                "universe_total": 100,
                "shortlist_total": 4,
                "runtime_summary": {"processed_symbols": 100},
                "errors": [{"symbol": "BAD"}],
            },
        )
        _write(self.root / "output/giannis_rule_based_latest.json", {"run_id": run_id})
        for agent in ("yankos", "periklis"):
            _write(
                self.root / f"output/{agent}_strategy_latest.json",
                {
                    "run_id": run_id,
                    "results": [
                        {"ticker": "ABC", "signal_verdict": "candidate"},
                        {"ticker": "ABC", "signal_verdict": "candidate"},
                        {"ticker": "XYZ", "signal_verdict": "reject"},
                    ],
                },
            )
        _write(
            self.root / "output/petros_review_latest.json",
            {"run_id": run_id, "results": [{"petros_verdict": "candidate"}]},
        )
        _write(
            self.root / "output/team_decision_latest.json",
            {
                "run_id": run_id,
                "focus_now": [{}],
                "review_next": [{}, {}],
                "selected_companies": [{}, {}, {}],
            },
        )

        event = build_event(
            project_root=self.root,
            run_id=run_id,
            generated_at="2026-08-31T20:00:00+03:00",
            trader_handoff_status="research_only_default",
            paper_requested=False,
        )

        self.assertTrue(event["contract_valid"])
        self.assertEqual(event["metrics"]["coverage_pct"], 99.0)
        self.assertEqual(
            event["metrics"]["yankos"],
            {"candidate_rows": 2, "candidate_symbols": 1},
        )
        self.assertEqual(event["metrics"]["selected_companies"], 3)
        self.assertFalse(event["alert"]["required"])
        # Optional in a research-only run, so it does not invalidate the bundle.
        self.assertFalse(event["handoffs"][-1]["contract_valid"])

    def test_run_identity_mismatch_requires_critical_alert(self) -> None:
        run_id = "run_expected"
        for name in (
            "babis_shortlist",
            "giannis_rule_based",
            "yankos_strategy",
            "periklis_strategy",
            "petros_review",
            "team_decision",
        ):
            _write(self.root / f"output/{name}_latest.json", {"run_id": run_id})
        _write(
            self.root / "output/periklis_strategy_latest.json",
            {"run_id": "run_wrong"},
        )

        event = build_event(
            project_root=self.root,
            run_id=run_id,
            generated_at="2026-08-31T20:00:00+03:00",
            trader_handoff_status="research_only_default",
            paper_requested=False,
        )

        self.assertFalse(event["contract_valid"])
        self.assertTrue(event["alert"]["required"])
        self.assertEqual(event["alert"]["severity"], "critical")
        self.assertIn("agent_handoff_contract_invalid", event["alert"]["reasons"])

    def test_write_once_is_idempotent(self) -> None:
        target = self.root / "outbox" / "event.json"
        path, created = write_once(target, {"value": 1})
        self.assertEqual(path, target)
        self.assertTrue(created)

        _, created_again = write_once(target, {"value": 2})
        self.assertFalse(created_again)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"value": 1})


if __name__ == "__main__":
    unittest.main()
