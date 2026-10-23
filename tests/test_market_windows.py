from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import alpaca_paper_trader
import miltos_entry_stalker


ATHENS = ZoneInfo("Europe/Athens")


class MarketWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "entry_allowed_from_eastern": "09:30",
            "entry_allowed_until_eastern": "15:00",
            "entry_allowed_from_athens": "16:30",
            "entry_allowed_until_athens": "22:00",
        }

    def test_us_eu_dst_mismatch_week_uses_eastern_canonical_time(self) -> None:
        open_now, reason = miltos_entry_stalker.entry_window_open(
            self.config,
            datetime(2026, 3, 16, 15, 45, tzinfo=ATHENS),
        )
        self.assertTrue(open_now)
        self.assertIn("15:30", reason)

    def test_normal_summer_offset_is_converted_at_runtime(self) -> None:
        open_now, reason = miltos_entry_stalker.entry_window_open(
            self.config,
            datetime(2026, 6, 15, 16, 45, tzinfo=ATHENS),
        )
        self.assertTrue(open_now)
        self.assertIn("16:30", reason)

    def test_weekend_is_closed(self) -> None:
        open_now, _ = miltos_entry_stalker.entry_window_open(
            self.config,
            datetime(2026, 6, 13, 17, 0, tzinfo=ATHENS),
        )
        self.assertFalse(open_now)

    def test_entry_window_is_not_finished_before_open(self) -> None:
        self.assertFalse(
            miltos_entry_stalker.entry_window_finished(
                self.config,
                datetime(2026, 6, 15, 16, 0, tzinfo=ATHENS),
            )
        )

    def test_entry_window_is_finished_after_cutoff(self) -> None:
        self.assertTrue(
            miltos_entry_stalker.entry_window_finished(
                self.config,
                datetime(2026, 6, 15, 22, 1, tzinfo=ATHENS),
            )
        )

    def test_broker_clock_blocks_closed_market(self) -> None:
        with patch.object(
            alpaca_paper_trader,
            "fetch_market_clock",
            return_value={
                "is_open": False,
                "timestamp": "2026-07-03T13:00:00-04:00",
                "next_open": "2026-07-06T09:30:00-04:00",
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "market clock is closed"):
                alpaca_paper_trader.require_regular_market_open()

    def test_broker_clock_allows_open_market(self) -> None:
        clock = {"is_open": True, "timestamp": "2026-06-15T10:00:00-04:00"}
        with patch.object(alpaca_paper_trader, "fetch_market_clock", return_value=clock):
            self.assertIs(alpaca_paper_trader.require_regular_market_open(), clock)

    def test_entry_confirmation_rejects_prior_session_bars(self) -> None:
        bars = [
            {"t": "2026-08-24T14:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 100},
            {"t": "2026-08-24T14:15:00Z", "o": 10.5, "h": 12, "l": 10, "c": 11.5, "v": 120},
        ]
        with patch.object(alpaca_paper_trader, "fetch_intraday_bars", return_value=bars):
            result = alpaca_paper_trader.entry_confirmation(
                "APT", "long", "15Min", expected_session_date="2026-08-25"
            )

        self.assertFalse(result["entry_trigger_confirmed"])
        self.assertEqual(result["data_error_code"], "stale_last_bar")
        self.assertEqual(result["required_session_date"], "2026-08-25")

    def test_entry_confirmation_uses_only_current_session_bars(self) -> None:
        bars = [
            {"t": "2026-08-24T14:00:00Z", "o": 20, "h": 30, "l": 19, "c": 29, "v": 100},
            {"t": "2026-08-25T14:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10, "v": 100},
            {"t": "2026-08-25T14:15:00Z", "o": 10, "h": 12, "l": 10, "c": 11.5, "v": 120},
        ]
        with patch.object(alpaca_paper_trader, "fetch_intraday_bars", return_value=bars), \
                patch.object(alpaca_paper_trader, "fetch_realtime_minutes", return_value=[]):
            result = alpaca_paper_trader.entry_confirmation(
                "APT", "long", "15Min", expected_session_date="2026-08-25"
            )

        # the trigger is there, but with three bars the 20-bar volume average is unknown: it waits (2026-10-24)
        self.assertFalse(result["entry_trigger_confirmed"])
        self.assertTrue(result["confirmation_checks"]["trigger"])
        self.assertIn("participation unknown", result["entry_trigger_reason"])
        self.assertEqual(result["bars_checked"], 2)
        self.assertEqual(result["latest_bar"]["t"], "2026-08-25T14:15:00Z")


if __name__ == "__main__":
    unittest.main()
