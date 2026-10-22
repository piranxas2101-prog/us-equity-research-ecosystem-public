"""SEC sector evidence: XBRL KPIs with their filings, same-SIC peers, unknown when old or missing."""
from __future__ import annotations

from datetime import date
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import sector_evidence_sec as se
from sector_context_handoff import build_from_profile

TODAY = date(2026, 10, 6)


def fact(end, val, *, start=None, form="10-Q", filed="2026-08-01", accn="0001-26-000001"):
    row = {"end": end, "val": val, "form": form, "filed": filed, "accn": accn}
    if start:
        row["start"] = start
    return row


def document(**concepts):
    return {"facts": {"us-gaap": {name: {"units": {"USD": rows}} for name, rows in concepts.items()}}}


def store(folder: Path, cik: int, doc: dict) -> None:
    body = json.dumps(doc).encode("utf-8")
    (folder / f"CIK{cik:010d}.json.gz").write_bytes(gzip.compress(body))
    (folder / f"CIK{cik:010d}.meta.json").write_text(json.dumps(
        {"sha256": hashlib.sha256(body).hexdigest(), "fetched_date_et": "2026-10-05"}), encoding="utf-8")


SEC = {
    "ABC": {"cik": "1", "sic": "7372", "sic_description": "Services-Prepackaged Software", "entity_type": "operating",
            "observed_at": "2026-09-16T08:00:00+00:00", "hash": "h"},
    "PEER": {"cik": "2", "sic": "7372", "entity_type": "operating"},
    "PEERP": {"cik": "2", "sic": "7372", "entity_type": "operating"},  # preferred series of the same issuer
    "OTHER": {"cik": "3", "sic": "7372", "entity_type": "operating"},
    "SHELL": {"cik": "4", "sic": "6770", "entity_type": "operating"},
    "SHELL2": {"cik": "5", "sic": "6770", "entity_type": "operating"},
    "SHELL3": {"cik": "6", "sic": "6770", "entity_type": "operating"},
}


class SectorKpiTests(unittest.TestCase):
    def test_reported_kpi_carries_concept_period_and_filing(self):
        doc = document(RevenueRemainingPerformanceObligation=[fact("2026-03-31", 100.0), fact("2026-06-30", 120.0, accn="A2")])
        kpis, facts = se.sector_kpis(doc, "technology", "software", TODAY)
        rpo = kpis["remaining_performance_obligations"]
        self.assertEqual((rpo["value"], rpo["period_end"], rpo["accessions"], rpo["basis"]),
                         (120.0, "2026-06-30", ["A2"], "fact_source_reported"))

    def test_an_old_fact_stays_unknown(self):
        doc = document(InventoryNet=[fact("2024-12-31", 50.0)])
        self.assertEqual(se.sector_kpis(doc, "technology", "semiconductors", TODAY)[0], {})

    def test_premium_growth_needs_two_consecutive_fiscal_years(self):
        years = [fact("2024-12-31", 100.0, start="2024-01-01", form="10-K"),
                 fact("2025-12-31", 125.0, start="2025-01-01", form="10-K", accn="K25")]
        kpis = se.sector_kpis(document(PremiumsEarnedNet=years), "financials", "insurance", TODAY)[0]
        self.assertEqual((kpis["premium_growth"]["value"], kpis["premium_growth"]["basis"]), (25.0, "derived_calculation"))
        quarter_only = [fact("2025-12-31", 30.0, start="2025-10-01", form="10-Q")]
        self.assertNotIn("premium_growth", se.sector_kpis(document(PremiumsEarnedNet=quarter_only), "financials", "insurance", TODAY)[0])

    def test_cash_runway_says_when_there_is_no_burn(self):
        burning = document(CashAndCashEquivalentsAtCarryingValue=[fact("2026-06-30", 200.0)],
                           NetCashProvidedByUsedInOperatingActivities=[fact("2025-12-31", -100.0, start="2025-01-01", form="10-K")])
        self.assertEqual(se.sector_kpis(burning, "healthcare", "therapeutics", TODAY)[0]["cash_runway_context"]["value"]["runway_years"], 2.0)
        earning = document(CashAndCashEquivalentsAtCarryingValue=[fact("2026-06-30", 200.0)],
                           NetCashProvidedByUsedInOperatingActivities=[fact("2025-12-31", 50.0, start="2025-01-01", form="10-K")])
        self.assertIsNone(se.sector_kpis(earning, "healthcare", "therapeutics", TODAY)[0]["cash_runway_context"]["value"]["runway_years"])

    def test_other_subcategories_get_nothing_invented(self):
        doc = document(InventoryNet=[fact("2026-06-30", 50.0)])
        self.assertEqual(se.sector_kpis(doc, "real_estate", "ownership", TODAY)[0], {})


class PeerGroupTests(unittest.TestCase):
    def test_peers_share_the_sic_one_ticker_per_issuer(self):
        peers, total = se.peer_group("ABC", SEC)
        self.assertEqual((peers, total), (["OTHER", "PEER"], 2))
        self.assertEqual(se.peer_group("ABC", SEC, preferred={"PEER"})[0], ["PEER", "OTHER"])

    def test_blank_check_and_catch_all_codes_have_no_peer_group(self):
        self.assertIsNone(se.peer_group("SHELL", SEC))
        catch_all = {**SEC, "ABC": {**SEC["ABC"], "sic_description": "Services-Business Services, NEC"}}
        self.assertIsNone(se.peer_group("ABC", catch_all))


