"""Tests for the local-Ollama filing evidence extractor, using a fake model."""
from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import filing_llm_extractor as fx

CONFIG = {"section_scan_chars": 40000, "business_lead_sentences": 2, "sentences_per_field": 3}

BUSINESS_SENTENCES = [
    "ACME provides cloud software for hospitals across the United States.",
    "We generate substantially all of our revenue from annual subscriptions sold to hospital systems.",
    "Our headquarters are located in Denver, Colorado, and we have offices in three states.",
    "Revenue growth depends on adding hospital customers and renewing existing contracts.",
    "Competition in our market comes from large electronic health record vendors and startups.",
]
RISK_SENTENCES = [
    "Changes in healthcare regulation could reduce demand for software across the industry.",
    "A small number of our customers account for a large share of our revenue.",
    "Our offices are leased under long-term agreements that expire in future years.",
]
FILLER = "<p>" + " ".join(["This paragraph describes routine operational matters in considerable length."] * 40) + "</p>"


def filing_html(business_heading: str = "<p>Item 1. Business</p>") -> str:
    toc = ("<table><tr><td>Item 1.</td><td>Business</td><td>4</td></tr>"
           "<tr><td>Item 1A.</td><td>Risk Factors</td><td>9</td></tr>"
           "<tr><td>Item 2.</td><td>Properties</td><td>20</td></tr></table>")
    business = "".join(f"<p>{sentence}</p>" for sentence in BUSINESS_SENTENCES)
    cross_ref = '<p>For more information, see the discussion under "Item 1A. Risk Factors" below.</p>'
    risks = "".join(f"<p>{sentence}</p>" for sentence in RISK_SENTENCES)
    return (f"<html><body>{toc}{business_heading}{business}{cross_ref}{FILLER}"
            f"<p>Item 1A. Risk Factors</p>{risks}{FILLER}<p>Item 1B. Unresolved Staff Comments</p><p>None.</p>"
            f"<p>Item 2. Properties</p><p>We lease offices.</p></body></html>")


class FakeClient:
    def __init__(self, responses: dict | None = None, installed=None, fail: bool = False, reachable: bool = True) -> None:
        self.responses = responses or {}
        self.installed = installed if installed is not None else {"small-model:latest": "sha256:abc"}
        self.reachable = reachable
        self.fail = fail
        self.calls = 0

    def models(self):
        return self.installed if self.reachable else None

    def generate(self, *, model, system, prompt, schema):
        self.calls += 1
        if self.fail:
            raise TimeoutError("model too slow")
        section = "risk_factors" if "Sentences (risk factors)" in prompt else "business"
        return json.dumps(self.responses.get(section, {}))


def number_of(excerpt: fx.Excerpt, fragment: str) -> int:
    return next(index for index, sentence in enumerate(excerpt.sentences, start=1) if fragment in sentence)


class ParsingTests(unittest.TestCase):
    def test_sections_skip_toc_and_ignore_cross_references(self) -> None:
        doc = fx.parse_filing(filing_html())
        sections = fx.locate_sections(doc)
        business = doc.text[slice(*sections["business"])]
        self.assertTrue(business.startswith("Item 1. Business ACME provides"))
        self.assertIn("see the discussion under", business)  # the cross-reference stays inside Business
        self.assertIn("healthcare regulation", doc.text[slice(*sections["risk_factors"])])

    def test_heading_split_across_two_blocks(self) -> None:
        doc = fx.parse_filing(filing_html("<div>Item 1.</div><div>Business</div>"))
        self.assertIn("business", fx.locate_sections(doc))

    def test_twenty_f_layout(self) -> None:
        html = ("<p>Item 3. Key Information</p><p>D. Risk Factors</p>"
                + "".join(f"<p>{s}</p>" for s in RISK_SENTENCES) + FILLER
                + "<p>Item 4. Information on the Company</p>"
                + "".join(f"<p>{s}</p>" for s in BUSINESS_SENTENCES) + FILLER
                + "<p>Item 4A. Unresolved Staff Comments</p><p>Item 5. Operating and Financial Review</p>")
        self.assertEqual(set(fx.locate_sections(fx.parse_filing(html))), {"business", "risk_factors"})

    def test_short_risk_section_stays_unknown(self) -> None:
        html = filing_html().replace("".join(f"<p>{s}</p>" for s in RISK_SENTENCES) + FILLER,
                                     "<p>Smaller reporting companies are not required to provide this information.</p>")
        self.assertNotIn("risk_factors", fx.locate_sections(fx.parse_filing(html)))

    def test_excerpts_keep_lead_and_keyword_sentences_only(self) -> None:
        excerpts = {e.section: e for e in fx.build_excerpts(fx.parse_filing(filing_html()), CONFIG)}
        business = excerpts["business"]
        self.assertIn("Competition in our market", business.text)
        self.assertNotIn("routine operational matters", business.text)
        self.assertNotIn("leased under long-term", excerpts["risk_factors"].text)


