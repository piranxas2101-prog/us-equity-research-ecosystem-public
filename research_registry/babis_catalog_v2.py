"""Babis catalogue pass v2: Polygon details + conservative existing taxonomy proposals."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from urllib.parse import quote, urlencode

from . import source_http
from .core import PROJECT, DEFAULT_DB, digest, taxonomy

ROOT = PROJECT / "data/babis_catalog"
CORPORATE = {"equity_candidate", "adr_candidate", "unknown_instrument"}
SPECIAL_TYPES = {
    "ETF": "etf", "ETN": "etn", "FUND": "fund_candidate", "PFD": "preferred",
    "ADRP": "preferred", "WARRANT": "warrant", "RIGHT": "right", "UNIT": "unit",
    "BOND": "debt",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path: Path, value):
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def save_text(path: Path, value):
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(value, encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def normalize_cik(value):
    text = str(value or "").strip()
    return str(int(text)) if text.isdigit() and int(text) > 0 else None


def prepare(root=ROOT):
    db = sqlite3.connect(DEFAULT_DB.as_uri() + "?mode=ro", uri=True)
    try:
        snapshot = db.execute(
            "SELECT snapshot_id FROM universe_snapshot ORDER BY retrieved_at DESC,rowid DESC LIMIT 1"
        ).fetchone()[0]
        records = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT payload_json FROM listed_symbol WHERE snapshot_id=?", (snapshot,)
            )
        ]
    finally:
        db.close()
    identity_raw = (PROJECT / "cache/sec_company_tickers_exchange.json").read_bytes()
    identity_payload = json.loads(identity_raw)
    identities = {}
    for values in identity_payload["data"]:
        row = dict(zip(identity_payload["fields"], values))
        identities.setdefault(row["ticker"], set()).add(normalize_cik(row.get("cik")))
    for row in records:
        found = set()
        for alias in row.get("directory_aliases", []):
            found.update(item for item in identities.get(alias, set()) if item)
        row["candidate_cik"] = next(iter(found)) if len(found) == 1 else None
    # Hash ordering prevents an early provider stop from creating an A-only sample.
    records.sort(key=lambda row: digest([row["ticker"], row["venue_code"], "babis-v2"]))
    job_id = "babis-v2-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = root / job_id
    folder.mkdir(parents=True)
    data = taxonomy()[0]
    inputs = {
        "snapshot_id": snapshot,
        "taxonomy_version": data["version"],
        "taxonomy_sha256": digest(data),
        "worker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "identity_sha256": hashlib.sha256(identity_raw).hexdigest(),
        "records": records,
        "symbols": len(records),
        "policy": {
            "preserve_more_specific_compatible_node": True,
            "asset_type_before_sector": True,
            "cross_branch_requires_review": True,
            "registry_writes": False,
            "llm_calls": False,
        },
    }
    save(folder / "input.json", inputs)
    save(folder / "state.json", {"status": "prepared", "job_id": job_id, "symbols": len(records), "created_at": now()})
    return folder


class PolygonSource:
    def __init__(self, folder: Path, max_requests=6000, max_seconds=21600):
        self.helper = source_http
        try:
            _, self.env = self.helper.load_env(str(PROJECT / ".env"))
        except SystemExit:
            self.env = {}
        self.db = sqlite3.connect(folder / "evidence.sqlite3")
        self.db.execute("CREATE TABLE IF NOT EXISTS evidence(key TEXT PRIMARY KEY,payload TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS attempts(key TEXT PRIMARY KEY,at TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS circuits(provider TEXT PRIMARY KEY,status TEXT NOT NULL)")
        self.db.commit()
        self.max_requests = max_requests
        self.deadline = time.monotonic() + max_seconds
        self.last_call = 0.0

    def details(self, ticker):
        key = "polygon_details:" + ticker
        cached = self.db.execute("SELECT payload FROM evidence WHERE key=?", (key,)).fetchone()
        if cached:
            return json.loads(cached[0])
        result = {
            "provider": "polygon",
            "source_url": "https://api.polygon.io/v3/reference/tickers/" + quote(ticker),
            "observed_at": now(),
            "data": {},
        }
        circuit = self.db.execute("SELECT status FROM circuits WHERE provider='polygon'").fetchone()
        attempted = self.db.execute("SELECT 1 FROM attempts WHERE key=?", (key,)).fetchone()
        count = self.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        api_key = self.env.get("POLYGON_API_KEY", "").strip()
        if circuit:
            result["status"] = "provider_disabled:" + circuit[0]
        elif attempted:
            result["status"] = "interrupted_request_not_retried"
        elif count >= self.max_requests or time.monotonic() >= self.deadline:
            result["status"] = "budget_exhausted"
        elif not api_key:
            result["status"] = "missing_configuration"
        else:
            time.sleep(max(0.0, 1.05 - (time.monotonic() - self.last_call)))
            self.db.execute("INSERT INTO attempts VALUES(?,?)", (key, now()))
            self.db.commit()
            url = result["source_url"] + "?" + urlencode({"apiKey": api_key})
            response = self.helper.fetch_json("polygon_ticker_details", url, timeout=20)
            self.last_call = time.monotonic()
            result["status"] = response.get("status")
            payload = response.get("data") if isinstance(response.get("data"), dict) else {}
            row = payload.get("results") if isinstance(payload.get("results"), dict) else {}
            if result["status"] == "ok":
                fields = [
                    "ticker", "name", "type", "cik", "sic_code", "sic_description", "description",
                    "primary_exchange", "active", "last_updated_utc", "list_date", "composite_figi",
                    "share_class_figi",
                ]
                result["data"] = {field: row.get(field) for field in fields if field in row}
                if not row:
                    result["status"] = "empty_response"
            if result["status"] in {
                "auth_failed", "forbidden_or_plan", "rate_limited", "timeout_or_network",
                "invalid_json", "unexpected_error",
            }:
                self.db.execute(
                    "INSERT OR REPLACE INTO circuits VALUES('polygon',?)", (result["status"],)
                )
        result["evidence_sha256"] = digest(result)
        self.db.execute("INSERT INTO evidence VALUES(?,?)", (key, json.dumps(result, ensure_ascii=False)))
        self.db.commit()
        return result


EXACT_SIC = {
    1531: "consumer_discretionary.services_housing.other.homebuilding",
    2833: "healthcare.therapeutics.medicines.pharma",
    2834: "healthcare.therapeutics.medicines.pharma",
    2835: "healthcare.care.equipment.diagnostics",
    2836: "healthcare.therapeutics.medicines.biotech",
    3841: "healthcare.care.equipment.devices",
    3842: "healthcare.care.equipment.devices",
    3844: "healthcare.care.equipment.diagnostics",
    3845: "healthcare.care.equipment.devices",
    6021: "financials.banking_credit.banks",
    6022: "financials.banking_credit.banks",
    6035: "financials.banking_credit.banks",
    6141: "financials.banking_credit.lending.consumer",
    6162: "financials.banking_credit.lending.mortgage",
    6211: "financials.markets.investment.brokerage",
    6282: "financials.markets.investment.management",
    6311: "financials.insurance.underwriting.life",
    6331: "financials.insurance.underwriting.property",
    6411: "financials.insurance.services.brokers",
    6798: "real_estate.ownership.equity_reits",
    7371: "technology.hardware_services.services.consulting",
    7372: "technology.software",
    7374: "technology.hardware_services.services",
    3571: "technology.hardware_services.hardware.computing",
    3572: "technology.hardware_services.hardware.computing",
    3661: "technology.hardware_services.hardware.networking",
    3674: "technology.semiconductors.chips",
    3711: "consumer_discretionary.mobility.automotive.makers",
    3714: "consumer_discretionary.mobility.automotive.suppliers",
    4512: "industrials.transport.passenger.airlines",
    4011: "industrials.transport.freight.rail_road",
    4213: "industrials.transport.freight.logistics",
    4412: "industrials.transport.freight.marine",
    4731: "industrials.transport.freight.logistics",
    5812: "consumer_discretionary.services_housing.leisure.restaurants",
    7011: "consumer_discretionary.services_housing.leisure.travel",
    1311: "energy.hydrocarbons.production.upstream",
    1381: "energy.support_fuels.services.oilfield",
    1382: "energy.support_fuels.services.oilfield",
    1389: "energy.support_fuels.services.oilfield",
    2911: "energy.hydrocarbons.processing_transport.refining",
    4612: "energy.hydrocarbons.processing_transport.midstream",
    4911: "utilities.networks.regulated.electric",
    4922: "utilities.networks.regulated.gas",
    4931: "utilities.networks.regulated.multi",
    4941: "utilities.networks.regulated.water",
    4813: "communication_services.telecom.connectivity",
    4832: "communication_services.media.content",
    4833: "communication_services.media.content.film_tv",
    4841: "communication_services.media.content",
    7311: "communication_services.media.publishing_ads.advertising",
    2711: "communication_services.media.publishing_ads.publishing",
    7812: "communication_services.media.content.film_tv",
    2082: "consumer_staples.products.beverages_tobacco.beverages",
    2085: "consumer_staples.products.beverages_tobacco.beverages",
    2086: "consumer_staples.products.beverages_tobacco.beverages",
    2111: "consumer_staples.products.beverages_tobacco.tobacco",
    5411: "consumer_staples.household_distribution.distribution.retail",
    1041: "materials.resources.metals.precious",
    2812: "materials.chemicals.chemicals.gases",
    2844: "consumer_staples.household_distribution.care.personal",
    2873: "materials.chemicals.chemicals.fertilizers",
    3312: "materials.resources.metals.industrial",
    3334: "materials.resources.metals.industrial",
    2421: "materials.forest_packaging.forest.wood_paper",
    2611: "materials.forest_packaging.forest.wood_paper",
    2653: "materials.forest_packaging.packaging.containers",
    3533: "energy.support_fuels.services.oilfield",
    3576: "technology.hardware_services.hardware.networking",
    3577: "technology.hardware_services.hardware.computing",
    3690: "industrials.capital_goods.electrical",
    3826: "healthcare.therapeutics.research_support.tools",
    4899: "communication_services.telecom.connectivity",
    5961: "consumer_discretionary.goods_retail.retail.broadline",
    6500: "real_estate",
    6531: "real_estate.development_services.operations.services",
    7310: "communication_services.media.publishing_ads.advertising",
    7370: "technology.hardware_services.services",
    7373: "technology.hardware_services.services",
    8071: "healthcare.care.services.providers",
    8200: "consumer_discretionary.services_housing.other.education_personal",
}


def sic_candidate(value):
    text = str(value or "").strip()
    if not text.isdigit():
        return None, "missing_sic"
    code = int(text)
    if code in EXACT_SIC:
        return EXACT_SIC[code], "explicit_sic_crosswalk"
    if 100 <= code <= 999:
        return "consumer_staples.products.food.agricultural", "sic_range_sector_or_group"
    if 1000 <= code <= 1499:
        return "materials.resources", "sic_range_sector_or_group"
    if 1500 <= code <= 1799:
        return "industrials.capital_goods.construction", "sic_range_sector_or_group"
    if 2000 <= code <= 2099:
        return "consumer_staples.products.food", "sic_range_sector_or_group"
    if 2100 <= code <= 2199:
        return "consumer_staples.products.beverages_tobacco.tobacco", "sic_range_sector_or_group"
    if 2200 <= code <= 2599:
        return "consumer_discretionary.goods_retail.goods", "sic_range_sector_or_group"
    if 2600 <= code <= 2699:
        return "materials.forest_packaging", "sic_range_sector_or_group"
    if 2800 <= code <= 2899:
        return "materials.chemicals", "sic_range_sector_or_group"
    if 2900 <= code <= 2999:
        return "energy.hydrocarbons.processing_transport", "sic_range_sector_or_group"
    if 3000 <= code <= 3499:
        return "materials", "sic_range_sector_only"
    if 3500 <= code <= 3599:
        return "industrials.capital_goods.machinery", "sic_range_sector_or_group"
    if 3600 <= code <= 3699:
        return "technology.hardware_services.hardware", "sic_range_sector_or_group"
    if 3700 <= code <= 3799:
        return "industrials.capital_goods", "sic_range_sector_or_group"
    if 3800 <= code <= 3899:
        return "technology.hardware_services.hardware", "sic_range_sector_or_group"
    if 4000 <= code <= 4799:
        return "industrials.transport", "sic_range_sector_or_group"
    if 4800 <= code <= 4899:
        return "communication_services.telecom", "sic_range_sector_or_group"
    if 4900 <= code <= 4999:
        return "utilities", "sic_range_sector_only"
    if 5000 <= code <= 5199:
        return "industrials.business_services", "sic_range_sector_or_group"
    if 5200 <= code <= 5999:
        return "consumer_discretionary.goods_retail.retail", "sic_range_sector_or_group"
    if 6000 <= code <= 6499:
        return "financials", "sic_range_sector_only"
    if 6500 <= code <= 6599:
        return "real_estate", "sic_range_sector_only"
    if 6600 <= code <= 6799:
        return "financials", "sic_range_sector_only"
    if 7000 <= code <= 7299:
        return "consumer_discretionary.services_housing", "sic_range_sector_or_group"
    if 7300 <= code <= 7369:
        return "industrials.business_services", "sic_range_sector_or_group"
    if 7370 <= code <= 7379:
        return "technology.hardware_services.services", "sic_range_sector_or_group"
    if 7380 <= code <= 7399:
        return "industrials.business_services", "sic_range_sector_or_group"
    if 7800 <= code <= 7899:
        return "communication_services.media", "sic_range_sector_or_group"
    if 7900 <= code <= 7999:
        return "consumer_discretionary.services_housing.leisure", "sic_range_sector_or_group"
    if 8000 <= code <= 8099:
        return "healthcare.care.services", "sic_range_sector_or_group"
    if 8200 <= code <= 8299:
        return "consumer_discretionary.services_housing.other.education_personal", "sic_range_sector_or_group"
    if 8700 <= code <= 8999:
        return "industrials.business_services.commercial.professional", "sic_range_sector_or_group"
    return None, "unsupported_sic"


def relation(old, new):
    if not new:
        return "no_candidate"
    if not old:
        return "new_assignment"
    if old == new:
        return "same_node"
    if new.startswith(old + "."):
        return "deeper_assignment"
    if old.startswith(new + "."):
        return "existing_more_specific"
    if old.split(".")[0] != new.split(".")[0]:
        return "cross_sector_conflict"
    return "different_branch_same_sector"


def propose(row, evidence, nodes=None):
    nodes = nodes or taxonomy()[1]
    details = evidence.get("data") or {}
    expected = row.get("candidate_cik")
    observed = normalize_cik(details.get("cik"))
    base = {
        "ticker": row["ticker"], "venue": row["venue"], "name": row["name"],
        "old_instrument_type": row["instrument_type"], "old_node": row.get("node_id"),
        "source_status": evidence.get("status"), "verified": False, "applied_to_registry": False,
        "source_ref": {
            "provider": evidence.get("provider"), "url": evidence.get("source_url"),
            "observed_at": evidence.get("observed_at"), "hash": evidence.get("evidence_sha256"),
        },
        "evidence": details,
    }
    if evidence.get("status") != "ok":
        return {**base, "status": "unresolved", "reason": "provider_profile_unavailable",
                "proposed_node": None, "proposed_instrument_type": None, "relation": "no_candidate"}
    if expected and observed and expected != observed:
        return {**base, "status": "identity_review", "reason": "polygon_cik_conflict",
                "proposed_node": None, "proposed_instrument_type": None, "relation": "no_candidate"}
    polygon_type = str(details.get("type") or "").upper()
    special = SPECIAL_TYPES.get(polygon_type)
    if special:
        return {**base, "status": "asset_type_review", "reason": "polygon_special_security_type",
                "proposed_node": None, "proposed_instrument_type": special, "relation": "no_candidate"}
    proposed_type = "adr_candidate" if polygon_type in {"ADRC", "ADRR"} else "equity_candidate" if polygon_type in {"CS", "OS"} else None
    candidate, basis = sic_candidate(details.get("sic_code"))
    if candidate and candidate not in nodes:
        raise ValueError("SIC crosswalk outside taxonomy: " + candidate)
    rel = relation(row.get("node_id"), candidate)
    if rel == "existing_more_specific":
        status, proposed = "confirmed_existing_more_specific", row.get("node_id")
    elif rel == "same_node":
        status, proposed = "confirmed_same_node", row.get("node_id")
    elif rel in {"cross_sector_conflict", "different_branch_same_sector"}:
        status, proposed = "classification_conflict_review", candidate
    elif candidate:
        status, proposed = "classification_proposal_review", candidate
    else:
        status, proposed = "unresolved", None
    return {**base, "status": status, "reason": basis, "proposed_node": proposed,
            "proposed_instrument_type": proposed_type, "relation": rel,
            "proposed_path": [nodes[node] for node in nodes if node == proposed or (proposed and proposed.startswith(node + "."))]}


def report(folder, inputs, db, state):
    rows = [json.loads(row[0]) for row in db.execute("SELECT payload FROM results ORDER BY ticker,venue")]
    counts = Counter(row["status"] for row in rows)
    relations = Counter(row.get("relation") for row in rows)
    state.update(processed_symbols=len(rows), total_symbols=inputs["symbols"], status_counts=dict(counts))
    save(folder / "state.json", state)
    save(folder / "proposals.json", rows)
    lines = [
        "# Μπάμπης v2: report εμπλουτισμού ταξινόμησης", "",
        f"Κατάσταση: {state['status']}.",
        f"Σύμβολα: {len(rows)} / {inputs['symbols']}.",
        f"Polygon requests: {state.get('source_requests', 0)}. LLM calls: 0. Production writes: 0.", "",
        "## Πολιτική", "",
        "Διατηρήθηκε κάθε συμβατός βαθύτερος υπάρχων κόμβος. Ειδικός τύπος τίτλου προηγείται της εταιρικής ταξινόμησης. Διαφωνίες κλάδου ή branch παραμένουν σε review. Καμία πρόταση δεν εφαρμόστηκε αυτόματα.",
        "Η σειρά εταιρικής επεξεργασίας ήταν deterministic hash order και όχι αλφαβητική, ώστε πιθανό rate limit να δώσει αντιπροσωπευτικότερο δείγμα.", "",
        "## Καταστάσεις", "", "| Κατάσταση | Πλήθος |", "| --- | ---: |",
    ]
    lines += [f"| {key} | {value} |" for key, value in sorted(counts.items())]
    lines += ["", "## Σχέση με υπάρχουσα ταξινόμηση", "", "| Σχέση | Πλήθος |", "| --- | ---: |"]
    lines += [f"| {key} | {value} |" for key, value in sorted(relations.items())]
    lines += [
        "", "## Όρια", "",
        "Το Polygon είναι συμπληρωματική πηγή. Το SIC και η περιγραφή δεν αποτελούν επίσημο GICS και δεν αρκούν πάντα για υποκλάδο. Τα range mappings σταματούν σε ευρύτερο κόμβο όταν η πηγή δεν τεκμηριώνει μεγαλύτερο βάθος.",
        "Το report διαχωρίζει πλήρη διέλευση από πλήρη ταξινόμηση. Unresolved και conflicts απαιτούν επόμενη πηγή ή ανθρώπινο έλεγχο.",
        "Το Excel των 44 εταιρειών παραμένει review fixture και δεν χρησιμοποιήθηκε ως authoritative source επειδή δεν έχει provenance/as-of.", "",
        f"Snapshot: `{inputs['snapshot_id']}`. Taxonomy: `{inputs['taxonomy_version']}`.",
    ]
    save_text(folder / "report.md", "\n".join(lines))


def run(folder, max_requests=6000, max_seconds=21600):
    import msvcrt
    lock = (folder.parent / "worker_v2.lock").open("a+b")
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        return 2
    inputs = json.loads((folder / "input.json").read_text(encoding="utf-8"))
    db = sqlite3.connect(folder / "results.sqlite3")
    db.execute("CREATE TABLE IF NOT EXISTS results(ticker TEXT,venue TEXT,payload TEXT,PRIMARY KEY(ticker,venue))")
    state = {"status": "running", "pid": os.getpid(), "job_id": folder.name, "started_at": now(),
             "max_requests": max_requests, "max_seconds": max_seconds, "llm_calls": 0, "registry_writes": 0}
    source = None
    try:
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != inputs["worker_sha256"]:
            raise ValueError("Worker changed since preparation")
        if digest(taxonomy()[0]) != inputs["taxonomy_sha256"]:
            raise ValueError("Taxonomy changed since preparation")
        source = PolygonSource(folder, max_requests, max_seconds)
        nodes = taxonomy()[1]
        report(folder, inputs, db, state)
        for index, row in enumerate(inputs["records"]):
            if (folder / "STOP").exists():
                state["status"] = "stopped_with_pending"
                break
            if db.execute("SELECT 1 FROM results WHERE ticker=? AND venue=?", (row["ticker"], row["venue"])).fetchone():
                continue
            if row["instrument_type"] not in CORPORATE:
                result = {
                    "ticker": row["ticker"], "venue": row["venue"], "name": row["name"],
                    "old_instrument_type": row["instrument_type"], "old_node": row.get("node_id"),
                    "proposed_node": None, "proposed_instrument_type": None,
                    "status": "retained_instrument_category", "reason": "separate_asset_classification",
                    "relation": "no_candidate", "verified": False, "applied_to_registry": False,
                }
            else:
                result = propose(row, source.details(row["ticker"]), nodes)
            with db:
                db.execute("INSERT INTO results VALUES(?,?,?)", (row["ticker"], row["venue"], json.dumps(result, ensure_ascii=False)))
            if index % 100 == 0:
                state.update(updated_at=now(), processed_symbols=db.execute("SELECT COUNT(*) FROM results").fetchone()[0],
                             source_requests=source.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0])
                save(folder / "state.json", state)
        else:
            state["status"] = "finished_with_review_required"
    except Exception as exc:
        state.update(status="failed", error_type=type(exc).__name__)
    finally:
        state["finished_at"] = now()
        if source:
            state["source_requests"] = source.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            state["provider_circuits"] = dict(source.db.execute("SELECT provider,status FROM circuits"))
            source.db.close()
        report(folder, inputs, db, state)
        db.close()
        lock.close()
    return 1 if state["status"] == "failed" else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "run", "status"])
    parser.add_argument("--job", type=Path)
    parser.add_argument("--max-requests", type=int, default=6000)
    parser.add_argument("--max-seconds", type=int, default=21600)
    args = parser.parse_args(argv)
    if not 0 <= args.max_requests <= 6000 or not 1 <= args.max_seconds <= 21600:
        parser.error("Bounds: 0..6000 requests and 1..21600 seconds")
    if args.action == "start":
        folder = args.job or prepare()
        log = (folder / "worker.log").open("a", encoding="utf-8")
        try:
            process = subprocess.Popen(
                [sys.executable, "-B", "-m", "research_registry.babis_catalog_v2", "run", "--job", str(folder),
                 "--max-requests", str(args.max_requests), "--max-seconds", str(args.max_seconds)],
                cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
            )
        finally:
            log.close()
        save(ROOT / "latest_v2.json", {"job": str(folder), "pid": process.pid})
        print(json.dumps({"status": "dispatched", "pid": process.pid, "job": str(folder)}, ensure_ascii=False))
        return 0
    folder = args.job or Path(json.loads((ROOT / "latest_v2.json").read_text(encoding="utf-8"))["job"])
    if args.action == "status":
        print((folder / "state.json").read_text(encoding="utf-8"))
        return 0
    return run(folder, args.max_requests, args.max_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
