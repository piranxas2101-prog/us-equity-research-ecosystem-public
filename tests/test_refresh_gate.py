import json
import tempfile
import unittest
from pathlib import Path

import refresh_gate as gate
from companyfacts_cache import effective_mode


def ok(n):
    return [{"cik": i} for i in range(n)]


class GateCriteriaTests(unittest.TestCase):
    def test_companyfacts_needs_enough_identical_and_no_difference(self):
        self.assertEqual(gate.decide_companyfacts({"identical": ok(25), "different": []})[0], "reuse")
        self.assertIsNone(gate.decide_companyfacts({"identical": ok(5), "different": []})[0])
        self.assertEqual(gate.decide_companyfacts({"identical": ok(40), "different": ok(1)})[0], "populate")

    def test_stage1_filing_fields_must_hold_and_price_fields_stay_close(self):
        good = {"compared": 30, "fields": {
            "grossMargins": {"median": 0.0, "max": 0.0, "within_tolerance": 1.0, "n": 30},
            "forwardPE": {"median": 0.01, "max": 0.2, "within_tolerance": 0.7, "n": 30}}}
        self.assertEqual(gate.decide_stage1_info(good)[0], "reuse")
        moved = json.loads(json.dumps(good))
        moved["fields"]["grossMargins"]["within_tolerance"] = 0.7
        self.assertEqual(gate.decide_stage1_info(moved)[0], "populate")
        drifted = json.loads(json.dumps(good))
        drifted["fields"]["forwardPE"]["median"] = 0.08
        self.assertEqual(gate.decide_stage1_info(drifted)[0], "populate")
        self.assertIsNone(gate.decide_stage1_info({"compared": 3, "fields": {}})[0])

    def test_only_fields_the_rules_use_can_switch_the_store_off(self):
        shadow = {"compared": 23, "fields": {
            "returnOnEquity": {"median": 0.0, "max": 0.01, "within_tolerance": 1.0, "n": 23},
            "sharesOutstanding": {"median": 0.0, "max": 0.06, "within_tolerance": 0.91, "n": 23}}}
        self.assertEqual(gate.decide_stage1_info(shadow)[0], "reuse")  # 91% of 23: refreshed one by one, not a flip
        fields = gate.rule_fields({"fundamental_rules": [{"metric": "roe"}, {"metric": "forward_pe"}]})
        self.assertEqual(fields, {"returnOnEquity", "forwardPE"})
        self.assertEqual(gate.decide_stage1_info(shadow, fields)[0], "reuse")
        shadow["fields"]["returnOnEquity"]["within_tolerance"] = 0.75
        self.assertEqual(gate.decide_stage1_info(shadow, fields)[0], "populate")  # >20% drift flips the store
        self.assertIsNone(gate.rule_fields({}))


class EffectiveModeTests(unittest.TestCase):
    def test_gate_state_moves_between_populate_and_reuse_but_never_turns_on_an_off_store(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "modes.json"
            self.assertEqual(effective_mode("companyfacts", "populate", path), "populate")  # no file
            path.write_text(json.dumps({"companyfacts": {"mode": "reuse"}, "stage1_info": {"mode": "bogus"}}))
            self.assertEqual(effective_mode("companyfacts", "populate", path), "reuse")
            self.assertEqual(effective_mode("companyfacts", "off", path), "off")
            self.assertEqual(effective_mode("stage1_info", "populate", path), "populate")


if __name__ == "__main__":
    unittest.main()


class PerCompanyRefreshTests(unittest.TestCase):
    def test_drifted_companies_are_refreshed_and_the_store_stays_in_reuse(self):
        import tempfile
        from pathlib import Path
        from stage1_info_cache import Stage1InfoCache
        with tempfile.TemporaryDirectory() as folder:
            cache = Stage1InfoCache(Path(folder), Path(folder) / "x.db", {}, mode="reuse")
            for symbol in ("AAA", "BBB", "CCC"):
                (Path(folder) / f"{symbol}.json").write_text("{}", encoding="utf-8")
            shadow = {"compared": 25, "fields": {"earningsGrowth": {"median": 0.0, "max": 0.3,
                                                                    "within_tolerance": 0.92, "n": 25}},
                      "drifted": {"AAA": ["earningsGrowth"], "BBB": ["sharesOutstanding"]}}
            mode, reason = gate.decide_stage1_info(shadow, {"earningsGrowth"})
            self.assertEqual(mode, "reuse")
            self.assertIn("1 drifted and are refreshed one by one", reason)
            self.assertEqual(gate.refresh_drifted(cache, shadow, {"earningsGrowth"}), ["AAA"])
            self.assertEqual(sorted(p.stem for p in Path(folder).glob("*.json")), ["BBB", "CCC"])
