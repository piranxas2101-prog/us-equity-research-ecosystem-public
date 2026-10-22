"""Collect and index candidate software KPI disclosures from SEC annual filings.

This finite worker downloads at most one cached annual filing per symbol and
uses deterministic keyword windows. Results are evidence candidates for human
or later structured review; they never become automatic company conclusions.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
from html import unescape
from html.parser import HTMLParser
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.request

from research_registry.core import DEFAULT_DB, PROJECT, connect, digest
from sec_xbrl_pilot import load_sec_user_agent
from sector_sec_ir_pilot import schema as source_schema
import information_registry
import research_control_plane


ROOT = PROJECT / "data" / "software_filing_evidence"
CACHE_DB = ROOT / "filing_cache.sqlite3"
INTERVAL_SECONDS = 0.25
MAX_REQUESTS = 250
PATTERNS = {
    "arr": (r"annual recurring revenue", r"\bARR\b"),
    "retention": (r"net revenue retention", r"net retention rate", r"dollar[- ]based net retention", r"gross retention"),
    "remaining_performance_obligations": (r"remaining performance obligations?", r"\bRPO\b"),
    "sales_and_marketing": (r"sales and marketing",),
    "risk_factors": (r"risk factors",),
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def schema(db: sqlite3.Connection) -> None:
    source_schema(db)
    db.executescript("""
      CREATE TABLE IF NOT EXISTS software_filing_evidence_run(
        run_id TEXT PRIMARY KEY,source_run_id TEXT NOT NULL,status TEXT NOT NULL,
        total_count INTEGER NOT NULL,processed_count INTEGER NOT NULL,
        candidate_count INTEGER NOT NULL,no_candidate_count INTEGER NOT NULL,
        source_unavailable_count INTEGER NOT NULL,failed_count INTEGER NOT NULL,
        source_requests INTEGER NOT NULL,llm_calls INTEGER NOT NULL,
        created_at TEXT NOT NULL,updated_at TEXT NOT NULL,finished_at TEXT,
        summary_json TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS software_filing_evidence_candidate(
        candidate_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,queue_id TEXT NOT NULL,
        ticker TEXT NOT NULL,cik TEXT,filing_form TEXT,filing_date TEXT,filing_url TEXT,
        candidate_json TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,
        UNIQUE(run_id,queue_id));
    """)


def prepare(db_path: Path = DEFAULT_DB, root: Path = ROOT) -> Path:
    db = connect(db_path)
    try:
        schema(db)
        source_run = db.execute(
            "SELECT run_id FROM sector_source_collection_run WHERE target_node='technology.software' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not source_run: raise ValueError("No completed software SEC/IR source run exists")
        records = []
        for row in db.execute(
            "SELECT e.queue_id,e.ticker,e.cik,e.evidence_json FROM sector_source_evidence e "
            "WHERE e.run_id=? ORDER BY e.ticker", (source_run[0],)
        ):
            evidence = json.loads(row["evidence_json"])
            annual = next((item for item in evidence.get("filings") or [] if item.get("form") in {"10-K","20-F","40-F"}), None)
            records.append({"queue_id": row["queue_id"], "ticker": row["ticker"], "cik": row["cik"], "filing": annual})
        if not records: raise ValueError("SEC/IR source run has no symbols")
        run_id = "software-filing-evidence-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        folder = Path(root) / run_id; folder.mkdir(parents=True, exist_ok=False)
        payload = {"run_id": run_id, "source_run_id": source_run[0], "records": records,
                   "total_symbols": len(records), "created_at": now(), "policy": {
                       "one_annual_filing_per_symbol": True, "cache_by_url": True,
                       "minimum_interval_seconds": INTERVAL_SECONDS, "maximum_new_requests": MAX_REQUESTS,
                       "retries": 0, "stop_on_429": True, "llm_calls": 0,
                       "candidate_evidence_only": True, "automatic_packet_claims": False,
                   }}
        save(folder / "input.json", payload); save(folder / "state.json", {"status":"prepared", **payload})
        return folder
    finally: db.close()


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.parts = []; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in {"script","style","noscript"}: self.skip += 1
        elif tag in {"p","div","br","tr","td","li","h1","h2","h3","h4"}: self.parts.append(" ")
    def handle_endtag(self, tag):
        if tag in {"script","style","noscript"} and self.skip: self.skip -= 1
        elif tag in {"p","div","tr","li","h1","h2","h3","h4"}: self.parts.append(" ")
    def handle_data(self, data):
        if not self.skip: self.parts.append(data)
    def text(self) -> str:
        return re.sub(r"\s+", " ", unescape(" ".join(self.parts))).strip()


class FilingCache:
    def __init__(self, path: Path = CACHE_DB):
        path.parent.mkdir(parents=True, exist_ok=True); self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS filing_cache(
          url TEXT PRIMARY KEY,status TEXT NOT NULL,observed_at TEXT NOT NULL,
          content_gzip BLOB,content_sha256 TEXT)""")
        self.db.commit(); self.user_agent = load_sec_user_agent(str(PROJECT / ".env"))
        if not self.user_agent: raise ValueError("SEC_USER_AGENT is not configured")
        self.requests = 0; self.last_call = 0.0; self.rate_limited = False
    def close(self): self.db.close()
    def get(self, url: str) -> dict[str, Any]:
        row = self.db.execute("SELECT status,observed_at,content_gzip,content_sha256 FROM filing_cache WHERE url=?", (url,)).fetchone()
        if row:
            return {"status":row[0],"observed_at":row[1],"content":gzip.decompress(row[2]).decode("utf-8",errors="replace") if row[2] else None,"sha256":row[3],"cache_hit":True}
        if self.rate_limited: return {"status":"circuit_open_rate_limited","cache_hit":False}
        if self.requests >= MAX_REQUESTS: return {"status":"request_budget_exhausted","cache_hit":False}
        time.sleep(max(0.0, INTERVAL_SECONDS - (time.monotonic()-self.last_call)))
        request = urllib.request.Request(url,headers={"User-Agent":self.user_agent,"Accept":"text/html,application/xhtml+xml"})
        observed = now(); content = None; status = "unexpected_error"
        try:
            with urllib.request.urlopen(request,timeout=30) as response:
                content = response.read().decode("utf-8",errors="replace"); status = "ok"
        except urllib.error.HTTPError as exc:
            status = {403:"forbidden",404:"not_found",429:"rate_limited"}.get(exc.code,"http_error")
            if exc.code == 429: self.rate_limited = True
        except (OSError,urllib.error.URLError,TimeoutError): status = "timeout_or_network"
        self.last_call = time.monotonic(); self.requests += 1
        raw = content.encode("utf-8") if content else b""; sha = hashlib.sha256(raw).hexdigest() if raw else None
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO filing_cache VALUES(?,?,?,?,?)",(url,status,observed,gzip.compress(raw,6) if raw else None,sha))
        return {"status":status,"observed_at":observed,"content":content,"sha256":sha,"cache_hit":False}


