from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import catalyst_trade_lens
import run_context
import run_manifest


class RunContractTests(unittest.TestCase):
    def test_mixed_run_ids_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            run_context.require_compatible_runs({"run_id": "run_a"}, {"run_id": "run_b"})

    def test_context_is_attached_to_handoff(self) -> None:
        payload = run_context.attach_run_context({}, stage="test", run_id="run_test", source_run_ids=["run_test"])
        self.assertEqual(payload["run_id"], "run_test")
        self.assertEqual(payload["run_context"]["schema"], "ecosystem.run_context.v1")

    def test_explicit_contract_rejects_missing_run_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "Explicit run_id is required"):
            run_context.require_explicit_compatible_runs(
                {"run_id": "run_test"}, {"run_timestamp": "2026-08-25T12:00:00Z"}
            )

    def test_explicit_contract_accepts_one_shared_run_id(self) -> None:
        run_id = run_context.require_explicit_compatible_runs(
            {"run_id": "run_test"}, {"run_id": "run_test"}
        )
        self.assertEqual(run_id, "run_test")

    def test_manifest_captures_hash_and_missing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "output").mkdir()
            (root / "output" / "agent.json").write_text(json.dumps({"run_id": "run_test"}), encoding="utf-8")
            (root / "config.json").write_text("{}", encoding="utf-8")
            manifest_path = run_manifest.capture_manifest(
                project_root=root,
                run_id="run_test",
                artifacts=[Path("output/agent.json"), Path("output/missing.json")],
                configs=[Path("config.json")],
                runs_dir=root / "output" / "runs",
            )
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["captured_count"], 1)
            self.assertEqual(payload["missing_count"], 1)
            self.assertEqual(len(payload["artifacts"][0]["sha256"]), 64)
            with self.assertRaises(FileExistsError):
                run_manifest.capture_manifest(
                    project_root=root,
                    run_id="run_test",
                    artifacts=[],
                    configs=[],
                    runs_dir=root / "output" / "runs",
                )

    def test_catalyst_lens_is_bounded_and_symmetric(self) -> None:
        lens = catalyst_trade_lens.advisory_adjustments(
            {
                "long_catalyst_score": 10,
                "short_catalyst_score": 0,
                "catalyst_evidence_confidence_score": 1,
                "catalyst_trade_bias": "long",
            }
        )
        self.assertEqual(lens["long_adjustment"], 0.35)
        self.assertEqual(lens["short_adjustment"], -0.35)
        self.assertEqual(lens["role"], "bounded_advisory_not_direction_authority")
