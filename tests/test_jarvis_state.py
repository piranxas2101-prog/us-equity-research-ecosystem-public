"""Jarvis proposal store: yes / no / later, no duplicates, a declined proposal comes back only on new evidence."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from jarvis_state import Proposal, connect, decide, open_proposals, upsert_proposal

P = Proposal(source="jarvis_learning", source_key="lesson_abc", title="Προσοχή στα short gap_fade",
             evidence={"verdict": "confirmed", "loss_rate_pct": 72.0, "n": 14, "file": "output/x.json"},
             change="Ο Periklis αποφεύγει gap_fade short", rules_review=None)


class ProposalStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "data" / "jarvis" / "proposals.sqlite3")

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_upsert_is_idempotent(self):
        pid, a1 = upsert_proposal(self.conn, P, session_date="2026-11-16")
        pid2, a2 = upsert_proposal(self.conn, P, session_date="2026-11-16")
        self.assertEqual((pid, a1, a2), (pid2, "created", "unchanged"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0], 1)

    def test_no_suppresses_until_evidence_changes(self):
        pid, _ = upsert_proposal(self.conn, P, session_date="2026-11-16")
        decide(self.conn, pid, "no", note="", session_date="2026-11-16")
        self.assertEqual(upsert_proposal(self.conn, P, session_date="2026-11-17")[1], "suppressed")
        changed = replace(P, evidence={**P.evidence, "loss_rate_pct": 81.0})
        self.assertEqual(upsert_proposal(self.conn, changed, session_date="2026-11-18")[1], "updated")
        open_ids = [p["proposal_id"] for p in open_proposals(self.conn, session_date="2026-11-18",
                                                              sessions_since=lambda d: 0)]
        self.assertEqual(open_ids, [pid])

    def test_small_noise_is_not_new_evidence(self):
        pid, _ = upsert_proposal(self.conn, P, session_date="2026-11-16")
        decide(self.conn, pid, "no", note="", session_date="2026-11-16")
        noisy = replace(P, evidence={**P.evidence, "loss_rate_pct": 72.04})
        self.assertEqual(upsert_proposal(self.conn, noisy, session_date="2026-11-17")[1], "suppressed")

    def test_later_returns_after_five_sessions(self):
        pid, _ = upsert_proposal(self.conn, P, session_date="2026-11-16")
        decide(self.conn, pid, "later", note="", session_date="2026-11-16")
        self.assertEqual(open_proposals(self.conn, session_date="2026-11-20", sessions_since=lambda d: 4), [])
        self.assertEqual(len(open_proposals(self.conn, session_date="2026-11-23", sessions_since=lambda d: 5)), 1)

    def test_yes_is_recorded_and_closed(self):
        pid, _ = upsert_proposal(self.conn, P, session_date="2026-11-16")
        out = decide(self.conn, pid, "yes", note="ok", session_date="2026-11-16")
        self.assertEqual(out["state"], "approved")
        self.assertEqual(open_proposals(self.conn, session_date="2026-11-16", sessions_since=lambda d: 0), [])
        self.assertEqual(self.conn.execute("SELECT decision, note FROM decisions").fetchall(), [("yes", "ok")])

    def test_bad_decision_raises(self):
        pid, _ = upsert_proposal(self.conn, P, session_date="2026-11-16")
        with self.assertRaises(ValueError):
            decide(self.conn, pid, "maybe", note="", session_date="2026-11-16")
        with self.assertRaises(ValueError):
            decide(self.conn, "prop_missing", "yes", note="", session_date="2026-11-16")

    def test_open_proposal_carries_its_evidence(self):
        upsert_proposal(self.conn, P, session_date="2026-11-16")
        row = open_proposals(self.conn, session_date="2026-11-16", sessions_since=lambda d: 0)[0]
        self.assertEqual((row["title"], row["evidence"]["n"], row["state"]), (P.title, 14, "open"))


class MaterialEvidenceTests(ProposalStoreTests):
    """Review finding 1: counters that grow every session are not new evidence."""

    def test_growing_counter_does_not_reopen_a_no(self):
        p = Proposal(source="paper_learning", source_key="lesson_x", title="t",
                     evidence={"verdict": "confirmed", "sessions_after": 11, "criterion_id": "c"})
        pid, _ = upsert_proposal(self.conn, p, session_date="2026-11-16")
        decide(self.conn, pid, "no", note="", session_date="2026-11-16")
        grown = replace(p, evidence={**p.evidence, "sessions_after": 12})
        self.assertEqual(upsert_proposal(self.conn, grown, session_date="2026-11-17")[1], "suppressed")

    def test_new_trades_in_the_same_band_do_not_reopen(self):
        p = Proposal(source="jarvis_learning", source_key="l", title="t",
                     evidence={"verdict": "confirmed", "n": 12, "losses": 9, "loss_rate_pct": 75.0})
        pid, _ = upsert_proposal(self.conn, p, session_date="2026-11-16")
        decide(self.conn, pid, "no", note="", session_date="2026-11-16")
        more = replace(p, evidence={"verdict": "confirmed", "n": 13, "losses": 10, "loss_rate_pct": 76.9})
        self.assertEqual(upsert_proposal(self.conn, more, session_date="2026-11-17")[1], "suppressed")

    def test_later_waits_its_sessions_even_on_new_evidence(self):
        pid, _ = upsert_proposal(self.conn, P, session_date="2026-11-16")
        decide(self.conn, pid, "later", note="", session_date="2026-11-16")
        changed = replace(P, evidence={**P.evidence, "verdict": "not_confirmed"})
        upsert_proposal(self.conn, changed, session_date="2026-11-17")
        self.assertEqual(open_proposals(self.conn, session_date="2026-11-17", sessions_since=lambda d: 1), [])
        self.assertEqual(len(open_proposals(self.conn, session_date="2026-11-23", sessions_since=lambda d: 5)), 1)

    def test_withdraw_stale_closes_only_seen_sources_that_are_no_longer_ready(self):
        from jarvis_state import withdraw_stale
        a, _ = upsert_proposal(self.conn, replace(P, source="check", source_key="flip"), session_date="2026-11-16")
        b, _ = upsert_proposal(self.conn, replace(P, source="check", source_key="news"), session_date="2026-11-16")
        out = withdraw_stale(self.conn, source="check", seen={"flip"}, ready=set(), session_date="2026-11-17")
        self.assertEqual(out, [a])
        open_ids = [x["proposal_id"] for x in open_proposals(self.conn, session_date="2026-11-17",
                                                              sessions_since=lambda d: 0)]
        self.assertEqual(open_ids, [b])


if __name__ == "__main__":
    unittest.main()
