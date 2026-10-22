"""Press-release reader: figures only when anchored on the SEC's prior-year quarter, whatever the column layout;
tags for the journal; the feature registry offers every source's tags with unknown kept unknown."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import feature_registry as fr
import press_release_reader as prr


def table(header: list[str], rows: list[list[str]]) -> str:
    def tr(cells: list[str]) -> str:
        return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
    return "<table>" + tr(header) + tr(["(in millions, except per share)"]) + "".join(tr(r) for r in rows) + "</table>"


ANCHORS = {"revenue": 11_510_307_000.0, "net_income": 2_546_916_000.0, "eps_diluted": 1.20}


class ReadTests(unittest.TestCase):
    def test_a_row_of_quarters_reads_the_same_quarter_one_year_on(self):  # Netflix's letter layout
        html = table(["", "Q3'25", "Q4'25", "Q1'26", "Q2'26", "Q3'26", "Q4'26 Forecast"],
                     [["Revenue", "$ 11,510", "12,051", "12,250", "12,560", "12,874", "13,442"],
                      ["Net Income", "2,547", "2,419", "5,283", "3,401", "3,664", "2,787"]])
        got = prr.read_tables(html, ANCHORS)
        self.assertEqual((got["revenue"]["current"], got["net_income"]["current"]), (12_874e6, 3_664e6))
        self.assertEqual(got["revenue"]["layout"], "quarter_row")

    def test_current_first_and_prior_first_tables(self):
        current_first = table(["", "Three Months Ended September 30, 2026", "2025"],
                              [["Total revenues", "12,874", "11,510"], ["Diluted", "$ 1.45", "$ 1.20"]])
        got = prr.read_tables(current_first, ANCHORS)
        self.assertEqual((got["revenue"]["current"], got["eps_diluted"]["current"]), (12_874e6, 1.45))
        prior_first = table(["", "2025", "2026"], [["Net sales", "11,510", "12,874"]])
        self.assertEqual(prr.read_tables(prior_first, ANCHORS)["revenue"]["current"], 12_874e6)

    def test_a_previous_quarter_column_is_never_taken_for_the_new_one(self):  # Intuitive Surgical's layout
        html = table(["", "Q3 2026", "Q2 2026", "Q3 2025"], [["Total revenue", "3,048.7", "2,892.3", "2,505.1"]])
        anchors = {"revenue": 2_505.1e6, "revenue_last": 2_892.3e6}
        self.assertEqual(prr.read_tables(html, anchors)["revenue"]["current"], 3_048.7e6)

    def test_year_to_date_columns_are_never_read_and_eps_follows_earnings(self):  # Pegasystems' layout
        html = table(["", "Three months 2026", "2025", "Nine months 2026", "2025"],
                     [["Net income", "14,092", "43,364", "60,190", "158,863"], ["Diluted", "0.08", "0.24", "0.35", "0.86"]])
        anchors = {"net_income": 43_364e3, "eps_diluted": 0.24, "eps_diluted_last": 0.08}
        got = prr.read_tables(html, anchors)
        self.assertEqual(got["net_income"]["current"], 14_092e3)
        self.assertNotIn("eps_diluted", got)  # 0.08 equals the last quarter; 0.35 is nine months: unknown

    def test_a_year_to_date_or_segment_cell_is_not_taken_for_the_quarter(self):
        self.assertFalse(prr.plausible("revenue", 7_051.9, 2_133.3))   # nine months against a quarter
        self.assertFalse(prr.plausible("revenue", 147.6, 1_544.9))     # a segment line
        self.assertFalse(prr.plausible("revenue", 12_470.0, 12_470.0))  # the prior cell again
        self.assertFalse(prr.plausible("eps_diluted", 15.8, 1.22))
        self.assertTrue(prr.plausible("revenue", 3_048.7, 2_505.1))
        self.assertTrue(prr.plausible("net_income", 32.4, 7.1))
        self.assertFalse(prr.plausible("net_income", 210.0, 5_152.0))  # another table's line

    def test_without_an_anchor_or_with_a_foreign_magnitude_nothing_is_read(self):
        html = table(["", "2026", "2025"], [["Revenue", "999", "888"], ["Net income", "2.8", "2,547"]])
        self.assertEqual(prr.read_tables(html, ANCHORS), {})   # 888 is not the SEC's 11,510; 2.8 is a per-share cell
        self.assertEqual(prr.read_tables(html, {}), {})

    def test_numbers_and_tags(self):
        self.assertEqual((prr.to_number("(1,234)"), prr.to_number("$ 5.87"), prr.to_number("—")), (-1234.0, 5.87, None))
        tags = prr.press_tags({"revenue": {"current": 112.0, "prior": 100.0},
                               "net_income": {"current": 5.0, "prior": -3.0}})
        self.assertEqual(tags, {"pr_revenue_growth": "10..25%", "pr_profit": "turned_profit", "pr_eps": None,
                                "pr_verified": "yes"})

    def test_the_index_page_gives_items_and_the_exhibit(self):
        html = ('<div class="formGrouping">Items Item 2.02: Results of Operations Item 9.01: Exhibits</div>'
                '<table class="tableFile"><tr><td>2</td><td>Press</td><td><a href="/Archives/x/ex991.htm">ex991.htm'
                '</a></td><td>EX-99.1</td><td>10</td></tr></table>')
        self.assertEqual(prr.read_index(html), {"items": ["2.02", "9.01"],
                                                "exhibit": "https://www.sec.gov/Archives/x/ex991.htm"})


class ForeignFilerTests(unittest.TestCase):
    """LAES and other foreign filers: half-year 6-K releases, no interim XBRL to anchor on."""

    def test_a_6k_is_read_from_the_years_in_its_header_and_marked_unverified(self):
        html = table(["", "H1 2026", "H1 2025"], [["Revenue", "11,151", "4,825"], ["Net loss", "(27,789)", "(20,030)"]])
        got = prr.read_unanchored(html)
        self.assertEqual((got["revenue"]["current"], got["revenue"]["anchored"]), (11_151.0, False))
        self.assertEqual(got["net_income"]["current"], -27_789.0)
        tags = prr.press_tags(got)
        self.assertEqual((tags["pr_profit"], tags["pr_verified"]), ("loss", "no"))
        self.assertEqual(prr.press_tags({})["pr_verified"], None)

    def test_without_two_consecutive_years_nothing_is_read(self):
        self.assertEqual(prr.read_unanchored(table(["", "Q2", "Q1"], [["Revenue", "10", "9"]])), {})

    def test_a_6k_without_items_is_read_by_its_exhibit(self):
        pages = {"https://www.sec.gov/Archives/edgar/data/9/b1/b-1-index.htm":
                 '<table class="tableFile"><tr><td>1</td><td>x</td><td><a href="/h.htm">h</a></td>'
                 '<td>EX-99.1</td></tr></table>',
                 "https://www.sec.gov/h.htm": table(["", "2026", "2025"], [["Revenue", "11,151", "4,825"]])}
        conn = sqlite3.connect(":memory:")
        done = prr.update(conn, [("b-1", "LAES", "9", "2026-09-11", "6-K")], pages.__getitem__)
        self.assertEqual(done[0]["numbers"]["revenue"]["anchored"], False)


class UpdateTests(unittest.TestCase):
    def test_only_results_8ks_are_read_and_each_once(self):
        pages = {"https://www.sec.gov/Archives/edgar/data/1/a1/a-1-index.htm":
                 '<div class="formGrouping">Item 2.02</div><table class="tableFile"><tr><td>1</td><td>x</td>'
                 '<td><a href="/e.htm">e</a></td><td>EX-99.1</td></tr></table>',
                 "https://www.sec.gov/e.htm": table(["", "2026", "2025"], [["Revenue", "12,874", "11,510"]]),
                 "https://www.sec.gov/Archives/edgar/data/1/a2/a-2-index.htm": '<div class="formGrouping">Item 5.02</div>'}
        conn = sqlite3.connect(":memory:")
        items = [("a-1", "AAA", "1", "2026-10-20"), ("a-2", "AAA", "1", "2026-10-19")]
        done = prr.update(conn, items, pages.__getitem__, anchors=lambda cik, filed: ANCHORS)
        self.assertEqual([d["accession"] for d in done], ["a-1"])
        self.assertEqual(dict(conn.execute("SELECT accession, status FROM press_release").fetchall()),
                         {"a-1": "read", "a-2": "not_results"})
        self.assertEqual(prr.update(conn, items, pages.__getitem__, anchors=lambda cik, filed: ANCHORS), [])


class RegistryTests(unittest.TestCase):
    def test_every_provider_adds_its_tags_and_unknown_stays_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "output"
            out.mkdir()
            (out / "periodic_results_latest.json").write_text(json.dumps({"companies": {"AAA": {"tags": {
                "revenue_trend": "accelerating", "cash_runway_months": 8.0}}}}), encoding="utf-8")
            (Path(folder) / "data").mkdir()
            conn = sqlite3.connect(Path(folder) / "data" / "earnings_journal.db")
            conn.execute("CREATE TABLE earnings_event (ticker TEXT, filed TEXT, form TEXT, tags_json TEXT)")
            conn.execute("INSERT INTO earnings_event VALUES ('AAA', '2026-10-20', '8-K 2.02', ?)",
                         (json.dumps({"pr_revenue_growth": ">=25%", "pr_profit": "profit"}),))
            conn.commit()
            conn.close()
            tags = fr.load(out)("AAA", date(2026, 10, 21))
            self.assertEqual((tags["results_revenue_trend"], tags["results_runway"], tags["results_dilution"]),
                             ("accelerating", "<12m", "unknown"))
            self.assertEqual((tags["results_this_week"], tags["pr_revenue_growth"]), ("yes", ">=25%"))
            self.assertEqual(fr.load(out)("BBB", date(2026, 10, 21))["results_this_week"], "no")


if __name__ == "__main__":
    unittest.main()
