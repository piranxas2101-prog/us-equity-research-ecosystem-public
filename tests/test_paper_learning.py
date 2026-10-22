"""Learning from paper trades: ledger records, counterfactuals, judgement only with enough trades, proposals as
candidates only, and the period reports."""
from __future__ import annotations

from contextlib import closing
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

import paper_learning as pl
import paper_trade_ledger as ledger

SESSION = date(2026, 10, 15)


def write(folder: Path, name: str, payload) -> None:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")


def session_files(out: Path) -> None:
    intent = {"agent": "yankos", "playbook_name": "support_bounce_swing", "trade_direction": "long",
              "signal_score": 10.0, "strategy_rank_pct": 0.95, "notional_usd": 250.0}
    write(out, "koulis_order_intents_latest.json", {
        "generated_at": "2026-10-15T13:35:00+00:00", "run_id": "full_research_x",
        "approved_intents": [{**intent, "ticker": "PGY"}, {**intent, "ticker": "ASND"}, {**intent, "ticker": "KARO"}],
        "blocked_intents": [{**intent, "ticker": "LEFT", "block_reason": "portfolio_order_limit"},
                            {**intent, "ticker": "OLD", "block_reason": "stale_primary_data"}]})
    write(out, "trader_paper_trading_run_latest.json", {"risk_settings": {
        "holding_period_rule": "same_day_flat", "stop_loss_pct": 0.03, "take_profit_pct": 0.06}})
    fill = lambda price, at: {"filled_avg_price": str(price), "filled_at": at}  # noqa: E731
    write(out, "yankos_paper_trades_latest.json", {"openings": [
        {"ticker": "PGY", "playbook_name": "support_bounce_swing", "trade_direction": "long", "order_status": "filled",
         "refreshed_order_response": fill(16.8, "2026-10-15T14:46:13.245975238Z")},
        {"ticker": "ASND", "playbook_name": "support_bounce_swing", "trade_direction": "long", "order_status": "filled",
         "refreshed_order_response": fill(233.846, "2026-10-15T14:05:48Z")},
        {"ticker": "KARO", "playbook_name": "support_bounce_swing", "trade_direction": "long",
         "order_status": "waiting_confirmation"}]})
    write(out, "paper_session/actions_20261015.jsonl", json.dumps({
        "agent": "yankos", "ticker": "PGY", "action_type": "close_position", "position_plpc": 0.066,
        "decision_reason": "yankos closes PGY because today's paper positions must be flat before the session ends.",
        "refreshed_response_payload": fill(17.91, "2026-10-15T19:56:05Z")}) + "\n")
    write(out, "preopen/preopen_check_latest.json", {"checked_at": "2026-10-15T13:02:00+00:00", "rows": [
        {"agent": "yankos", "ticker": "PGY", "playbook": "support_bounce_swing", "direction": "long", "decision": "kept",
         "gap_pct": -2.38},
        {"agent": "yankos", "ticker": "TSM", "playbook": "rsi2_mean_reversion", "direction": "long",
         "decision": "demoted_to_watch", "gap_pct": -3.39, "demote_reasons": ["gap"]}]})
    write(out, "macro_environment_latest.json", {"market": {"state": "up_calm"}})


