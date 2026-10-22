from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import agent_assimilation


class AgentAssimilationTests(unittest.TestCase):
    def test_current_contract_and_reports_pass(self) -> None:
        root = Path(__file__).resolve().parents[1]
        result = agent_assimilation.verify_reports(
            root / "ecosystem_theory_contract.json", root / "docs" / "agent_reports", root
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["scope"], "theoretical_only")
        self.assertFalse(result["daily_run_authorized_by_this_gate"])

    def test_stale_report_is_rejected(self) -> None:
        root = Path(__file__).resolve().parents[1]
        contract = json.loads((root / "ecosystem_theory_contract.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp:
            reports = Path(temp)
            for agent in agent_assimilation.EXPECTED_AGENTS:
                (reports / f"{agent}_theory_report.md").write_text(
                    agent_assimilation.render_report(agent, contract), encoding="utf-8"
                )
            (reports / "babis_theory_report.md").write_text("stale", encoding="utf-8")
            result = agent_assimilation.verify_reports(
                root / "ecosystem_theory_contract.json", reports, root
            )
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any(error.startswith("babis:") for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