class EvidenceIntoSectorPacketTests(unittest.TestCase):
    def test_sector_packet_takes_the_kpis_and_peers_with_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            store(Path(folder), 1, document(RevenueRemainingPerformanceObligation=[fact("2026-06-30", 120.0, accn="A2")]))
            evidence = se.build_evidence(ticker="ABC", cik=1, leaf_node="technology.software.applications",
                                         sec_by_ticker=SEC, today=TODAY, companyfacts_dir=Path(folder))
        profile = {"profile_id": "p1", "snapshot_id": "s1", "ticker": "ABC", "venue_code": "Q",
                   "taxonomy_node": "technology.software.applications", "status": "identity_only",
                   "source_references": [], "unknown_fields": []}
        packet = build_from_profile(profile, "run-1", evidence)
        self.assertEqual(packet["sector_kpis"]["remaining_performance_obligations"]["value"], 120.0)
        self.assertEqual([peer["ticker"] for peer in packet["peer_group"]], ["OTHER", "PEER"])
        self.assertNotIn("peer_group", packet["unknown_fields"])
        self.assertNotIn("sector_kpis.remaining_performance_obligations", packet["unknown_fields"])
        self.assertIn("sector_kpis.retention", packet["unknown_fields"])  # not reported in XBRL: stays unknown
        self.assertEqual(packet["evidence_coverage"], {"answered": 2, "required": 14})
        self.assertEqual({s["provider"] for s in packet["source_references"]}, {"sec_companyfacts", "sec_submissions"})
        self.assertNotEqual(packet["status"], "complete")  # two answers do not make a complete sector view

    def test_a_tampered_store_copy_is_not_used(self):
        with tempfile.TemporaryDirectory() as folder:
            store(Path(folder), 1, document(InventoryNet=[fact("2026-06-30", 1.0)]))
            meta = Path(folder) / "CIK0000000001.meta.json"
            meta.write_text(json.dumps({"sha256": "0" * 64}), encoding="utf-8")
            self.assertIsNone(se.load_companyfacts(1, Path(folder)))


class EvidenceTierTests(unittest.TestCase):
    def test_tiers_follow_coverage_and_adequate_needs_a_business_model_and_a_kpi(self):
        from sector_agents.base import evidence_tier
        self.assertEqual(evidence_tier(2, 14, has_business_model=False, kpi_count=1), "insufficient")
        self.assertEqual(evidence_tier(3, 14, has_business_model=False, kpi_count=1), "partial")
        self.assertEqual(evidence_tier(7, 14, has_business_model=True, kpi_count=1), "adequate")
        self.assertEqual(evidence_tier(7, 14, has_business_model=False, kpi_count=3), "partial")
        self.assertEqual(evidence_tier(7, 14, has_business_model=True, kpi_count=0), "partial")
        self.assertEqual(evidence_tier(14, 14, has_business_model=True, kpi_count=5), "complete")

    def test_only_insufficient_packets_open_a_missing_evidence_gap(self):
        import sqlite3
        from company_profile_agent import publish_profile_to_control_plane
        from sector_context_handoff import publish_sector_context
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "flow.db"
            for index, tier in enumerate(("insufficient", "partial", "adequate")):
                profile = {"schema": "ecosystem.company_profile.v1", "profile_id": f"profile-{index}", "run_id": "r1",
                           "snapshot_id": "s1", "ticker": f"T{index}", "venue_code": "Q",
                           "taxonomy_node": "technology.software", "status": "identity_only",
                           "created_at": "2026-10-06T00:00:00+00:00", "unknown_fields": [], "source_references": []}
                subject = f"sec:cik:{index + 1:010d}"
                control = publish_profile_to_control_plane(profile, db_path, canonical_subject_id=subject)
                packet = {**build_from_profile(profile, "r1"), "evidence_tier": tier}
                publish_sector_context(packet=packet, control_plane_db=db_path, subject_id=subject,
                                       profile_control_packet_id=control["control_packet_id"])
            db = sqlite3.connect(db_path)
            reasons = sorted(row[0] for row in db.execute("SELECT reason_code FROM ecosystem_control_plane_gap"))
            db.close()
        self.assertEqual(reasons, ["sector_evidence_missing", "sector_evidence_partial"])


if __name__ == "__main__":
    unittest.main()


class CatalystEvidenceTests(unittest.TestCase):
    def test_only_8k_items_with_a_source_and_date_become_catalysts(self):
        row = {"catalyst_evidence": [
            {"source_kind": "sec_item", "sec_form": "8-K", "sec_item": "2.02", "event_name": "Results",
             "event_date": "2026-10-01", "direction": "mixed", "category_id": "earnings_guidance", "source_url": "https://sec/a"},
            {"source_kind": "sec_form", "sec_form": "4", "event_date": "2026-10-02", "source_url": "https://sec/b"},
            {"source_kind": "sec_item", "sec_item": "2.02", "event_date": "2026-10-01", "source_url": "https://sec/a"}]}
        evidence = se.catalyst_evidence(row)
        self.assertEqual([e["item"] for e in evidence["catalysts"]], ["2.02"])
        self.assertEqual(evidence["source_references"][0]["as_of"], "2026-10-01")
        self.assertEqual(se.catalyst_evidence({"catalyst_evidence": []}), {})