def extract_candidates(html: str, max_per_field: int = 3, radius: int = 420) -> dict[str, list[str]]:
    parser = TextExtractor(); parser.feed(html); text = parser.text(); output = {}
    for field, patterns in PATTERNS.items():
        snippets = []
        for pattern in patterns:
            for match in re.finditer(pattern,text,flags=re.IGNORECASE):
                start=max(0,match.start()-radius); end=min(len(text),match.end()+radius)
                snippet=text[start:end].strip()
                if snippet and snippet not in snippets: snippets.append(snippet)
                if len(snippets)>=max_per_field: break
            if len(snippets)>=max_per_field: break
        if snippets: output[field]=snippets
    return output


def index_candidate_filing(
    db: sqlite3.Connection, *, record: dict[str, Any], filing: dict[str, Any],
    fetched: dict[str, Any], candidates: dict[str, list[str]], run_id: str,
) -> dict[str, Any] | None:
    """Index exact SEC keyword windows as provisional document elements.

    The article/filing is not represented as fully parsed: only matched windows
    become elements, and no candidate is promoted to a verified metric or claim.
    """
    if not fetched.get("content") or not fetched.get("sha256") or not candidates:
        return None
    cik = str(record.get("cik") or "").strip()
    subject_id = f"sec:cik:{int(cik):010d}" if cik.isdigit() else f"ticker:{record['ticker']}"
    source_url = str(filing["url"])
    observation, _ = research_control_plane.record_observation(
        db, subject_id=subject_id, capability="sec.filing_candidate_excerpt",
        provider="sec_edgar", source_ref=source_url,
        observed_at=str(fetched.get("observed_at") or now()),
        as_of=filing.get("filing_date"), run_id=run_id, status="available",
        payload={"sha256": fetched["sha256"], "form": filing.get("form"),
                 "candidate_fields": sorted(candidates), "excerpt_only": True},
    )
    parser = TextExtractor()
    parser.feed(str(fetched["content"]))
    flat_text = parser.text()
    elements = []
    for field in sorted(candidates):
        for index, snippet in enumerate(candidates[field]):
            start = flat_text.find(snippet)
            elements.append({
                "label": "narrative_text", "text": snippet, "field": field,
                "candidate_index": index, "char_start": start if start >= 0 else None,
                "char_end": start + len(snippet) if start >= 0 else None,
                "url": source_url,
            })
    extraction = {
        "schema": "ecosystem.document_extraction.v1",
        "provider": "software_filing_evidence_runner",
        "verification_state": "provisional",
        "conversion_status": "candidate_excerpt_only",
        "source": {"sha256": fetched["sha256"], "uri": source_url,
                   "name": f"{filing.get('form') or 'SEC'} {filing.get('filing_date') or ''}".strip()},
        "metrics": {"candidate_fields": len(candidates), "excerpt_count": len(elements)},
        "markdown": "\n\n".join(item["text"] for item in elements),
        "structure": {"elements": elements},
    }
    return information_registry.register_document_extraction(
        db, extraction=extraction, subject_id=subject_id, run_id=run_id,
        document_type="sec_filing_candidate", observation_id=observation["observation_id"],
    )


