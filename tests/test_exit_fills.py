"""The real exit price of a position held over several sessions: read once it is final, unknown stays out of the
averages, a broker that does not answer leaves the exit pending, and the ledger's own records are not rewritten."""
from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
