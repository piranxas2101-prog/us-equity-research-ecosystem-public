"""The real exit price of a position held over several sessions: read once it is final, unknown stays out of the
averages, a broker that does not answer leaves the exit pending, and the ledger's own records are not rewritten."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import paper_trade_ledger as ledger


def line(order="o1", direction="short", entry=10.0, plpc=0.10, reason="thesis_direction_flip", symbol="VISN"):
    return {"exit_order_id": order, "symbol": symbol, "direction": direction, "entry_price": entry, "exit_plpc": plpc,
            "close_reason": reason}


class ExitFillsTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "closed.jsonl"

    def write(self, *rows):
        self.log.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")

    def test_a_final_order_is_read_once(self):
        self.write(line())
        calls = []

        def order(order_id):
            calls.append(order_id)
            return {"status": "filled", "filled_avg_price": "9.1", "filled_qty": "39", "filled_at": "2026-11-06T14:38:13Z"}
        self.assertEqual(ledger.record_exit_fills(self.conn, self.log, order)["read"], 1)
        self.assertEqual(ledger.record_exit_fills(self.conn, self.log, order)["read"], 0)
        self.assertEqual(calls, ["o1"])
        row = self.conn.execute("SELECT status, filled_avg_price, filled_qty FROM paper_exit_fills").fetchone()
        self.assertEqual(row, ("filled", 9.1, 39.0))

    def test_an_order_not_final_stays_pending_and_is_read_again(self):
        self.write(line())
        out = ledger.record_exit_fills(self.conn, self.log, lambda _: {"status": "pending_new"})
        self.assertEqual(out["not_final"], 1)
        self.assertEqual(ledger.exit_fill_summary(self.conn, self.log)["pending"], 1)
        answer = {"status": "filled", "filled_avg_price": "9.1"}
        self.assertEqual(ledger.record_exit_fills(self.conn, self.log, lambda _: answer)["read"], 1)
        self.assertEqual(ledger.exit_fill_summary(self.conn, self.log)["pending"], 0)

    def test_a_broker_that_does_not_answer_stops_soon_and_writes_nothing(self):
        self.write(*[line(f"o{i}") for i in range(8)])
        calls = []

        def order(order_id):
            calls.append(order_id)
            raise OSError("no answer")
        out = ledger.record_exit_fills(self.conn, self.log, order)
        self.assertEqual((out["unreadable"], len(calls)), (ledger.FILL_READ_ERRORS_MAX, ledger.FILL_READ_ERRORS_MAX))
        summary = ledger.exit_fill_summary(self.conn, self.log)
        self.assertEqual(summary["pending"], 8)
        self.assertIsNone(summary["overall"])

    def test_decision_against_fill_for_a_short_and_a_long(self):
        self.write(line("s", "short", 10.0, 0.10), line("l", "long", 10.0, -0.05, "scaled_stop"))
        fills = {"s": "9.1", "l": "9.4"}
        ledger.record_exit_fills(self.conn, self.log, lambda oid: {"status": "filled", "filled_avg_price": fills[oid]})
        out = ledger.exit_fill_summary(self.conn, self.log)
        flip = out["by_exit_rule"]["thesis_direction_flip"]
        self.assertEqual((flip["mean_at_decision_pct"], flip["mean_at_fill_pct"], flip["mean_difference_pct"]),
                         (10.0, 9.0, -1.0))          # the short bought back higher than the decision showed
        stop = out["by_exit_rule"]["scaled_stop"]
        self.assertEqual((stop["mean_at_fill_pct"], stop["mean_difference_pct"]), (-6.0, -1.0))
        self.assertEqual((out["overall"]["exits"], out["overall"]["worst_difference_pct"]), (2, -1.0))

    def test_unknown_is_counted_and_never_averaged(self):
        self.write(line("cancelled"), line("no_entry", entry=None), line("no_pct", plpc=None), line("waiting"),
                   {"symbol": "OLD", "close_reason": "not_in_account"})
        answers = {"cancelled": {"status": "canceled"}, "no_entry": {"status": "filled", "filled_avg_price": "9"},
                   "no_pct": {"status": "filled", "filled_avg_price": "9"}, "waiting": {"status": "new"}}
        ledger.record_exit_fills(self.conn, self.log, lambda oid: answers[oid])
        out = ledger.exit_fill_summary(self.conn, self.log)
        self.assertEqual((out["not_filled"], out["no_comparison"], out["pending"], out["overall"]), (1, 2, 1, None))

    def test_no_closed_log_is_no_exit(self):
        out = ledger.exit_fill_summary(self.conn, self.log)
        self.assertEqual((out["pending"], out["overall"], out["by_exit_rule"]), (0, None, {}))


class ResultAtTheFillTests(unittest.TestCase):
    """Owner 2026-11-13: a closed position is measured at the price its closing order really got."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.conn = ledger.connect(self.root / "ledger.db")
        self.addCleanup(self.conn.close)
        self.log = self.root / "closed.jsonl"

    def held(self, symbol, direction, order, *, entry=10.0, plpc=0.10, record_entry=10.0):
        opened = date(2026, 11, 6)
        rid = ledger.record_id(opened, "trade", "yankos", symbol, "support_bounce_swing", direction)
        ledger.store(self.conn, [{"record_id": rid, "session_date": opened.isoformat(), "kind": "trade",
                                  "complete": True, "entry_price": record_entry, "result_pct": round(plpc * 100, 3)}])
        return rid, {"exit_order_id": order, "symbol": symbol, "direction": direction, "entry_price": entry,
                     "exit_plpc": plpc, "close_reason": "scaled_stop", "opened_session": opened.isoformat(),
                     "agent": "yankos", "playbook": "support_bounce_swing"}

    def record(self, rid):
        return json.loads(self.conn.execute("SELECT record_json FROM paper_trade_ledger WHERE record_id=?",
                                            (rid,)).fetchone()[0])

    def test_a_held_trade_is_settled_at_its_fill_once_and_keeps_the_decision_beside_it(self):
        short, short_row = self.held("VISN", "short", "s", plpc=0.10)
        long_, long_row = self.held("AEM", "long", "l", plpc=-0.05)
        waiting, waiting_row = self.held("DAVE", "long", "w", plpc=-0.02)
        unfilled, unfilled_row = self.held("ROK", "short", "u", plpc=-0.09)
        self.log.write_text("\n".join(json.dumps(r) for r in (short_row, long_row, waiting_row, unfilled_row)),
                            encoding="utf-8")
        answers = {"s": {"status": "filled", "filled_avg_price": "9.1"}, "l": {"status": "filled", "filled_avg_price": "9.4"},
                   "w": {"status": "pending_new"}, "u": {"status": "canceled"}}
        ledger.record_exit_fills(self.conn, self.log, lambda oid: answers[oid])
        self.assertEqual(ledger.apply_exit_fills(self.conn, self.log), 3)
        got = self.record(short)
        self.assertEqual((got["result_pct"], got["result_at_decision_pct"], got["result_basis"], got["exit_price"]),
                         (9.0, 10.0, "fill", 9.1))                    # bought back higher than the decision showed
        self.assertEqual((self.record(long_)["result_pct"], self.record(long_)["result_at_decision_pct"]), (-6.0, -5.0))
        self.assertNotIn("result_basis", self.record(waiting))        # not read yet: waits, stays at the decision
        self.assertEqual(self.record(waiting)["result_pct"], -2.0)
        ended = self.record(unfilled)                                 # ended without a fill: said, not invented
        self.assertEqual((ended["result_pct"], ended["result_basis"]), (-9.0, "decision"))
        self.assertEqual(ledger.apply_exit_fills(self.conn, self.log), 0)          # once
        ledger.record_exit_fills(self.conn, self.log, lambda oid: {"status": "filled", "filled_avg_price": "9.7"})
        self.assertEqual(ledger.apply_exit_fills(self.conn, self.log), 1)          # the waiting one, when it is read
        self.assertEqual((self.record(waiting)["result_pct"], self.record(waiting)["result_basis"]), (-3.0, "fill"))

    def test_the_sessions_own_closing_orders_are_read_too(self):
        out = self.root / "output"
        (out / "paper_session").mkdir(parents=True)
        actions = [{"agent": "periklis", "ticker": "PBYI", "action_type": "close_position",
                    "response_payload": {"id": "day-1"}, "refreshed_response_payload": {"id": "day-1"}},
                   {"agent": "periklis", "ticker": "LLY", "action_type": "close_position", "response_payload": {"id": "day-2"}},
                   {"agent": "periklis", "ticker": "X", "action_type": "hold"},
                   {"agent": "periklis", "ticker": "Y", "action_type": "close_position"}]
        (out / "paper_session" / "actions_20261112.jsonl").write_text(
            "\n".join(json.dumps(a) for a in actions) + "\nnot json\n", encoding="utf-8")
        extra = ledger.session_exit_orders(out, date(2026, 11, 12))
        self.assertEqual(sorted(e["exit_order_id"] for e in extra), ["day-1", "day-2"])
        counts = ledger.record_exit_fills(self.conn, self.log, lambda oid: {"status": "filled", "filled_avg_price": "5"},
                                          extra)
        self.assertEqual(counts["read"], 2)
        self.assertEqual(ledger.session_exit_orders(out, date(2026, 11, 13)), [])


if __name__ == "__main__":
    unittest.main()
