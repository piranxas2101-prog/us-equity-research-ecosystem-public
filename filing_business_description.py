"""Business description from the latest 10-K: the opening sentences of Item 1, word for word, no model.

Approved by the owner 2026-10-06 (docs/REDESIGN_NOTES_2026-10.md 4θ). The first sentences of Item 1 ("Business") are
almost always the company's own description of what it does; on 200 stored 10-Ks the section was found in 198. The
text is kept verbatim and labelled ``issuer_management_claim`` (the company about itself, not a judgement).

Only the extracted sentences, the filing identity and the document hash are stored (``cache/sec_business_descriptions
.sqlite3``), not the document. A description is fetched once per 10-K: it is refreshed when the SEC filing calendar
shows a newer 10-K, and each run fetches at most ``max_new_per_run`` missing ones so the backfill spreads over a few
nights within the SEC rate policy. 20-F filers are read the same way (Item 4, 3.D, 5); 40-F filers stay unknown.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable
import urllib.error
import urllib.request

import filing_sector_text

ROOT = Path(__file__).resolve().parent
DEFAULT_STORE = ROOT / "cache" / "sec_business_descriptions.sqlite3"
ANNUAL_FORMS = ("10-K", "20-F")  # 20-F: Item 4 / 3.D / 5 play the roles of Item 1 / 1A / 7
LEAD_SENTENCES = 3
MIN_DESCRIPTION_CHARS = 60
RETRY_NEGATIVE_DAYS = 30
MAX_AGE_DAYS = 400
# Only unambiguous section headings are cut: "ITEM 1." and capitalised BUSINESS / OVERVIEW / GENERAL, and the
# two-word headings "Company Overview" / "Business Overview". A bare "General" or "Overview" may start a real name
# (General Motors), so mixed-case single words are left alone.
HEADING = re.compile(r"^(?:ITEM\s*1\.?\s*|Item\s*1\.?\s*)?(?:BUSINESS|Business)?\s*"
                     r"(?:(?:[A-D]\.\s*)?(?:Information on the Company|History and Development of the Company|Business Overview)|OVERVIEW|GENERAL|(?:Company|Business)\s+Overview|Overview(?=\s+[A-Z][a-z]+\s+(?:is|was|are)\b))?"
                     r"\s*[:.\-–—]?\s*")
NOT_DESCRIPTION = re.compile(r"form 10-k|glossary|forward-looking|this annual report|unless the context|"
                             r"references to|refers? to .{0,40}subsidiaries|as used (?:in this|herein)|table of contents|"
                             r"principal executive offices|telephone number|registered office|our agent for service", re.IGNORECASE)


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("""CREATE TABLE IF NOT EXISTS business_description(
        cik INTEGER PRIMARY KEY, status TEXT NOT NULL, form TEXT, accession TEXT, filing_date TEXT, url TEXT,
        document_sha256 TEXT, sentences_json TEXT, checked_at TEXT NOT NULL, detail TEXT)""")
    columns = {row[1] for row in db.execute("PRAGMA table_info(business_description)")}
    if "evidence_json" not in columns:  # 2026-10-06: the same 10-K also gives the other framework fields
        db.execute("ALTER TABLE business_description ADD COLUMN evidence_json TEXT")
        db.execute("ALTER TABLE business_description ADD COLUMN extractor_version TEXT")
    return db


def _strip_headings(sentence: str) -> str:
    """Cut stacked headings ("Business BUSINESS OVERVIEW ...") from the first sentence; the rest stays verbatim."""
    text = sentence.strip()
    for _ in range(3):
        cut = HEADING.sub("", text, count=1).strip()
        if cut == text:
            break
        text = cut
    return text


def lead_sentences(html: str, count: int = LEAD_SENTENCES) -> list[str] | None:
    """The first descriptive sentences of Item 1, verbatim apart from a leading section heading; None if not found."""
    import filing_llm_extractor as fx

    doc = fx.parse_filing(html)
    section = fx.locate_sections(doc).get("business")
    if not section:
        return None
    lead: list[str] = []
    for sentence in fx.split_sentences(doc.text[section[0]:section[1]]):
        text = _strip_headings(sentence) if not lead else sentence.strip()
        if len(text) < 30 or NOT_DESCRIPTION.search(text):
            continue
        lead.append(text)
        if len(lead) == count:
            break
    return lead if sum(len(s) for s in lead) >= MIN_DESCRIPTION_CHARS else None


def _get(url: str, user_agent: str, timeout: int = 30) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def latest_annual_filing(submissions: dict[str, Any]) -> dict[str, Any] | None:
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    for index, form in enumerate(forms):
        if form in ANNUAL_FORMS:
            return {"form": form, "accession": recent["accessionNumber"][index],
                    "filing_date": recent["filingDate"][index], "primary_document": recent["primaryDocument"][index]}
    return None


class BusinessDescriptions:
    def __init__(self, store: Path = DEFAULT_STORE, *, user_agent: str = "", limiter: Any = None,
                 max_new_per_run: int = 150, filing_db: Path | None = None,
                 fetch: Callable[[str, str], bytes] = _get, today: date | None = None) -> None:
        self.store, self.user_agent, self.limiter = Path(store), user_agent, limiter
        self.remaining, self.filing_db, self.fetch = int(max_new_per_run), filing_db, fetch
        self.today = today or date.today()
        self.counts = {"stored": 0, "fetched": 0, "deferred": 0, "unavailable": 0}

    def _newer_10k(self, cik: int, filing_date: str) -> bool | None:
        if self.filing_db is None or not Path(self.filing_db).is_file():
            return None
        from research_registry.sec_filing_index import filed_since, sec_today
        try:
            db = sqlite3.connect(f"file:{Path(self.filing_db).resolve().as_posix()}?mode=ro", uri=True, timeout=10)
            try:
                found = filed_since(db, cik, date.fromisoformat(filing_date), until=sec_today(), category="financial")
            finally:
                db.close()
        except (sqlite3.Error, ValueError):
            return None
        return None if found is None else any(item["form"] in ANNUAL_FORMS for item in found)

    def _needs_refresh(self, row: tuple) -> bool:
        status, filing_date, checked_at = row[1], row[4], row[8]
        checked = datetime.fromisoformat(checked_at).date()
        if status != "ok":
            return status == "fetch_failed" or (self.today - checked).days >= RETRY_NEGATIVE_DAYS
        if filing_date and (self.today - date.fromisoformat(filing_date)).days > MAX_AGE_DAYS:
            return True
        if len(row) > 11 and row[11] != filing_sector_text.EXTRACTOR_VERSION:
            return True  # stored before the other fields were extracted: read the 10-K again (within the budget)
        return bool(filing_date and self._newer_10k(row[0], filing_date))

    def _permitted(self) -> bool:
        if self.limiter is None:
            return True
        return bool(self.limiter.permit("sec", max_interval_wait_seconds=1.0).permitted)

    def _record(self, db: sqlite3.Connection, cik: int, status: str, **values: Any) -> None:
        db.execute("INSERT OR REPLACE INTO business_description VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            cik, status, values.get("form"), values.get("accession"), values.get("filing_date"), values.get("url"),
            values.get("document_sha256"), json.dumps(values.get("sentences")) if values.get("sentences") else None,
            datetime.now(timezone.utc).isoformat(), values.get("detail"),
            json.dumps(values["evidence"], ensure_ascii=False) if values.get("evidence") else None,
            filing_sector_text.EXTRACTOR_VERSION))
        db.commit()

    def _fetch_and_store(self, db: sqlite3.Connection, cik: int) -> None:
        try:
            submissions = json.loads(self.fetch(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", self.user_agent))
            filing = latest_annual_filing(submissions)
            if filing is None:
                self._record(db, cik, "no_10k", detail="no 10-K or 20-F among recent filings (40-F filer or none)")
                return
            if not self._permitted():
                self.counts["deferred"] += 1
                return
            url = (f"https://www.sec.gov/Archives/edgar/data/{cik}/{filing['accession'].replace('-', '')}/"
                   f"{filing['primary_document']}")
            body = self.fetch(url, self.user_agent)
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as exc:
            self._record(db, cik, "fetch_failed", detail=f"{type(exc).__name__}: {str(exc)[:200]}")
            return
        html = body.decode("utf-8", "replace")
        sentences = lead_sentences(html)
        try:
            evidence = filing_sector_text.extract(html)
        except Exception as exc:  # noqa: BLE001 - the description stays usable when the other fields fail
            evidence = {"extractor_version": filing_sector_text.EXTRACTOR_VERSION, "error": type(exc).__name__}
        meta = {"form": filing["form"], "accession": filing["accession"], "filing_date": filing["filing_date"],
                "url": url, "document_sha256": hashlib.sha256(body).hexdigest()}
        if sentences is None:
            self._record(db, cik, "no_section", detail="Item 1 not found in the document", **meta)
            return
        self._record(db, cik, "ok", sentences=sentences, evidence=evidence, **meta)
        self.counts["fetched"] += 1

    def get(self, cik: int) -> dict[str, Any] | None:
        """The stored description, refreshed within this run's budget; None when unknown."""
        cik = int(cik)
        db = _connect(self.store)
        try:
            row = db.execute("SELECT * FROM business_description WHERE cik=?", (cik,)).fetchone()
            if (row is None or self._needs_refresh(row)) and self.user_agent:
                if self.remaining > 0 and self._permitted():
                    self.remaining -= 1
                    self._fetch_and_store(db, cik)
                    row = db.execute("SELECT * FROM business_description WHERE cik=?", (cik,)).fetchone()
                else:
                    self.counts["deferred"] += 1
        finally:
            db.close()
        if not row or row[1] != "ok":
            self.counts["unavailable"] += 1
            return None
        self.counts["stored"] += 1
        return {"cik": cik, "form": row[2], "accession": row[3], "filing_date": row[4], "url": row[5],
                "document_sha256": row[6], "sentences": json.loads(row[7]),
                "evidence": json.loads(row[10]) if len(row) > 10 and row[10] else {}}


