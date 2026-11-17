"""Jarvis daily report: Greek sections in a fixed order; every unknown printed as unknown with its reason."""
from __future__ import annotations

import unittest

from jarvis_report import SECTIONS, render

RECORD = {
    "schema": "ecosystem.jarvis_pass.v1", "kind": "after-session", "day": "2026-11-16",
    "started_at": "2026-11-16T23:15:00+02:00", "finished_at": "2026-11-16T23:16:00+02:00",
    "sections": {
        "nightly": {"steps": [{"name": "run", "state": "failed", "detail": "το run r είναι failed, στο βήμα giannis",
                               "diagnosis": "rate_limit", "evidence": ["output/x.json"]}]},
        "session": {"steps": [{"name": "session_report", "state": "missing", "detail": "δεν υπάρχει αναφορά",
                               "diagnosis": None, "evidence": []}]},
        "checks": {"results": [{"id": "T3_volume_basis", "title": "T3", "state": "needs_lab", "verdict": None,
                                "detail": "δεν υπάρχει εργαστήριο", "evidence": [], "rules_review": "T3"}]},
        "learning": {"error": "RuntimeError: boom"},
        "proposals": {"open": [{"proposal_id": "prop_1", "title": "Αλλαγή Α", "change": "κάτι",
                                "evidence": {"verdict": "confirmed", "n": 12}, "source": "paper_learning"}]},
    },
}


class ReportTests(unittest.TestCase):
    def test_sections_in_order(self):
        md, _ = render(RECORD)
        positions = [md.index(title) for title in SECTIONS]
        self.assertEqual(positions, sorted(positions))

    def test_problems_are_listed(self):
        md, data = render(RECORD)
        problems = md[md.index("Τι πήγε στραβά"):md.index("Έλεγχοι")]
        self.assertIn("giannis", problems)
        self.assertIn("όριο αιτήσεων", problems)  # rate_limit in Greek
        self.assertIn("RuntimeError", problems)
        self.assertEqual(data["problems"], 3)  # failed run, missing session report, learning error

    def test_missing_section_is_unknown(self):
        record = {**RECORD, "sections": {}}
        md, _ = render(record)
        self.assertGreaterEqual(md.count("άγνωστο"), 2)

    def test_proposal_is_shown_with_evidence(self):
        md, data = render(RECORD)
        self.assertIn("Αλλαγή Α", md)
        self.assertIn("confirmed", md)
        self.assertEqual(data["open_proposals"], 1)

    def test_json_keeps_the_record(self):
        _, data = render(RECORD)
        self.assertEqual((data["day"], data["sections"]["checks"]["results"][0]["state"]), ("2026-11-16", "needs_lab"))


if __name__ == "__main__":
    unittest.main()


class AutoChangesSectionTests(unittest.TestCase):
    def test_auto_changes_are_listed(self):
        record = {**RECORD, "sections": {**RECORD["sections"], "tuning": {"changes": [
            {"setting": "max_total_chars", "old": 1800, "new": 1440, "action": "lowered", "tier": "AUTO",
             "reason": "4 από τις 20 απαντήσεις του μοντέλου έληξαν"}]}}}
        md, _ = render(record)
        part = md[md.index("Τι άλλαξα μόνος μου"):]
        self.assertIn("1800 → 1440", part)
        self.assertIn("έληξαν", part)

    def test_no_tuning_section_says_nothing_changed(self):
        md, _ = render(RECORD)
        self.assertIn("Τι άλλαξα μόνος μου (AUTO)", md)


class OwnerSectionTests(unittest.TestCase):
    def test_owner_section_comes_first(self):
        record = {**RECORD, "sections": {**RECORD["sections"], "owner": {"story": "Περιμένουν εσένα 2 είδη αποφάσεων: Χ."}}}
        md, _ = render(record)
        self.assertLess(md.index("Τι περιμένει από σένα"), md.index("Νυχτερινό run"))
        self.assertIn("Περιμένουν εσένα 2 είδη αποφάσεων", md)


class WaitingLessonsTests(unittest.TestCase):
    """Owner 2026-11-18: new lessons wait as candidates; the report shows how many, unknown when unreadable."""

    def lines(self, waiting):
        from jarvis_report import _ecosystem_lines

        return [line for line in _ecosystem_lines({"market": None, "lessons": None, "waiting_lessons": waiting})
                if "αναμονή" in line]

    def test_counts_per_task(self):
        (line,) = self.lines({"paper_trading": 3, "earnings_journal": 1})
        self.assertIn("σε αναμονή για επόμενη περίοδο: 4 (earnings_journal 1, paper_trading 3)", line)
        self.assertIn("μόνο με δική σου απόφαση", line)

    def test_none_waiting_is_zero_and_unreadable_is_unknown(self):
        self.assertIn(": 0.", self.lines({})[0])
        self.assertIn("άγνωστο", self.lines(None)[0])

    def test_older_records_without_the_field_print_nothing(self):
        from jarvis_report import _ecosystem_lines

        self.assertFalse([x for x in _ecosystem_lines({"market": None, "lessons": None}) if "αναμονή" in x])