class LedgerTests(unittest.TestCase):
    def test_a_session_becomes_trades_and_the_decisions_that_did_not_trade(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            session_files(out)
            broker = lambda day: [  # noqa: E731 - only the missing ASND exit comes from the broker
                {"symbol": "ASND", "side": "sell", "status": "filled", "filled_avg_price": "226.97",
                 "filled_at": "2026-10-15T15:10:00Z"}]
            records = {(r["kind"], r["ticker"]): r for r in ledger.build_records(out, SESSION, orders=broker)}
            pgy = records[("trade", "PGY")]
            self.assertEqual((pgy["exit_reason"], pgy["result_pct"], pgy["market_state"]), ("end_of_day", 6.607, "up_calm"))
            self.assertEqual(pgy["preopen"]["gap_pct"], -2.38)
            asnd = records[("trade", "ASND")]
            self.assertEqual((asnd["exit_reason"], asnd["exit_reason_inferred"], asnd["result_pct"]),
                             ("stop_loss", True, -2.94))
            self.assertIn(("approved_not_entered", "KARO"), records)
            self.assertIn(("preopen_demoted", "TSM"), records)
            self.assertIn(("koulis_left_out", "LEFT"), records)
            self.assertNotIn(("koulis_left_out", "OLD"), records)           # not a portfolio-limit block
            self.assertEqual(ledger.build_records(out, date(2026, 10, 14)), [])  # no session that day

    def test_an_idea_the_recheck_at_the_open_restored_is_a_candidate_not_a_take_down(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            session_files(out)
            write(out, "preopen/yankos_strategy_preopen_latest.json", {"results": [
                {"ticker": "TSM", "playbook_name": "rsi2_mean_reversion", "trade_direction": "long",
                 "signal_verdict": "candidate", "signal_score": 8.0, "execution_eligible": True,
                 "open_recheck": {"decision": "restored"}}]})
            records = {(r["kind"], r["ticker"]): r for r in ledger.build_records(out, SESSION, orders=lambda day: [])}
            self.assertNotIn(("preopen_demoted", "TSM"), records)
            self.assertEqual(records[("shadow_candidate", "TSM")]["categories"]["open_recheck"], "restored")
            self.assertEqual(records[("trade", "PGY")]["categories"]["open_recheck"], "none")

    def test_a_complete_record_is_never_rewritten_and_an_incomplete_one_is_completed(self):
        with tempfile.TemporaryDirectory() as folder, closing(ledger.connect(Path(folder) / "l.db")) as conn:
            base = {"record_id": "ptr_1", "session_date": "2026-10-15", "kind": "trade", "complete": False}
            self.assertEqual(ledger.store(conn, [base])["inserted"], 1)
            self.assertEqual(ledger.store(conn, [{**base, "complete": True, "result_pct": 1.0}])["completed"], 1)
            self.assertEqual(ledger.store(conn, [{**base, "complete": True, "result_pct": 9.0}])["kept"], 1)
            self.assertEqual(ledger.load(conn)[0]["result_pct"], 1.0)


def trade(day: int, outcome: float, playbook: str = "rsi2_mean_reversion", kind: str = "trade") -> dict:
    return {"record_id": f"r{day}{playbook}{outcome}{kind}", "session_date": f"2026-11-{day:02d}", "kind": kind,
            "agent": "yankos", "ticker": "T", "playbook": playbook, "direction": "long", "holding_class": "day",
            "market_state": "up_calm", "exit_reason": "end_of_day", "outcome_pct": outcome, "excess_pct": outcome - 0.2}


class EvaluationTests(unittest.TestCase):
    def test_few_trades_are_insufficient_and_nothing_is_proposed(self):
        evaluation = pl.evaluate([trade(1, 2.0), trade(2, -1.0)])
        self.assertEqual(evaluation["overall"]["status"], "insufficient_sample")
        self.assertEqual(pl.proposals(evaluation), [])
        self.assertFalse(evaluation["active_decisions_changed"])

    def test_a_losing_strategy_with_enough_trades_becomes_a_candidate_proposal(self):
        rows = [trade(1 + i % 12, -1.0 if i % 3 else 0.5) for i in range(36)]
        evaluation = pl.evaluate(rows)
        cell = evaluation["strategies"]["yankos · rsi2_mean_reversion · long"]
        self.assertEqual(cell["status"], "judged")
        found = pl.proposals(evaluation)
        self.assertTrue(any(p["criterion_id"].startswith("live_strategy_underperforms:yankos:rsi2") for p in found))

    def test_the_control_team_is_judged_from_what_would_have_happened(self):
        traded = [trade(1 + i % 12, -0.5, kind="shadow_candidate") for i in range(36)]  # what the check kept
        demoted = [trade(1 + i % 12, 1.5, kind="preopen_demoted") for i in range(36)]
        check = {c["check"]: c for c in pl.evaluate(traded + demoted)["control_team"]}["preopen_check"]
        self.assertEqual(check["verdict"], "the other side did better")
        self.assertTrue(any(p["criterion_id"] == "control_team:preopen_check"
                            for p in pl.proposals(pl.evaluate(traded + demoted))))

    def test_counterfactuals_come_from_the_store_once_the_session_is_there(self):
        with tempfile.TemporaryDirectory() as folder, closing(ledger.connect(Path(folder) / "l.db")) as conn:
            record = {**trade(15, 0.0, kind="preopen_demoted"), "session_date": "2026-10-15", "ticker": "TSM"}
            prices = lambda t, d: (100.0, 103.0) if t == "TSM" else (500.0, 505.0)  # noqa: E731
            self.assertEqual(pl.fill_counterfactuals(conn, [record], lambda t, d: (None, None)), 0)  # not stored yet
            self.assertEqual(pl.fill_counterfactuals(conn, [record], prices), 1)
            pl.attach_counterfactuals(conn, [record])
            self.assertEqual((record["open_close_pct"], record["spy_open_close_pct"], record["excess_pct"]), (3.0, 1.0, 2.0))


def shadow(day: int, sector: str, beats: bool, i: int) -> dict:
    return {"record_id": f"s{day}-{sector}-{i}", "session_date": f"2026-11-{day:02d}", "kind": "shadow_candidate",
            "agent": "periklis", "ticker": f"T{i}", "playbook": "momentum_12_1", "direction": "long",
            "outcome_pct": 1.0 if beats else -1.0, "excess_pct": 0.5 if beats else -0.5,
            "categories": {"sector": sector, "piotroski": "unknown"}}


class CategoryTests(unittest.TestCase):
    DAYS = [2, 3, 4, 5, 6, 9, 10, 11, 12, 13, 16, 17]  # three weeks of sessions

    def test_a_real_and_repeated_difference_is_found(self):
        rows = []
        for day in self.DAYS:
            rows += [shadow(day, "Technology", i < 4, i) for i in range(5)]     # 80% beat SPY
            rows += [shadow(day, "Industrials", i < 2, i + 5) for i in range(5)]  # 40% beat SPY
        found = pl.category_findings(rows)["findings"]
        tech = [f for f in found if f["value"] == "Technology"]
        self.assertEqual(len(tech), 1)
        self.assertEqual((tech[0]["effect_pct_points"], tech[0]["weeks_same_direction"]), (40.0, 3))
        self.assertFalse([f for f in found if f["value"] == "unknown"])           # unknown is never a finding
        proposed = [p["criterion_id"] for p in pl.proposals({"strategies": {}, "control_team": [],
                                                              "category_findings": {"findings": tech}})]
        self.assertEqual(proposed, ["category:prefer:periklis:momentum_12_1:long:sector=Technology"])

    def test_a_difference_from_a_single_lucky_week_is_not_a_finding(self):
        rows = []
        for day in self.DAYS:
            lucky = day <= 6  # Technology beats only in the first week
            rows += [shadow(day, "Technology", lucky or i < 2, i) for i in range(5)]
            rows += [shadow(day, "Industrials", i < 2, i + 5) for i in range(5)]
        result = pl.category_findings(rows)
        self.assertEqual([f for f in result["findings"] if f["value"] == "Technology"], [])

    def test_too_few_records_are_not_tested(self):
        rows = [shadow(day, "Technology", True, 0) for day in self.DAYS[:3]]
        self.assertEqual(pl.category_findings(rows)["tests"], 0)


class ReportTests(unittest.TestCase):
    def test_periods_and_what_is_due_each_night(self):
        self.assertEqual(pl.period_bounds("week", date(2026, 10, 16))[:2], (date(2026, 10, 12), date(2026, 10, 18)))
        self.assertEqual(pl.period_bounds("quarter", date(2026, 11, 5))[2], "2026-Q4")
        self.assertEqual(pl.period_bounds("quarter", date(2026, 12, 31))[1], date(2026, 12, 31))
        self.assertEqual(pl.due_periods(date(2026, 10, 17)), [("week", date(2026, 10, 16))])        # a Saturday
        self.assertEqual([p for p, _ in pl.due_periods(date(2027, 1, 1))], ["month", "quarter", "year"])
        self.assertEqual(pl.due_periods(date(2026, 10, 20)), [])

    def test_the_report_states_what_it_cannot_judge_yet(self):
        evaluation = pl.evaluate([trade(1, 2.0)])
        text = pl.render_report("week", "2026-W45", date(2026, 11, 2), date(2026, 11, 8), evaluation,
                                pl.trade_review([{**trade(1, 2.0), "agent": "yankos"}]), [])
        self.assertIn("ανεπαρκές δείγμα", text)
        self.assertIn("Sokratis", text)
        self.assertIn("Καμία ακόμη", text)


if __name__ == "__main__":
    unittest.main()