TEXT_FIELDS = ("value_chain_position", "revenue_drivers", "cost_drivers", "competitive_position",
               "industry_risks", "company_specific_risks")


def as_evidence(description: dict[str, Any] | None, required_kpis: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """Sector-agent evidence from the company's own 10-K: the business model and every framework field the text states.

    Each value is a list of verbatim statements (``text``, ``section``, ``basis``); KPIs are limited to the company's
    framework (``required_kpis``). Everything is the issuer's own claim, sourced to the 10-K.
    """
    if not description:
        return {}
    source_id = f"sec_10k:{int(description['cik']):010d}:{description['accession']}"
    evidence: dict[str, Any] = {
        "business_model": " ".join(description["sentences"]),
        "claim_sources": {"business_model": [source_id]},
        "source_references": [{
            "source_id": source_id, "provider": "sec_10k", "as_of": description["filing_date"],
            "evidence_label": "issuer_management_claim", "url": description["url"], "form": description["form"],
            "section": "Item 1 (opening sentences), Item 1A, Item 7 — verbatim",
            "document_sha256": description["document_sha256"]}],
    }
    text = description.get("evidence") or {}
    for field in TEXT_FIELDS:
        if text.get(field):
            evidence[field] = text[field]
            evidence["claim_sources"][field] = [source_id]
    kpis = {kpi: {"statements": text["kpis"][kpi], "basis": "issuer_management_claim"}
            for kpi in required_kpis if (text.get("kpis") or {}).get(kpi)}
    if kpis:
        evidence["sector_kpis"] = kpis
        evidence["claim_sources"]["sector_kpis"] = [source_id]
    return evidence


def merge_evidence(*parts: dict[str, Any]) -> dict[str, Any]:
    """Combine evidence parts; KPI values and claim sources are merged, a reported XBRL KPI wins over text."""
    merged: dict[str, Any] = {"source_references": [], "claim_sources": {}}
    for part in parts:
        for key, value in part.items():
            if key == "source_references":
                merged[key] += value
            elif key == "claim_sources":
                for field, ids in value.items():
                    merged[key][field] = list(dict.fromkeys([*merged[key].get(field, []), *ids]))
            elif key == "sector_kpis":
                merged[key] = {**value, **merged.get(key, {})}  # earlier parts (XBRL) keep their KPIs
            elif key not in merged:
                merged[key] = value
    return merged


def config_limit(config: dict[str, Any]) -> int:
    cfg = ((config.get("data_sources") or {}).get("sec") or {}).get("business_description") or {}
    try:
        return max(0, int(cfg.get("max_new_per_run", 150)))
    except (TypeError, ValueError):
        return 150