def _report(folder: Path, state: dict[str, Any], field_counts: Counter[str], statuses: Counter[str]) -> None:
    lines=["# Software annual-filing evidence candidates","",f"Run: `{state['run_id']}`",f"Status: `{state['status']}`","","## Summary","",
           f"- Symbols: **{state['processed_symbols']} / {state['total_symbols']}**",f"- With candidate evidence: **{state['candidate_symbols']}**",
           f"- Without candidate evidence: **{state['no_candidate_symbols']}**",f"- Source unavailable: **{state['source_unavailable']}**",
           f"- Failures: **{state['failed']}**",f"- New filing requests: **{state['source_requests']}**","- Retries: **0**","- LLM calls: **0**","- Automatic packet claims: **0**","",
           "## Candidate fields","","| Field | Symbols |","|---|---:|"]
    lines += [f"| `{k}` | {v} |" for k,v in field_counts.most_common()]
    lines += ["","## Source outcomes","","| Status | Symbols |","|---|---:|"]+[f"| `{k}` | {v} |" for k,v in sorted(statuses.items())]
    lines += ["","## Governance","","Keyword windows are source-backed candidate evidence only. A later extractor or review step must validate metric definition, period, scope and comparability before promotion into a Sector Context Packet."]
    (folder/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")


def run(folder: Path, db_path: Path = DEFAULT_DB) -> int:
    import msvcrt
    folder=Path(folder); lock=(folder.parent/"software_filing_worker.lock").open("a+b"); lock.seek(0)
    try: msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError: lock.close(); return 2
    inputs=json.loads((folder/"input.json").read_text(encoding="utf-8")); state={**inputs,"records":None,"status":"running","pid":os.getpid(),"started_at":now(),"processed_symbols":0,"candidate_symbols":0,"no_candidate_symbols":0,"source_unavailable":0,"failed":0,"source_requests":0,"llm_calls":0}
    save(folder/"state.json",state); db=connect(db_path); cache=None; fields=Counter(); statuses=Counter()
    try:
        schema(db); cache=FilingCache()
        with db: db.execute("INSERT OR REPLACE INTO software_filing_evidence_run VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(inputs["run_id"],inputs["source_run_id"],state["status"],len(inputs["records"]),0,0,0,0,0,0,0,inputs["created_at"],now(),None,json.dumps(state,ensure_ascii=False,sort_keys=True)))
        for index,record in enumerate(inputs["records"],1):
            if (folder/"STOP").exists(): state["status"]="stopped_with_pending"; break
            if cache.rate_limited: state["status"]="stopped_rate_limited"; break
            try:
                filing=record.get("filing")
                if not filing:
                    result={"status":"annual_filing_not_available"}; candidates={}; state["source_unavailable"]+=1
                else:
                    result=cache.get(filing["url"]); candidates=extract_candidates(result["content"]) if result.get("status")=="ok" and result.get("content") else {}
                    state["source_unavailable"]+=int(result.get("status")!="ok")
                status="candidate_evidence_found" if candidates else "no_candidate_evidence"
                state["candidate_symbols"]+=int(bool(candidates)); state["no_candidate_symbols"]+=int(not candidates); fields.update(candidates.keys()); statuses[result.get("status") or "unknown"]+=1
                payload={"schema":"ecosystem.software_filing_evidence_candidate.v1","ticker":record["ticker"],"cik":record.get("cik"),"filing":filing,"source_status":result.get("status"),"source_observed_at":result.get("observed_at"),"source_sha256":result.get("sha256"),"candidates":candidates,"automatic_packet_claims":False,"created_at":now()}
                if filing and candidates:
                    index_candidate_filing(
                        db, record=record, filing=filing, fetched=result,
                        candidates=candidates, run_id=inputs["run_id"],
                    )
                candidate_id="software_filing_candidate_"+digest([inputs["run_id"],record["queue_id"],payload])[:24]
                with db: db.execute("INSERT OR REPLACE INTO software_filing_evidence_candidate VALUES(?,?,?,?,?,?,?,?,?,?,?)",(candidate_id,inputs["run_id"],record["queue_id"],record["ticker"],record.get("cik"),(filing or {}).get("form"),(filing or {}).get("filing_date"),(filing or {}).get("url"),json.dumps(payload,ensure_ascii=False,sort_keys=True),status,payload["created_at"]))
            except Exception as exc:
                state["failed"]+=1; statuses["failed"]+=1
                with (folder/"errors.log").open("a",encoding="utf-8") as handle: handle.write(json.dumps({"ticker":record["ticker"],"error_type":type(exc).__name__,"error":str(exc)},ensure_ascii=False)+"\n")
            state["processed_symbols"]=index; state["source_requests"]=cache.requests
            if index%20==0: state["updated_at"]=now(); save(folder/"state.json",state)
        else: state["status"]="finished_with_review_required"
    except Exception as exc: state.update(status="failed",error_type=type(exc).__name__,error_message=str(exc)[:1000])
    finally:
        state["finished_at"]=now(); state["updated_at"]=state["finished_at"]
        if cache: state["source_requests"]=cache.requests; cache.close()
        save(folder/"state.json",state); _report(folder,state,fields,statuses)
        with db: db.execute("UPDATE software_filing_evidence_run SET status=?,processed_count=?,candidate_count=?,no_candidate_count=?,source_unavailable_count=?,failed_count=?,source_requests=?,updated_at=?,finished_at=?,summary_json=? WHERE run_id=?",(state["status"],state["processed_symbols"],state["candidate_symbols"],state["no_candidate_symbols"],state["source_unavailable"],state["failed"],state["source_requests"],state["updated_at"],state["finished_at"],json.dumps(state,ensure_ascii=False,sort_keys=True),inputs["run_id"]))
        db.close(); lock.close()
    return 1 if state["status"]=="failed" else 0


def start(db_path: Path=DEFAULT_DB) -> dict[str,Any]:
    folder=prepare(db_path); log=(folder/"worker.log").open("a",encoding="utf-8")
    try: process=subprocess.Popen([sys.executable,"-B",str(Path(__file__).resolve()),"run","--job",str(folder),"--db",str(db_path)],cwd=PROJECT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW)
    finally: log.close()
    save(ROOT/"latest.json",{"run":str(folder),"run_id":folder.name,"pid":process.pid})
    notifier=PROJECT/"notify_software_filing_evidence.ps1"
    if notifier.exists():
        subprocess.Popen(["powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-File",str(notifier),"-StatePath",str(folder/"state.json")],cwd=PROJECT,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW)
    return {"status":"dispatched","pid":process.pid,"run":str(folder),"run_id":folder.name}


def status(job: Path|None=None)->dict[str,Any]:
    if job is None: job=Path(json.loads((ROOT/"latest.json").read_text(encoding="utf-8"))["run"])
    return json.loads((job/"state.json").read_text(encoding="utf-8"))


def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("action",choices=("start","run","status"));p.add_argument("--job",type=Path);p.add_argument("--db",type=Path,default=DEFAULT_DB);a=p.parse_args(argv)
    if a.action=="start": result=start(a.db)
    elif a.action=="run":
        if not a.job:p.error("--job is required for run")
        return run(a.job,a.db)
    else:result=status(a.job)
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0


if __name__=="__main__":raise SystemExit(main())
