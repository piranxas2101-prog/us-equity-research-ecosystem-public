from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

import market_intel_db
import outcome_tracker


class OutcomeTrackerTests(unittest.TestCase):
    def test_record_setups_preserves_sector_and_information_category(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "outcomes.db"
            source = root / "giannis.json"
            source.write_text(json.dumps({
                "run_id": "run_test",
                "run_timestamp": "2026-08-25T12:00:00+00:00",
                "results": [{
                    "ticker": "ABC", "share_price": 10.0, "sector": "Technology",
                    "industry": "Software", "sector_id": "technology",
                    "industry_id": "technology.software", "classification_status": "classified",
                    "catalyst_category_id": "earnings", "catalyst_direction": "positive",
                }],
            }), encoding="utf-8")
            outcome_tracker.record_setups(db_path, source)
            with closing(market_intel_db.connect(db_path)) as conn:
                row = conn.execute("SELECT run_id, sector_id, industry_id, catalyst_category_id FROM candidate_setups").fetchone()
            self.assertEqual(row["run_id"], "run_test")
            self.assertEqual(row["sector_id"], "technology")
            self.assertEqual(row["industry_id"], "technology.software")
            self.assertEqual(row["catalyst_category_id"], "earnings")

    def test_late_refresh_uses_exact_target_session_not_latest_close(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "outcomes.db"
            with closing(market_intel_db.connect(db_path)) as conn:
                outcome_tracker.init_schema(conn)
                conn.execute(
                    """
                    INSERT INTO candidate_setups (
                        source_file, source_run_timestamp, ticker, company, observed_at,
                        baseline_price, giannis_score, giannis_tier, risk_level,
                        catalyst_bucket, rank, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "fixture.json",
                        "2026-08-03T20:00:00+00:00",
                        "TEST",
                        "Test Co",
                        "2026-08-03T20:00:00+00:00",
                        10.0,
                        7.0,
                        "Core Watchlist",
                        "Medium",
                        "UNKNOWN",
                        1,
                        "{}",
                    ),
                )
                conn.commit()

            sessions = [
                date(2026, 8, 4),
                date(2026, 8, 5),
                date(2026, 8, 6),
                date(2026, 8, 7),
                date(2026, 8, 10),
                date(2026, 8, 11),
            ]
            requested_dates: list[date] = []

            def session_provider(_baseline: datetime, _now: datetime) -> list[date]:
                return sessions

            def price_provider(_ticker: str, target: date) -> float:
                requested_dates.append(target)
                return {date(2026, 8, 4): 11.0, date(2026, 8, 10): 15.0}[target]

            outcome_tracker.refresh_outcomes(
                db_path,
                [1, 5],
                now=datetime(2026, 8, 25, tzinfo=timezone.utc),
                sessions_after_fn=session_provider,
                close_on_session_fn=price_provider,
            )

            self.assertEqual(requested_dates, [date(2026, 8, 4), date(2026, 8, 10)])
            with closing(market_intel_db.connect(db_path)) as conn:
                rows = conn.execute(
                    """
                    SELECT horizon_days, target_session_date, price, return_pct, payload_json
                    FROM price_outcomes ORDER BY horizon_days
                    """
                ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["target_session_date"], "2026-08-04")
            self.assertEqual(rows[1]["target_session_date"], "2026-08-10")
            self.assertAlmostEqual(rows[0]["return_pct"], 10.0)
            self.assertAlmostEqual(rows[1]["return_pct"], 50.0)
            self.assertEqual(json.loads(rows[1]["payload_json"])["price_adjustment"], "raw_close")

    def test_horizon_is_not_due_until_enough_completed_sessions_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "outcomes.db"
            with closing(market_intel_db.connect(db_path)) as conn:
                outcome_tracker.init_schema(conn)
                conn.execute(
                    """
                    INSERT INTO candidate_setups (
                        source_file, source_run_timestamp, ticker, observed_at,
                        baseline_price, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "fixture.json",
                        "2026-08-24T20:00:00+00:00",
                        "TEST",
                        "2026-08-24T20:00:00+00:00",
                        10.0,
                        "{}",
                    ),
                )
                conn.commit()

            calls = 0

            def no_price_call(_ticker: str, _target: date) -> float:
                nonlocal calls
                calls += 1
                return 10.0

            outcome_tracker.refresh_outcomes(
                db_path,
                [3],
                now=datetime(2026, 8, 25, tzinfo=timezone.utc),
                sessions_after_fn=lambda _baseline, _now: [date(2026, 8, 25)],
                close_on_session_fn=no_price_call,
            )
            self.assertEqual(calls, 0)
            with closing(market_intel_db.connect(db_path)) as conn:
                count = conn.execute("SELECT COUNT(*) FROM price_outcomes").fetchone()[0]
            self.assertEqual(count, 0)


    def test_session_calendar_fetched_once_per_run_and_done_setups_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "outcomes.db"
            with closing(market_intel_db.connect(db_path)) as conn:
                outcome_tracker.init_schema(conn)
                for ticker in ("AAA", "BBB", "CCC"):  # three setups from the same run
                    conn.execute(
                        "INSERT INTO candidate_setups (source_file, source_run_timestamp, ticker, observed_at, "
                        "baseline_price, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                        ("f.json", "2026-08-03T20:00:00+00:00", ticker, "2026-08-03T20:00:00+00:00", 10.0, "{}"),
                    )
                conn.commit()
            calendar_calls = 0

            def sessions(_baseline: datetime, _now: datetime) -> list[date]:
                nonlocal calendar_calls
                calendar_calls += 1
                return [date(2026, 8, 4)]

            outcome_tracker.refresh_outcomes(db_path, [1], now=datetime(2026, 8, 25, tzinfo=timezone.utc),
                                             sessions_after_fn=sessions, close_on_session_fn=lambda _t, _d: 11.0)
            self.assertEqual(calendar_calls, 1)  # not once per setup
            with closing(market_intel_db.connect(db_path)) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM price_outcomes").fetchone()[0], 3)

            calendar_calls = 0
            outcome_tracker.refresh_outcomes(db_path, [1], now=datetime(2026, 8, 26, tzinfo=timezone.utc),
                                             sessions_after_fn=sessions, close_on_session_fn=lambda _t, _d: 11.0)
            self.assertEqual(calendar_calls, 0)  # everything already recorded: no network at all

    def test_todays_session_counts_once_its_bar_is_final(self) -> None:
        # 21:50 New York (04:50 Athens): the nightly run, after the close -> today's bar is final
        self.assertEqual(outcome_tracker.completed_through(datetime(2026, 10, 6, 1, 50, tzinfo=timezone.utc)),
                         date(2026, 10, 5))
        # 10:00 New York, market open -> only yesterday is final
        self.assertEqual(outcome_tracker.completed_through(datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc)),
                         date(2026, 10, 4))

    def test_unreadable_calendar_is_reported_not_counted_as_not_due(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "outcomes.db"
            with closing(market_intel_db.connect(db_path)) as conn:
                outcome_tracker.init_schema(conn)
                conn.execute(
                    "INSERT INTO candidate_setups (source_file, source_run_timestamp, ticker, observed_at, "
                    "baseline_price, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                    ("f.json", "2026-08-03T20:00:00+00:00", "AAA", "2026-08-03T20:00:00+00:00", 10.0, "{}"),
                )
                conn.commit()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                outcome_tracker.refresh_outcomes(db_path, [1, 3], now=datetime(2026, 8, 25, tzinfo=timezone.utc),
                                                 sessions_after_fn=lambda _b, _n: None,
                                                 close_on_session_fn=lambda _t, _d: 11.0)
            self.assertIn("not_due=0", output.getvalue())
            self.assertIn("calendar_unavailable=2", output.getvalue())


if __name__ == "__main__":
    unittest.main()
