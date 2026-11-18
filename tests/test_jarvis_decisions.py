"""Owner 2026-11-18: the owner's approvals go through Jarvis. A "yes" on a proposal with a lesson action is carried
out once on the next pass, along the allowed transitions only; nothing without a yes; code changes carry no action."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import agent_learning_control_plane as learning
import jarvis
import jarvis_decisions as jd
import jarvis_state
import market_intel_db
from jarvis_report import SECTIONS, render

EXP = {"id": "E2", "title": "avoid mega", "lesson_ids": ["L2"], "final": True, "sessions": 18,
       "mean_daily_difference_pct": -1.0, "p_holm": 0.2}


class DecisionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.intel = self.root / "data" / "market_intel.db"
        self.intel.parent.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, rel: str, payload: dict) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def lesson(self, criterion: str, *, shadow: bool = True) -> str:
        with closing(market_intel_db.connect(self.intel)) as conn:
            learning.init_schema(conn)
            lesson_id, _ = learning.upsert_lesson_candidate(
                conn, target_agent="koulis", task_id="paper_trading", rubric_version="r", criterion_id=criterion,
                support_count=1, proposal={"proposed_change": criterion}, evidence={})
            if shadow:
                learning.set_lesson_status(conn, lesson_id, "shadow", reason="owner")
        return lesson_id

    def status(self, lesson_id: str) -> str:
        with closing(sqlite3.connect(self.intel)) as conn:
            return conn.execute("SELECT status FROM agent_lesson_candidates WHERE lesson_id=?",
                                (lesson_id,)).fetchone()[0]

    def propose(self, proposal: jarvis_state.Proposal, decision: str | None) -> str:
        with closing(jarvis_state.connect(self.root / "data" / "jarvis" / "proposals.sqlite3")) as conn:
            pid, _ = jarvis_state.upsert_proposal(conn, proposal, session_date="2026-12-29")
            if decision:
                jarvis_state.decide(conn, pid, decision, note="", session_date="2026-12-29")
        return pid

    def test_period_proposals_only_for_judged_experiments_and_failed_carries_the_action(self) -> None:
        self.write(str(jd.PERIOD_OUTPUT), {"period": {"id": "P1"}, "verdict": "collecting", "experiments": [
            {**EXP, "verdict": "failed"}, {**EXP, "id": "E4", "verdict": "passed", "lesson_ids": ["L4"]},
            {**EXP, "id": "E5", "verdict": "insufficient_sample"}, {**EXP, "id": "E1a", "verdict": "collecting",
                                                                    "final": False}]})
        got = {p.source_key: p for p in jd.period_proposals(self.root)}
        self.assertEqual(sorted(got), ["P1:E2", "P1:E4", "P1:E5"])
        self.assertEqual(got["P1:E2"].evidence["action"], {"kind": "lesson_status", "lesson_ids": ["L2"],
                                                           "to": "rejected"})
        self.assertNotIn("action", got["P1:E4"].evidence)  # making it live is code: with Claude
        self.assertNotIn("action", got["P1:E5"].evidence)
        self.write(str(jd.PERIOD_OUTPUT), {"baseline": True, "experiments": [{**EXP, "verdict": "failed"}]})
        self.assertEqual(jd.period_proposals(self.root), [])

    def test_a_judged_period_proposes_waiting_lessons_for_the_next_one(self) -> None:
        waiting = self.lesson("category:prefer:yankos:x:long:market_cap=mid", shadow=False)
        self.write(str(jd.PERIOD_OUTPUT), {"period": {"id": "P1"}, "verdict": "judged", "experiments": []})
        (proposal,) = jd.period_proposals(self.root)
        self.assertEqual((proposal.source, proposal.source_key), ("next_period", f"P1:{waiting}"))
        self.assertNotIn("action", proposal.evidence)  # the next period's pre-registration is written with Claude

    def test_a_yes_is_carried_out_once_and_nothing_without_it(self) -> None:
        yes, no, later = self.lesson("a"), self.lesson("b"), self.lesson("c")
        for lesson_id, decision in ((yes, "yes"), (no, "no"), (later, "later")):
            self.propose(jarvis_state.Proposal(source="period_experiment", source_key=lesson_id, title=f"t {lesson_id}",
                                               evidence={"verdict": "failed",
                                                         "action": jd.lesson_action([lesson_id], "rejected")}),
                         decision)
        self.propose(jarvis_state.Proposal(source="check", source_key="x", title="no action", evidence={}), "yes")
        done = jd.apply_approved(self.root)
        self.assertEqual([(d["result"], d["lessons"][0]["lesson_id"]) for d in done], [("applied", yes)])
        self.assertEqual([self.status(i) for i in (yes, no, later)], ["rejected", "shadow", "shadow"])
        self.assertEqual(jd.apply_approved(self.root), [])  # once only
        with closing(sqlite3.connect(self.intel)) as conn:
            decisions = json.loads(conn.execute("SELECT evidence_json FROM agent_lesson_candidates WHERE lesson_id=?",
                                                (yes,)).fetchone()[0])["decisions"]
        self.assertTrue(decisions[-1]["reason"].startswith("owner via Jarvis (2026-12-29)"))

    def test_a_transition_the_store_refuses_is_recorded_not_forced(self) -> None:
        waiting = self.lesson("w", shadow=False)
        self.propose(jarvis_state.Proposal(source="period_experiment", source_key="w", title="accept?",
                                           evidence={"action": jd.lesson_action([waiting], "accepted")}), "yes")
        (item,) = jd.apply_approved(self.root)
        self.assertEqual((item["result"], item["lessons"][0]["result"]), ("refused", "refused"))
        self.assertEqual(self.status(waiting), "candidate")
        md, data = render({"day": "2026-12-29", "sections": {"applied": {"items": [item]}}})
        self.assertIn(SECTIONS[8], md)
        self.assertIn("δεν εφαρμόστηκε: accept?", md)
        self.assertEqual(data["problems"], 1)

    def test_no_store_means_nothing_to_apply(self) -> None:
        self.assertEqual(jd.apply_approved(self.root), [])

    def test_lessons_judged_by_the_period_are_not_proposed_by_the_older_follow(self) -> None:
        self.write("period_experiments.json", {"experiments": [{"id": "E2", "lesson_ids": ["in_period"]}]})
        self.write("output/paper_learning/evaluation_latest.json", {"shadow_follow": [
            {"lesson_id": "in_period", "verdict": "not_confirmed"},
            {"lesson_id": "other", "verdict": "not_confirmed", "proposed_change": "x"},
            {"lesson_id": "good", "verdict": "confirmed", "proposed_change": "y"}]})
        got = {p.source_key: p for p in jarvis.paper_learning_proposals(self.root)}
        self.assertEqual(sorted(got), ["good", "other"])
        self.assertEqual(got["other"].evidence["action"]["to"], "archived")
        self.assertNotIn("action", got["good"].evidence)

    def test_the_report_lists_what_was_applied(self) -> None:
        md, _ = render({"day": "2026-12-29", "sections": {"applied": {"items": [
            {"title": "Περίοδος P1, E2: Δεν πέρασε", "result": "applied",
             "lessons": [{"lesson_id": "L2", "to": "rejected", "result": "applied"}]}]}}})
        part = md[md.index(SECTIONS[8]):md.index(SECTIONS[9])]
        self.assertIn("εφαρμόστηκε: Περίοδος P1, E2: Δεν πέρασε (L2 → rejected)", part)
        md, _ = render({"day": "2026-12-29", "sections": {}})
        self.assertIn("Τίποτα (εφαρμόζω μόνο ό,τι είπες «ναι»", md)


    def test_a_flipped_finding_becomes_a_proposal_whose_yes_archives_the_older_lesson(self) -> None:
        old = self.lesson("results:reaction:gross_margin=steady:better")
        self.lesson("results:reaction:gross_margin=steady:worse", shadow=False)
        with closing(sqlite3.connect(self.intel)) as conn:  # the older one really is older
            conn.execute("UPDATE agent_lesson_candidates SET created_at='2026-10-22' WHERE lesson_id=?", (old,))
            conn.commit()
        (proposal,) = jd.conflict_proposals(self.root)
        self.assertEqual((proposal.source, proposal.source_key), ("lesson_conflict", old))
        self.assertEqual(proposal.evidence["action"], jd.lesson_action([old], "archived"))
        self.assertIn("βγαίνει από την περίοδο", proposal.change)  # it was in shadow
        self.assertEqual(jd.apply_approved(self.root), [])  # nothing without the owner's yes
        self.propose(proposal, "yes")
        (done,) = jd.apply_approved(self.root)
        self.assertEqual((done["result"], self.status(old)), ("applied", "archived"))
        self.assertEqual(jd.conflict_proposals(self.root), [])

if __name__ == "__main__":
    unittest.main()