class ResolutionTests(unittest.TestCase):
    excerpt = fx.Excerpt("business", ("business_model",), ("First sentence about the company.", "Second one."))

    def test_numbers_map_to_our_own_sentences(self) -> None:
        self.assertEqual(fx.resolve_sentences([2, 2], self.excerpt), ("verified", [2]))

    def test_out_of_range_or_non_integer_is_rejected(self) -> None:
        for bad in ([0], [3], ["1"], [True], [1.5]):
            self.assertEqual(fx.resolve_sentences(bad, self.excerpt)[0], "invalid_sentence_number")

    def test_empty_list_stays_unknown(self) -> None:
        self.assertEqual(fx.resolve_sentences([], self.excerpt), ("not_in_excerpt", []))

    def test_invalid_json(self) -> None:
        self.assertIsNone(fx.parse_output("not json", ("business_model",)))
        self.assertEqual(fx.parse_output('{"business_model": null}', ("business_model",)), {"business_model": []})

    def test_prompt_numbers_sentences(self) -> None:
        prompt = fx.build_prompt(self.excerpt)
        self.assertIn("[1] First sentence about the company.", prompt)
        self.assertIn("[2] Second one.", prompt)


def good_responses() -> dict:
    excerpts = {e.section: e for e in fx.build_excerpts(fx.parse_filing(filing_html()), CONFIG)}
    b, r = excerpts["business"], excerpts["risk_factors"]
    return {
        "business": {"business_model": [number_of(b, "annual subscriptions")],
                     "revenue_drivers": [99],  # does not exist -> rejected
                     "competitive_position": []},
        "risk_factors": {"industry_risks": [number_of(r, "healthcare regulation")],
                         "company_specific_risks": [number_of(r, "small number of our customers")]},
    }


class ExtractTests(unittest.TestCase):
    def test_results_use_filing_text_not_model_text(self) -> None:
        excerpt = next(e for e in fx.build_excerpts(fx.parse_filing(filing_html()), CONFIG) if e.section == "business")
        results = {row["field"]: row for row in fx.extract_excerpt(FakeClient(good_responses()), "m", excerpt, CONFIG)}
        self.assertEqual(results["business_model"]["status"], "verified")
        self.assertIn("annual subscriptions sold to hospital systems", results["business_model"]["quote"])
        self.assertEqual(results["revenue_drivers"]["status"], "invalid_sentence_number")
        self.assertEqual(results["competitive_position"]["status"], "not_in_excerpt")

    def test_model_error_is_recorded_not_raised(self) -> None:
        excerpt = fx.build_excerpts(fx.parse_filing(filing_html()), CONFIG)[0]
        results = fx.extract_excerpt(FakeClient(fail=True), "m", excerpt, CONFIG)
        self.assertTrue(all(row["status"] == "model_error" for row in results))


