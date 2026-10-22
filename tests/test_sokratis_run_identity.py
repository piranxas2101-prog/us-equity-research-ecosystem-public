from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

import sokratis_strategy_teacher


class SokratisRunIdentityTests(unittest.TestCase):
    def build_args(self, root: Path, yankos: dict, periklis: dict) -> argparse.Namespace:
        files = {
            "manifest": {},
            "backtest": {},
            "yankos": yankos,
            "periklis": periklis,
        }
        for name, payload in files.items():
            (root / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
        for name in ("taxonomy", "curriculum", "intake"):
            (root / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
        return argparse.Namespace(
            manifest=str(root / "manifest.json"),
            backtest=str(root / "backtest.json"),
            yankos=str(root / "yankos.json"),
            periklis=str(root / "periklis.json"),
            taxonomy=str(root / "taxonomy.md"),
            curriculum=str(root / "curriculum.md"),
            intake=str(root / "intake.md"),
            out_prefix=str(root / "sokratis"),
        )

    def test_sokratis_rejects_missing_upstream_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            args = self.build_args(
                Path(temp),
                {"run_id": "run_test", "results": []},
                {"run_timestamp": "2026-08-25T12:00:00Z", "results": []},
            )
            with self.assertRaisesRegex(ValueError, "Explicit run_id is required"):
                sokratis_strategy_teacher.build_payload(args)

    def test_sokratis_attaches_hashes_and_specific_decision_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            args = self.build_args(
                Path(temp),
                {
                    "run_id": "run_test",
                    "agent_name": "yankos",
                    "results": [
                        {
                            "ticker": "ABC",
                            "playbook_name": "support_bounce",
                            "signal_verdict": "candidate",
                            "trade_direction": "long",
                            "decision_id": "decision_1",
                        }
                    ],
                },
                {"run_id": "run_test", "agent_name": "periklis", "results": []},
            )
            payload = sokratis_strategy_teacher.build_payload(args)

        self.assertEqual(payload["run_id"], "run_test")
        self.assertEqual(payload["run_context"]["stage"], "sokratis_strategy_teacher")
        self.assertTrue(all(source["sha256"] for source in payload["source_manifest"]))
        self.assertEqual(payload["decision_links"][0]["decision_id"], "decision_1")
        self.assertEqual(payload["skill_proposals"][0]["decision_links"][0]["ticker"], "ABC")


if __name__ == "__main__":
    unittest.main()
