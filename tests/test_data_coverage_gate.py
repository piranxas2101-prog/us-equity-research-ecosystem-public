from __future__ import annotations

import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

import pandas as pd

import us_equity_monitor


class DataCoverageGateTests(unittest.TestCase):
    @staticmethod
    def history(rows: int) -> pd.DataFrame:
        return pd.DataFrame(
            {"Close": list(range(rows))},
            index=pd.date_range("2026-01-01", periods=rows, freq="D", tz="UTC"),
        )

    def test_short_first_provider_falls_back_to_complete_second_provider(self) -> None:
        cfg = {"market_data": {"provider_priority": ["alpaca", "polygon", "yfinance"]}}
        with (
            patch.object(us_equity_monitor, "fetch_history_alpaca", return_value=self.history(5)),
            patch.object(us_equity_monitor, "fetch_history_polygon", return_value=self.history(12)),
            patch.object(us_equity_monitor, "fetch_history_yfinance") as yfinance_fetch,
        ):
            history, provider, attempts = us_equity_monitor.fetch_history_with_routing(
                "TEST", 30, cfg, min_history_days=10
            )

        self.assertEqual(len(history), 12)
        self.assertEqual(provider, "polygon")
        self.assertEqual([item["status"] for item in attempts], ["insufficient_result", "success"])
        yfinance_fetch.assert_not_called()

    def test_provider_symbol_aliases_cover_exchange_special_suffixes(self) -> None:
        self.assertEqual(
            us_equity_monitor.provider_symbol_candidates("ABR$D", "alpaca")[:1],
            ["ABR.PRD"],
        )
        self.assertEqual(
            us_equity_monitor.provider_symbol_candidates("ABR$D", "yfinance")[:1],
            ["ABR-PD"],
        )
        self.assertEqual(
            us_equity_monitor.provider_symbol_candidates("AAC.W", "alpaca")[:1],
            ["AAC.WS"],
        )
        self.assertEqual(
            us_equity_monitor.provider_symbol_candidates("UHAL.B", "yfinance")[:1],
            ["UHAL-B"],
        )

    def test_shared_rate_policy_blocks_network_call_and_records_reason(self) -> None:
        limiter = Mock()
        limiter.permit.return_value = SimpleNamespace(
            permitted=False,
            reason_code="rate_limited",
            next_eligible_at="2026-09-22T12:00:00+00:00",
        )
        cfg = {"market_data": {"provider_priority": ["polygon"]}}
        with patch.object(us_equity_monitor, "fetch_history_polygon") as fetch:
            with self.assertRaises(us_equity_monitor.MarketDataCollectionError) as caught:
                us_equity_monitor.fetch_history_with_routing(
                    "TEST", 30, cfg, min_history_days=10, rate_limiter=limiter
                )
        fetch.assert_not_called()
        record = caught.exception.as_record("TEST")
        self.assertEqual(record["provider_attempts"][0]["status"], "provider_rate_policy_blocked")
        self.assertEqual(record["provider_attempts"][0]["reason_code"], "rate_limited")

    def test_noncompany_provider_gap_is_coverage_exclusion_not_technical_error(self) -> None:
        coverage_gap = us_equity_monitor.MarketDataCollectionError(
            "No provider coverage.",
            code="all_providers_exhausted",
            retryable=True,
            details={"failure_class": "provider_coverage_gap"},
        )
        with (
            patch.object(us_equity_monitor, "fetch_snapshot", side_effect=coverage_gap),
            patch.object(us_equity_monitor, "fetch_fred_macro_context", return_value={}),
        ):
            payload = us_equity_monitor.scan_market_core(
                symbols=["TEST.W"],
                lookback_days=30,
                min_history_days=10,
                max_price=None,
                min_composite_score=None,
                min_fundamental_coverage=None,
                ma_rules=[],
                fundamental_rules=[],
                ma_weight=0.0,
                fundamental_weight=1.0,
                data_sources_cfg={},
                symbol_routes={"TEST.W": {
                    "instrument_type": "warrant",
                    "research_route": "listed_derivative_observation",
                    "company_fundamental_eligible": False,
                }},
            )
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["error_count"], 0)
        self.assertEqual(payload["coverage_exclusion_count"], 1)
        self.assertEqual(payload["coverage_exclusions"][0]["symbol"], "TEST.W")

    def test_all_short_provider_results_raise_structured_eligibility_gap(self) -> None:
        cfg = {"market_data": {"provider_priority": ["alpaca", "polygon"]}}
        with (
            patch.object(us_equity_monitor, "fetch_history_alpaca", return_value=self.history(5)),
            patch.object(us_equity_monitor, "fetch_history_polygon", return_value=self.history(8)),
        ):
            with self.assertRaises(us_equity_monitor.MarketDataCollectionError) as caught:
                us_equity_monitor.fetch_history_with_routing(
                    "TEST", 30, cfg, min_history_days=10
                )

        record = caught.exception.as_record("TEST")
        self.assertEqual(record["error_code"], "all_providers_insufficient_history")
        self.assertFalse(record["retryable"])
        self.assertEqual(record["details"]["best_provider"], "polygon")
        self.assertEqual(record["details"]["failure_class"], "eligibility_gap")
        self.assertEqual(record["details"]["retry_after_trading_sessions"], 2)
        self.assertFalse(record["details"]["immediate_retry_recommended"])
        self.assertEqual(len(record["provider_attempts"]), 2)

    def test_insufficient_history_is_an_exclusion_not_a_technical_error(self) -> None:
        history_gap = us_equity_monitor.MarketDataCollectionError(
            "Not enough history.",
            code="all_providers_insufficient_history",
            retryable=False,
            details={
                "best_row_count": 8,
                "required_rows": 10,
                "failure_class": "eligibility_gap",
                "retry_after_trading_sessions": 2,
            },
        )
        with (
            patch.object(us_equity_monitor, "fetch_snapshot", side_effect=history_gap),
            patch.object(us_equity_monitor, "fetch_fred_macro_context", return_value={}),
        ):
            payload = us_equity_monitor.scan_market_core(
                symbols=["NEW"],
                lookback_days=30,
                min_history_days=10,
                max_price=8.0,
                min_composite_score=None,
                min_fundamental_coverage=None,
                ma_rules=[],
                fundamental_rules=[],
                ma_weight=0.35,
                fundamental_weight=0.65,
                data_sources_cfg={},
            )

        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["error_count"], 0)
        self.assertEqual(payload["history_exclusion_count"], 1)
        self.assertEqual(payload["excluded_counts"]["insufficient_history"], 1)
        self.assertEqual(payload["history_exclusions"][0]["symbol"], "NEW")

    def test_sparse_fundamentals_cannot_enter_results(self) -> None:
        sparse_row = {
            "symbol": "TEST",
            "close": 2.0,
            "close_above_sma_50": 1,
            "roe": 0.25,
            "source_base": {"timing_seconds": {}},
        }
        fundamental_rules = [
            {"metric": "roe", "operator": ">=", "value": 0.12, "weight": 1.0},
            {"metric": "profit_margin", "operator": ">=", "value": 0.10, "weight": 1.0},
            {"metric": "current_ratio", "operator": ">=", "value": 1.0, "weight": 1.0},
        ]
        with (
            patch.object(us_equity_monitor, "fetch_snapshot", return_value=sparse_row),
            patch.object(us_equity_monitor, "build_scan_row", side_effect=lambda row: dict(row)),
            patch.object(us_equity_monitor, "fetch_fred_macro_context", return_value={}),
        ):
            payload = us_equity_monitor.scan_market_core(
                symbols=["TEST"],
                lookback_days=30,
                min_history_days=10,
                max_price=8.0,
                min_composite_score=None,
                min_fundamental_coverage=50.0,
                ma_rules=[
                    {
                        "metric": "close_above_sma_50",
                        "operator": ">=",
                        "value": 1,
                        "weight": 1.0,
                    }
                ],
                fundamental_rules=fundamental_rules,
                ma_weight=0.35,
                fundamental_weight=0.65,
                data_sources_cfg={},
            )

        self.assertEqual(payload["results"], [])
        self.assertEqual(
            payload["excluded_counts"]["below_min_fundamental_coverage"],
            1,
        )
        self.assertEqual(payload["filters_applied"]["min_fundamental_coverage"], 50.0)
        self.assertEqual(payload["snapshot_success_count"], 1)
        self.assertEqual(payload["data_coverage_pct"], 100.0)

    def test_resume_keeps_prior_elapsed_time_and_skips_processed_symbols(self) -> None:
        snapshot = {"symbol": "NEXT", "close": 2.0, "source_base": {"timing_seconds": {}}}
        prior = {
            "processed_count": 1,
            "results": [],
            "errors": [],
            "history_exclusions": [],
            "snapshot_success_count": 1,
            "excluded_counts": {},
            "macro_context": {},
            "runtime_summary": {"elapsed_seconds": 100.0},
        }
        with (
            patch.object(us_equity_monitor, "fetch_snapshot", return_value=snapshot) as fetch,
            patch.object(us_equity_monitor, "build_scan_row", side_effect=lambda row: dict(row)),
        ):
            payload = us_equity_monitor.scan_market_core(
                symbols=["DONE", "NEXT"],
                lookback_days=30,
                min_history_days=10,
                max_price=None,
                min_composite_score=None,
                min_fundamental_coverage=None,
                ma_rules=[],
                fundamental_rules=[],
                ma_weight=0.0,
                fundamental_weight=1.0,
                data_sources_cfg={},
                resume_state=prior,
            )

        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[0], "NEXT")
        self.assertEqual(payload["processed_count"], 2)
        self.assertGreaterEqual(payload["runtime_summary"]["elapsed_seconds"], 100.0)


if __name__ == "__main__":
    unittest.main()