class ReadinessTests(unittest.TestCase):
    def test_ollama_not_running_stops_without_starting_it(self) -> None:
        with self.assertRaises(fx.OllamaNotReady) as ctx:
            fx.check_model(FakeClient(reachable=False), "small-model", "http://127.0.0.1:11434")
        self.assertIn("never starts Ollama", str(ctx.exception))

    def test_missing_model_says_how_to_install(self) -> None:
        with self.assertRaises(fx.OllamaNotReady) as ctx:
            fx.check_model(FakeClient(installed={}), "small-model", "h")
        self.assertIn("ollama pull small-model", str(ctx.exception))

    def test_no_model_configured(self) -> None:
        with self.assertRaises(fx.OllamaNotReady):
            fx.check_model(FakeClient(), "", "h")

    def test_extractor_uses_only_local_ollama(self) -> None:
        source = Path(fx.__file__).read_text(encoding="utf-8").lower()
        for forbidden in ("anthropic", "openai", "api.claude", "import requests"):
            self.assertNotIn(forbidden, source)


class RunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.registry = root / "registry.db"
        self.cache = root / "cache.sqlite3"
        self.url = "https://www.sec.gov/Archives/acme-10k.htm"
        html = filing_html().encode("utf-8")
        reg = sqlite3.connect(self.registry)
        reg.execute("CREATE TABLE software_filing_evidence_candidate(ticker TEXT, cik TEXT, filing_form TEXT, filing_date TEXT, filing_url TEXT)")
        reg.execute("INSERT INTO software_filing_evidence_candidate VALUES('ACME','123','10-K','2026-02-01',?)", (self.url,))
        reg.commit(); reg.close()
        cache = sqlite3.connect(self.cache)
        cache.execute("CREATE TABLE filing_cache(url TEXT PRIMARY KEY, status TEXT, observed_at TEXT, content_gzip BLOB, content_sha256 TEXT)")
        cache.execute("INSERT INTO filing_cache VALUES(?,?,?,?,?)",
                      (self.url, "ok", "2026-02-02", gzip.compress(html), hashlib.sha256(html).hexdigest()))
        cache.commit(); cache.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, client: FakeClient) -> dict:
        return fx.run(client=client, model="small-model", host="h", config=CONFIG, limit=5,
                      db_path=self.registry, cache_path=self.cache)

    def test_run_stores_candidates_and_writes_no_packets(self) -> None:
        summary = self._run(FakeClient(good_responses()))
        self.assertEqual(summary["filings_processed"], 1)
        self.assertEqual(summary["llm_calls"], 2)
        self.assertEqual(summary["packet_writes"], 0)
        self.assertEqual(summary["result_counts"], {"verified": 3, "invalid_sentence_number": 1, "not_in_excerpt": 1})
        db = sqlite3.connect(self.registry)
        states = dict(db.execute("SELECT field, verification_state FROM filing_llm_extraction").fetchall())
        db.close()
        self.assertEqual(states["business_model"], "provisional_needs_review")
        self.assertEqual(states["revenue_drivers"], "rejected_or_unknown")

    def test_second_run_reuses_results_without_model_calls(self) -> None:
        self._run(FakeClient(good_responses()))
        client = FakeClient(good_responses())
        summary = self._run(client)
        self.assertEqual(client.calls, 0)
        self.assertEqual(summary["filings_processed"], 0)

    def test_failed_calls_are_retried_next_run(self) -> None:
        self._run(FakeClient(fail=True))
        client = FakeClient(good_responses())
        summary = self._run(client)
        self.assertEqual(client.calls, 2)
        self.assertEqual(summary["result_counts"].get("verified"), 3)

    def test_review_files(self) -> None:
        self._run(FakeClient(good_responses()))
        csv_path, md_path = fx.write_review(self.registry, Path(self.tmp.name) / "out")
        self.assertIn("reviewer_verdict", csv_path.read_text(encoding="utf-8-sig").splitlines()[0])
        self.assertIn("| business_model | 1 | 0 | 0 | 0 |", md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
