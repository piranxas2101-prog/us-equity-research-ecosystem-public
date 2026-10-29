"""Yahoo writes share classes with a dash (BRK-B); asked with the dot it answers 404 and the values stay empty
(2026-10-29: 25 names). Every caller asks with the dash and keeps the canonical ticker as its key."""
from __future__ import annotations

from datetime import date
import unittest
from unittest import mock

import pandas as pd

from monitor_core import yahoo_symbol


def ticker_with_close(made: list[str]):
    def make(symbol):
        made.append(symbol)
        ticker = mock.MagicMock()
        ticker.history.return_value = pd.DataFrame({"Close": [10.0], "Volume": [1]},
                                                   index=pd.DatetimeIndex(["2026-10-28"]))
        ticker.info = {"floatShares": 7}
        ticker.get_info.return_value = {}
        return ticker
    return make


class YahooSymbolTests(unittest.TestCase):
    def test_share_classes_preferreds_and_plain_tickers(self):
        self.assertEqual(yahoo_symbol("BRK.B"), "BRK-B")
        self.assertEqual(yahoo_symbol("MKC.V"), "MKC-V")
        self.assertEqual(yahoo_symbol("ABR$D"), "ABR-PD")
        self.assertEqual(yahoo_symbol("AAPL"), "AAPL")

    def test_callers_ask_yahoo_with_the_dash(self):
        import yfinance as yf

        import alpaca_short_gate_data
        import outcome_tracker

        made: list[str] = []
        with mock.patch.object(yf, "Ticker", side_effect=ticker_with_close(made)):
            self.assertEqual(outcome_tracker.close_on_session("BRK.B", date(2026, 10, 28)), 10.0)
            self.assertEqual(alpaca_short_gate_data.live_info("BF.B")["floatShares"], 7)
        self.assertEqual(made, ["BRK-B", "BF-B"])

    def test_session_prices_are_keyed_by_the_canonical_ticker(self):
        import yfinance as yf

        import paper_session_report

        index = pd.DatetimeIndex(["2026-10-28 13:35", "2026-10-28 19:59"], tz="UTC")
        bars = pd.DataFrame({"Open": [100.0, 101.0], "Close": [100.5, 102.0]}, index=index)
        frame = pd.concat({"BRK-B": bars, "AAPL": bars}, axis=1)
        with mock.patch.object(yf, "download", return_value=frame) as download:
            prices = paper_session_report.session_prices(["BRK.B", "AAPL"], date(2026, 10, 28))
        self.assertEqual(download.call_args.args[0], ["AAPL", "BRK-B"])
        self.assertEqual(prices["BRK.B"], {"at_0935": 100.0, "close": 102.0})
        self.assertIn("AAPL", prices)


if __name__ == "__main__":
    unittest.main()
