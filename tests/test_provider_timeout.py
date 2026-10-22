import threading
import time
import unittest
from unittest import mock

import pandas as pd

import us_equity_monitor as monitor
from us_equity_monitor import ProviderCallTimeout, call_with_timeout


class CallWithTimeoutTests(unittest.TestCase):
    def tearDown(self):
        monitor._HUNG_PROVIDER_THREADS.clear()

    def test_returns_the_value_and_passes_errors_through(self):
        self.assertEqual(call_with_timeout(lambda: 7, 1.0, "ok"), 7)
        with self.assertRaises(ValueError):
            call_with_timeout(lambda: (_ for _ in ()).throw(ValueError("boom")), 1.0, "err")

    def test_a_hanging_call_is_abandoned_after_the_limit(self):
        release = threading.Event()
        started = time.perf_counter()
        with self.assertRaises(ProviderCallTimeout):
            call_with_timeout(lambda: release.wait(10), 0.2, "hang")
        self.assertLess(time.perf_counter() - started, 3.0)
        self.assertEqual(len(monitor._HUNG_PROVIDER_THREADS), 1)
        release.set()

    def test_zero_disables_the_limit(self):
        self.assertEqual(call_with_timeout(lambda: "direct", 0, "off"), "direct")

    def test_too_many_hanging_calls_fail_at_once_instead_of_piling_up(self):
        release = threading.Event()
        monitor._HUNG_PROVIDER_THREADS.extend(
            threading.Thread(target=release.wait, daemon=True) for _ in range(monitor.MAX_HUNG_PROVIDER_THREADS))
        for thread in monitor._HUNG_PROVIDER_THREADS:
            thread.start()
        started = time.perf_counter()
        with self.assertRaises(ProviderCallTimeout):
            call_with_timeout(lambda: 1, 5.0, "blocked")
        self.assertLess(time.perf_counter() - started, 1.0)
        release.set()


class SnapshotTimeoutTests(unittest.TestCase):
    def snapshot(self, ticker):
        history = pd.DataFrame({"Close": [90.0] * 260, "Volume": [1000] * 260})
        with mock.patch.object(monitor, "fetch_history_with_routing", return_value=(history, "test", [])), \
                mock.patch.object(monitor.yf, "Ticker", return_value=ticker), \
                mock.patch.object(monitor, "fetch_sec_recent_filings", return_value=[]):
            started = time.perf_counter()
            result = monitor.fetch_snapshot(
                "ABC", 30, 1, listing_context={"company_fundamental_eligible": True},
                data_sources_cfg={"openbb_research": {"enabled": False},
                                  "market_data": {"yfinance_call_timeout_seconds": 0.3}})
            return result, time.perf_counter() - started

    def tearDown(self):
        monitor._HUNG_PROVIDER_THREADS.clear()

    def test_a_hung_get_info_gives_unknown_values_not_zero_and_does_not_block(self):
        release = threading.Event()
        ticker = mock.MagicMock()
        ticker.get_info.side_effect = lambda: release.wait(10)
        ticker.fast_info = {}
        ticker.news = [{"title": "n"}]
        result, seconds = self.snapshot(ticker)
        release.set()
        self.assertLess(seconds, 5.0)
        self.assertIsNone(result["fundamentals"]["market_cap"])  # unknown, never 0
        self.assertEqual(result["source_base"]["fundamentals_source"]["status"], "provider_timeout")
        self.assertIn("get_info", result["source_base"]["provider_timeouts"])
        self.assertEqual(len(result["news"]), 1)  # the other calls are unaffected

    def test_hung_news_is_marked_and_the_rest_of_the_snapshot_is_kept(self):
        release = threading.Event()

        class Ticker:
            def get_info(self):
                return {"marketCap": 1000.0, "currentPrice": 90.0}

            fast_info = {}

            @property
            def news(self):
                release.wait(10)
                return []

        result, seconds = self.snapshot(Ticker())
        release.set()
        self.assertLess(seconds, 5.0)
        self.assertEqual(result["source_base"]["news_status"], "provider_timeout")
        self.assertEqual(result["news"], [])
        self.assertEqual(result["fundamentals"]["market_cap"], 1000.0)

    def test_a_healthy_provider_records_no_timeouts(self):
        ticker = mock.MagicMock()
        ticker.get_info.return_value = {"marketCap": 5.0, "currentPrice": 90.0}
        ticker.fast_info = {}
        ticker.news = []
        result, _ = self.snapshot(ticker)
        self.assertEqual(result["source_base"]["provider_timeouts"], [])
        self.assertEqual(result["fundamentals"]["market_cap"], 5.0)


class HistoryTimeoutTests(unittest.TestCase):
    def tearDown(self):
        monitor._HUNG_PROVIDER_THREADS.clear()

    def test_a_hung_price_request_is_recorded_as_a_timeout_and_the_next_provider_is_tried(self):
        release = threading.Event()
        good = pd.DataFrame({"Close": [1.0] * 5})
        cfg = {"market_data": {"provider_priority": ["yfinance", "polygon"], "yfinance_call_timeout_seconds": 0.3}}
        with mock.patch.object(monitor, "fetch_history_yfinance", side_effect=lambda *_a: release.wait(10)),                 mock.patch.object(monitor, "fetch_history_polygon", return_value=good):
            started = time.perf_counter()
            history, provider, attempts = monitor.fetch_history_with_routing("ABC", 30, cfg, min_history_days=5)
        release.set()
        self.assertLess(time.perf_counter() - started, 5.0)
        self.assertEqual((provider, len(history)), ("polygon", 5))
        self.assertEqual(attempts[0]["status"], "provider_timeout")


if __name__ == "__main__":
    unittest.main()
