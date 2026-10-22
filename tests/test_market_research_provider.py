import unittest

from market_research_provider import ApiFirstResearchProvider
from rule_based_agent_engine import RuleBasedAgentEngine


class StubProvider(ApiFirstResearchProvider):
    def __init__(self, *, primary_ok=True):
        super().__init__(now=lambda: "2026-09-01T00:00:00+00:00")
        self.primary_ok = primary_ok
        self.yahoo_calls = []

    def _sec(self, symbol):
        return {
            "provider": "sec",
            "status": "ok",
            "filings": [{"form": "10-Q", "filing_date": "2026-08-01", "url": "https://sec.test/1"}],
        }

    def _alpaca(self, symbol):
        if not self.primary_ok:
            return {"provider": "alpaca", "status": "auth_failed"}
        return {
            "provider": "alpaca",
            "status": "ok",
            "bars": [{"t": f"d{i}", "c": 100 + i, "v": 1000} for i in range(60)],
        }

    def _polygon(self, symbol):
        return {
            "provider": "polygon",
            "status": "ok" if self.primary_ok else "rate_limited",
            "bars": [{"t": i, "c": 100 + i, "v": 1000} for i in range(60)]
            if self.primary_ok
            else [],
        }

    def _fmp(self, symbol):
        return {
            "provider": "fmp",
            "status": "ok" if self.primary_ok else "auth_failed",
            "quote": {"name": "Test Co", "price": 159, "marketCap": 1_000_000_000}
            if self.primary_ok
            else {},
            "profile": {"sector": "Test"} if self.primary_ok else {},
        }

    def _benzinga(self, symbol):
        return {
            "provider": "benzinga",
            "status": "ok" if self.primary_ok else "auth_failed",
            "news": [{"title": "API news", "url": "https://news.test/1"}]
            if self.primary_ok
            else [],
        }

    def _yahoo_fallback(self, symbol, *, need_price, need_news):
        self.yahoo_calls.append((need_price, need_news))
        return {
            "provider": "yahoo_finance",
            "status": "fallback",
            "bars": [{"t": f"y{i}", "c": 50 + i, "v": 500} for i in range(60)]
            if need_price
            else [],
            "news": [{"title": "Yahoo fallback", "url": "https://yahoo.test/1"}]
            if need_news
            else [],
        }


class MarketResearchProviderTests(unittest.TestCase):
    def test_configured_apis_precede_yahoo(self):
        provider = StubProvider(primary_ok=True)
        packet = provider.build("COST")
        self.assertEqual(packet["price"]["provider"], "alpaca")
        self.assertEqual(provider.yahoo_calls, [(False, False)])
        self.assertEqual(packet["news"][0]["title"], "API news")
        self.assertEqual(packet["source_priority"][-1], "yahoo_finance")
        self.assertNotIn("yahoo_finance_fallback_used", packet["warnings"])

    def test_yahoo_is_used_only_when_primary_data_is_unavailable(self):
        provider = StubProvider(primary_ok=False)
        packet = provider.build("COST")
        self.assertEqual(provider.yahoo_calls, [(True, True)])
        self.assertEqual(packet["price"]["provider"], "yahoo_finance")
        self.assertEqual(packet["news"][0]["title"], "Yahoo fallback")
        self.assertIn("yahoo_finance_fallback_used", packet["warnings"])

    def test_rule_engine_finishes_all_sections_in_greek(self):
        packet = StubProvider(primary_ok=True).build("COST")
        prompt = __import__("json").dumps(
            {"reply_language": "Greek", "research_packets": [packet]}, ensure_ascii=False
        )
        panel = RuleBasedAgentEngine().generate_panel(
            "research", ("babis", "giannis", "petros"), prompt
        )
        self.assertIn("ROUND 1", panel)
        self.assertIn("ROUND 2", panel)
        self.assertIn("FINAL", panel)
        self.assertIn("Εκτέλεση:** καμία", panel)


if __name__ == "__main__":
    unittest.main()
