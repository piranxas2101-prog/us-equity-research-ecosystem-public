from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import agent_learning_control_plane as learning


class AgentLearningControlPlaneTests(unittest.TestCase):
    def connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        learning.init_schema(conn)
        return conn

    def test_episode_is_idempotent_and_immutable(self) -> None:
        conn = self.connection()
        self.assertGreaterEqual(learning.summary(conn)["feature_count"], 5)
        kwargs = dict(agent_id="babis", task_id="discovery", run_id="run_1",
                      subject_id="abc", observed_at="2026-09-23T00:00:00+00:00",
                      context={"available": 1}, decision={"rank": 1}, policy_version="v1")
        first, created = learning.record_episode(conn, **kwargs)
        second, created_again = learning.record_episode(conn, **kwargs)
        self.assertEqual(first, second)
        self.assertTrue(created)
        self.assertFalse(created_again)
        with self.assertRaises(ValueError):
            learning.record_episode(conn, **{**kwargs, "decision": {"rank": 2}})

    def test_model_requires_json_and_manual_state_progression(self) -> None:
        conn = self.connection()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model.json"
            path.write_text(json.dumps({"coefficients": [1.0]}), encoding="utf-8")
            model_id = learning.register_model(
                conn, agent_id="petros", task_id="synthesis", model_version="v1",
                model_type="logistic", model_path=path, dataset_sha256="data",
                feature_schema_sha256="features", training_rows=100,
                metrics={"balanced_accuracy": .7}, gates={"minimum_rows": 100})
            with self.assertRaises(ValueError):
                learning.transition_model(conn, model_id, "champion", "skip shadow", {})
            learning.transition_model(conn, model_id, "shadow", "manual review", {})
            learning.transition_model(conn, model_id, "champion", "validated", {})
            state = conn.execute("SELECT state FROM agent_model_registry WHERE model_id=?", (model_id,)).fetchone()[0]
            self.assertEqual(state, "champion")

    def test_artifact_adapter_records_each_symbol(self) -> None:
        conn = self.connection()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "babis.json"
            path.write_text(json.dumps({"run_id": "r1", "run_timestamp": "2026-09-23T00:00:00Z",
                                        "shortlist": [{"ticker": "ABC"}, {"ticker": "XYZ"}]}), encoding="utf-8")
            result = learning.record_artifact(conn, path, "babis", "discovery", "shortlist", "v1")
            self.assertEqual(result["inserted"], 2)
            self.assertEqual(learning.summary(conn)["episode_count"], 2)

    def test_artifact_rerecord_accepts_old_lineage_context_when_decision_is_same(self) -> None:
        conn = self.connection()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "agent.json"
            payload = {"run_id": "r1", "run_timestamp": "2026-09-23T00:00:00Z",
                       "results": [{"ticker": "ABC", "score": 1}]}
            path.write_text(json.dumps(payload), encoding="utf-8")
            learning.record_episode(
                conn, agent_id="yankos", task_id="strategy", run_id="r1",
                subject_id="ABC", observed_at=payload["run_timestamp"],
                context={"source_artifact_sha256": "old"}, decision=payload["results"][0],
                policy_version="v1",
            )
            result = learning.record_artifact(conn, path, "yankos", "strategy", "results", "v1")
            self.assertEqual(result["existing"], 1)

    def test_artifact_conflict_is_atomic(self) -> None:
        conn = self.connection()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "agent.json"
            base = {"run_id": "r1", "run_timestamp": "2026-09-23T00:00:00Z",
                    "results": [{"ticker": "ABC", "score": 1}]}
            path.write_text(json.dumps(base), encoding="utf-8")
            learning.record_artifact(conn, path, "babis", "discovery", "results", "v1")
            changed = {**base, "results": [{"ticker": "NEW", "score": 1},
                                             {"ticker": "ABC", "score": 2}]}
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(ValueError):
                learning.record_artifact(conn, path, "babis", "discovery", "results", "v1")
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM agent_learning_episodes WHERE subject_id='NEW'"
            ).fetchone())

    def test_artifact_records_distinct_playbooks_for_the_same_ticker(self) -> None:
        conn = self.connection()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "agent.json"
            payload = {"run_id": "r1", "run_timestamp": "2026-09-23T00:00:00Z",
                       "results": [{"ticker": "ABC", "score": 1},
                                   {"ticker": "ABC", "score": 2}]}
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = learning.record_artifact(conn, path, "yankos", "strategy", "results", "v1")
            self.assertEqual(result["inserted"], 2)
            subjects = [row[0] for row in conn.execute(
                "SELECT subject_id FROM agent_learning_episodes ORDER BY subject_id"
            )]
            self.assertEqual(len(subjects), 2)
            self.assertTrue(all(value.startswith("ABC::VARIANT:") for value in subjects))

    def test_evaluation_and_memory_are_immutable_and_versioned(self) -> None:
        conn = self.connection()
        episode_id, _ = learning.record_episode(
            conn, agent_id="giannis", task_id="fundamental_quality", run_id="r1",
            subject_id="ABC", observed_at="2026-09-23T00:00:00Z",
            context={"available": True}, decision={"score_final": 4.0},
            policy_version="v1",
        )
        evaluation_id, created = learning.record_evaluation(
            conn, episode_id=episode_id, evaluator_id="independent",
            rubric_version="r1", verdict="partial", score=0.5,
            criteria=[{"criterion_id": "risk", "passed": False}],
            evidence={"run_id": "r1"},
        )
        self.assertTrue(created)
        repeated_id, repeated = learning.record_evaluation(
            conn, episode_id=episode_id, evaluator_id="independent",
            rubric_version="r1", verdict="partial", score=0.5,
            criteria=[{"criterion_id": "risk", "passed": False}],
            evidence={"run_id": "r1"},
        )
        self.assertEqual(evaluation_id, repeated_id)
        self.assertFalse(repeated)
        with self.assertRaises(ValueError):
            learning.record_evaluation(
                conn, episode_id=episode_id, evaluator_id="independent",
                rubric_version="r1", verdict="pass", score=1.0,
                criteria=[], evidence={"run_id": "r1"},
            )
        memory_id, first_version, first_created = learning.remember_version(
            conn, agent_id="giannis", category="evaluation", subject_id="ABC",
            memory_key="r1", content={"verdict": "partial"},
            evidence={"evaluation_id": evaluation_id}, created_by="independent",
        )
        _, repeated_version, repeated_created = learning.remember_version(
            conn, agent_id="giannis", category="evaluation", subject_id="ABC",
            memory_key="r1", content={"verdict": "partial"},
            evidence={"evaluation_id": evaluation_id}, created_by="independent",
        )
        _, second_version, second_created = learning.remember_version(
            conn, agent_id="giannis", category="evaluation", subject_id="ABC",
            memory_key="r1", content={"verdict": "pass"},
            evidence={"evaluation_id": "new"}, created_by="independent",
        )
        self.assertTrue(first_created)
        self.assertFalse(repeated_created)
        self.assertEqual(first_version, repeated_version)
        self.assertTrue(second_created)
        self.assertNotEqual(first_version, second_version)
        current = conn.execute(
            "SELECT current_version_id FROM agent_memory_items WHERE memory_id=?", (memory_id,)
        ).fetchone()[0]
        self.assertEqual(current, second_version)

    def test_new_lesson_waits_and_never_moves_by_itself(self) -> None:
        # owner 2026-11-18: a new lesson waits as a candidate; shadow and acceptance are the owner's decisions
        conn = self.connection()
        lesson_id, created = learning.upsert_lesson_candidate(
            conn, target_agent="petros", task_id="fundamental_synthesis",
            rubric_version="r1", criterion_id="rationale", support_count=20,
            proposal={"change": "require rationale"}, evidence={"run_id": "r1"},
        )
        self.assertTrue(created)
        _, created_again = learning.upsert_lesson_candidate(
            conn, target_agent="petros", task_id="fundamental_synthesis",
            rubric_version="r1", criterion_id="rationale", support_count=25,
            proposal={"change": "require rationale"}, evidence={"run_id": "r2"},
        )
        self.assertFalse(created_again)
        status, support = conn.execute(
            "SELECT status,support_count FROM agent_lesson_candidates WHERE lesson_id=?",
            (lesson_id,),
        ).fetchone()
        self.assertEqual(status, "candidate")
        self.assertEqual(support, 25)
        decisions = json.loads(conn.execute("SELECT evidence_json FROM agent_lesson_candidates").fetchone()[0])[
            "decisions"]
        self.assertEqual([(d["from"], d["to"]) for d in decisions], [(None, "candidate")])
        self.assertEqual(decisions[0]["reason"], learning.WAITING_REASON)
        self.assertFalse(hasattr(learning, "shadow_all_candidates"))  # no bulk move to shadow any more
        learning.set_lesson_status(conn, lesson_id, "shadow", reason="owner: period P1")
        with self.assertRaises(ValueError):  # never accepted straight from waiting, never twice in shadow
            learning.set_lesson_status(conn, lesson_id, "shadow", reason="again")
        # owner 2026-11-18: back to waiting only by a decision (upsert never moves it)
        learning.set_lesson_status(conn, lesson_id, "candidate", reason="owner: out of the period, still taught")
        learning.upsert_lesson_candidate(
            conn, target_agent="petros", task_id="fundamental_synthesis",
            rubric_version="r1", criterion_id="rationale", support_count=26,
            proposal={"change": "require rationale"}, evidence={"run_id": "r3"},
        )
        self.assertEqual(conn.execute("SELECT status FROM agent_lesson_candidates").fetchone()[0], "candidate")

    def test_waiting_lessons_are_counted_per_task_and_unknown_stays_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "intel.db"
            self.assertIsNone(learning.waiting_lessons(db))  # no store: unknown, not zero
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            learning.init_schema(conn)
            for task, criterion in (("paper_trading", "a"), ("paper_trading", "b"), ("earnings_journal", "c")):
                learning.upsert_lesson_candidate(conn, target_agent="koulis", task_id=task, rubric_version="r",
                                                 criterion_id=criterion, support_count=1, proposal={"p": criterion},
                                                 evidence={})
            lesson_id = conn.execute("SELECT lesson_id FROM agent_lesson_candidates WHERE criterion_id='c'"
                                     ).fetchone()[0]
            learning.set_lesson_status(conn, lesson_id, "shadow", reason="owner")
            conn.close()
            self.assertEqual(learning.waiting_lessons(db), {"paper_trading": 2})

    def _shadow(self, conn: sqlite3.Connection, *ids: str) -> None:
        for lesson_id in ids:
            conn.execute("INSERT INTO agent_lesson_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (lesson_id, "koulis", "paper_trading", "r", lesson_id, "shadow", 3, "{}", "{}", "h", "t", "t"))
        conn.commit()

    def status(self, conn: sqlite3.Connection, lesson_id: str) -> str:
        return conn.execute("SELECT status FROM agent_lesson_candidates WHERE lesson_id=?", (lesson_id,)).fetchone()[0]

    def test_lesson_decisions_apply_all_with_reasons(self) -> None:
        conn = self.connection()
        self._shadow(conn, "a", "b", "keep")
        counts = learning.apply_lesson_decisions(conn, [
            {"lesson_id": "a", "status": "archived", "reason": "owner: waiting list"},
            {"lesson_id": "b", "status": "rejected", "reason": "owner: one yardstick says no"}])
        self.assertEqual(counts, {"archived": 1, "rejected": 1})
        self.assertEqual([self.status(conn, x) for x in ("a", "b", "keep")], ["archived", "rejected", "shadow"])
        decisions = json.loads(conn.execute("SELECT evidence_json FROM agent_lesson_candidates WHERE lesson_id='a'"
                                            ).fetchone()[0])["decisions"]
        self.assertEqual((decisions[-1]["from"], decisions[-1]["to"], decisions[-1]["reason"]),
                         ("shadow", "archived", "owner: waiting list"))

    def test_lesson_decisions_write_nothing_when_one_is_invalid(self) -> None:
        conn = self.connection()
        self._shadow(conn, "a", "b")
        bad_batches = [
            [{"lesson_id": "a", "status": "archived", "reason": "ok"}, {"lesson_id": "b", "status": "shadow",
                                                                       "reason": "already in shadow"}],
            [{"lesson_id": "a", "status": "archived", "reason": "ok"}, {"lesson_id": "b", "status": "archived",
                                                                       "reason": " "}],
            [{"lesson_id": "a", "status": "archived", "reason": "ok"}, {"lesson_id": "a", "status": "archived",
                                                                       "reason": "twice"}],
        ]
        for batch in bad_batches:
            with self.assertRaises(ValueError):
                learning.apply_lesson_decisions(conn, batch)
        with self.assertRaises(KeyError):
            learning.apply_lesson_decisions(conn, [{"lesson_id": "missing", "status": "archived", "reason": "x"}])
        self.assertEqual([self.status(conn, x) for x in ("a", "b")], ["shadow", "shadow"])

    def test_an_archived_lesson_is_not_revived_by_new_evidence(self) -> None:
        conn = self.connection()
        proposal = {"proposed_change": "x"}
        kwargs = dict(target_agent="koulis", task_id="paper_trading", rubric_version="r", criterion_id="c",
                      support_count=3, proposal=proposal, evidence={"n": 1})
        lesson_id, _ = learning.upsert_lesson_candidate(conn, **kwargs)
        learning.apply_lesson_decisions(conn, [{"lesson_id": lesson_id, "status": "archived", "reason": "owner"}])
        learning.upsert_lesson_candidate(conn, **{**kwargs, "evidence": {"n": 2}})
        self.assertEqual(self.status(conn, lesson_id), "archived")
        # owner 2026-11-18: back to the waiting list only by a decision, and never straight to shadow
        with self.assertRaises(ValueError):
            learning.set_lesson_status(conn, lesson_id, "shadow", reason="owner")
        learning.apply_lesson_decisions(conn, [{"lesson_id": lesson_id, "status": "candidate",
                                                "reason": "owner: back to the waiting list"}])
        self.assertEqual(self.status(conn, lesson_id), "candidate")

    def test_triage_of_2026_11_18_is_well_formed(self) -> None:
        root = Path(__file__).resolve().parents[1]
        triage = json.loads((root / "docs" / "decisions" / "lesson_triage_2026-11-18.json").read_text(encoding="utf-8"))
        kept = {i for e in json.loads((root / "period_experiments.json").read_text(encoding="utf-8"))["experiments"]
                for i in e["lesson_ids"]}
        decided = [d["lesson_id"] for d in triage["decisions"]]
        self.assertEqual(len(decided), len(set(decided)))
        self.assertFalse(set(triage["kept_in_shadow"]) & set(decided))
        self.assertEqual(len(decided) + len(triage["kept_in_shadow"]), 178)
        self.assertTrue(all(d["status"] in {"archived", "rejected"} and d["reason"].startswith("owner 2026-11-18")
                            for d in triage["decisions"]))
        # later the same day: E1a replaced (old lesson archived, new one in shadow), the 12 waiting-list lessons back
        # to waiting so the agents learn them
        follow = json.loads((root / "docs" / "decisions" / "lesson_decisions_2026-11-18b.json"
                             ).read_text(encoding="utf-8"))
        to = {d["lesson_id"]: d["status"] for d in follow["decisions"]}
        now_shadow = (set(triage["kept_in_shadow"]) - {i for i, s in to.items() if s != "shadow"}) | {
            i for i, s in to.items() if s == "shadow"}
        self.assertEqual(now_shadow, kept)
        self.assertEqual(sum(1 for s in to.values() if s == "candidate"), 12)
        self.assertEqual({c["lesson_id"] for c in follow["creates"]} - set(decided), {c["lesson_id"]
                                                                                      for c in follow["creates"]})


    def test_giannis_triage_of_2026_11_18_is_well_formed(self) -> None:
        # owner 2026-11-18: the results-journal lessons - reaction and position-monitor copies archived (not usable),
        # one side of each mirrored pair archived, the drift lessons for Giannis and the traders back to waiting
        root = Path(__file__).resolve().parents[1]
        triage = json.loads((root / "docs" / "decisions" / "lesson_triage_giannis_2026-11-18.json"
                             ).read_text(encoding="utf-8"))
        items = triage["decisions"]
        ids = [d["lesson_id"] for d in items]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(d["reason"].startswith("owner 2026-11-18") for d in items))
        archived = [d for d in items if d["status"] == "archived"]
        waiting = [d for d in items if d["status"] == "candidate"]
        self.assertEqual((len(archived), len(waiting), len(items)), (99, 72, 171))
        self.assertFalse([d for d in waiting if d["target_agent"] == "position_monitor"])
        self.assertFalse([d for d in waiting if ":reaction:" in d["criterion_id"]])
        self.assertTrue(all(d["criterion_id"].startswith(("results:drift", "results_entry:drift")) for d in waiting))

if __name__ == "__main__":
    unittest.main()
