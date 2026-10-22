import json
from pathlib import Path
import tempfile
import unittest

from provider_rate_limiter import SharedProviderRateLimiter


class Clock:
    def __init__(self): self.value = 1_700_000_000.0
    def __call__(self): return self.value
    def sleep(self, seconds): self.value += seconds


class ProviderRateLimiterTests(unittest.TestCase):
    def limiter(self, folder, clock):
        policy = Path(folder) / "policy.json"
        policy.write_text(json.dumps({"version": "test", "providers": {"fmp": {
            "minimum_interval_seconds": 1, "maximum_requests_per_minute": 2,
            "maximum_requests_per_utc_day": 3, "cooldown_seconds_after_429": 60}}}))
        return SharedProviderRateLimiter(policy, Path(folder) / "rate.sqlite3", clock=clock, sleeper=clock.sleep)

    def test_interval_minute_and_cooldown_gates_do_not_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            clock = Clock(); limiter = self.limiter(folder, clock)
            self.assertTrue(limiter.permit("fmp_quote").permitted)
            denied = limiter.permit("fmp")
            self.assertFalse(denied.permitted); self.assertEqual(denied.reason_code, "minimum_interval")
            clock.value += 1
            self.assertTrue(limiter.permit("fmp_profile").permitted)
            clock.value += 1
            denied = limiter.permit("fmp")
            self.assertEqual(denied.reason_code, "minute_budget_exhausted")
            limiter.record_result("fmp", "rate_limited")
            denied = limiter.permit("fmp")
            self.assertEqual(denied.reason_code, "rate_limited")
            self.assertIsNotNone(denied.next_eligible_at)

    def test_single_short_pacing_wait_is_allowed(self):
        with tempfile.TemporaryDirectory() as folder:
            clock = Clock(); limiter = self.limiter(folder, clock)
            self.assertTrue(limiter.permit("fmp").permitted)
            self.assertTrue(limiter.permit("fmp", max_interval_wait_seconds=1.1).permitted)
            self.assertEqual(clock.value, 1_700_000_001.0)

    def test_status_exposes_remaining_budget_without_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            clock = Clock(); limiter = self.limiter(folder, clock)
            limiter.permit("fmp")
            status = limiter.status()[0]
            self.assertEqual(status["day_limit"], 3)
            self.assertEqual(status["day_remaining"], 2)
            self.assertNotIn("api_key", status)


if __name__ == "__main__": unittest.main()
