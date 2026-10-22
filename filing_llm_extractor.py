"""Quote-verified evidence extraction from cached SEC annual filings via local Ollama.

Sector agents need text-only facts (business model, competitive position,
risks) that no rule can read out of a 10-K, so those fields stay ``unknown``.
This worker picks a short list of relevant sentences from filings that are
already cached on disk, numbers them, and asks a *local* Ollama model which
sentence numbers state each field. The evidence text is then taken from our own
sentence list, word for word, so the model cannot invent wording; a number that
does not exist is rejected, and a field the model finds nothing for stays
unknown. Returning numbers instead of rewritten text also keeps each call short
on a CPU.

Boundaries:
- Local only: talks HTTP to the configured Ollama host, never to a hosted model,
  and never starts the Ollama app. If Ollama is not running or the model is not
  installed, it stops with a clear message.
- No new downloads: reads only filings already in the software filing cache.
- Candidate evidence only: results are stored for human review and are never
  written into Sector Context Packets or used by any scoring or trading step.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol

from common import load_json
from research_registry.core import DEFAULT_DB, PROJECT, connect
from software_filing_evidence_runner import CACHE_DB

CONFIG_PATH = PROJECT / "filing_llm_extraction_config.json"
OUTPUT_DIR = PROJECT / "output"
# v2: the model returns sentence numbers instead of rewriting quotes and summaries.
PROMPT_VERSION = "filing-evidence-v2"
RUN_SCHEMA = "ecosystem.filing_llm_extraction_run.v1"

FIELD_DEFINITIONS = {
    "business_model": "How the company makes money: what it sells, to whom, and how it charges (subscription, license, usage, transaction, services).",
    "revenue_drivers": "What makes revenue grow or shrink (customers, seats, volume, pricing, contract renewals, specific markets).",
    "competitive_position": "Who the company competes with and on what basis (product, price, scale, integration, relationships).",
    "industry_risks": "Risks that affect the whole industry, not just this company (regulation, technology shifts, macro demand, security threats).",
    "company_specific_risks": "Risks particular to this company (customer concentration, key products, debt, execution, material weaknesses).",
}
SECTION_FIELDS = {
    "business": ("business_model", "revenue_drivers", "competitive_position"),
    "risk_factors": ("industry_risks", "company_specific_risks"),
}

# Words that make a sentence worth showing the model for a field. Sentences
# without any of them are not sent, which keeps the input short on a CPU.
FIELD_KEYWORDS = {
    "business_model": ("provide", "offer", "sell", "develop", "subscription", "licens", "platform", "solution",
                       "service", "product"),
    "revenue_drivers": ("revenue", "recurring", "subscription", "transaction", "volume", "pricing", "renewal",
                        "growth", "bookings"),
    "competitive_position": ("compet",),
    "industry_risks": ("industry", "regulat", "economic", "technolog", "cyber", "interest rate", "inflation",
                       "market conditions"),
    "company_specific_risks": ("our customers", "we depend", "concentrat", "indebted", "our debt", "key personnel",
                               "acquisition", "material weakness", "our ability"),
}

SYSTEM_PROMPT = (
    "You find evidence in numbered sentences from a company's SEC annual report. For each requested field, "
    "return the numbers of up to two sentences that directly state it. Return an empty list when no sentence "
    "clearly states it. Use only the numbers shown."
)

_QUOTE_CHARS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                              "–": "-", "—": "-", " ": " "})


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize(text: str) -> str:
    """Make quote matching robust to entities, curly quotes, dashes and whitespace."""
    return re.sub(r"\s+", " ", unescape(text).translate(_QUOTE_CHARS)).strip()


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    return load_json(path)


# ---------------------------------------------------------------- sections


_BLOCK_TAGS = {"p", "div", "br", "tr", "td", "th", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section"}
_HEADING_MAX_CHARS = 100
# "Item 1. Business", "ITEM 1A. RISK FACTORS.", "Item 1 . Business", "Item 4.A.", "Item 3.D Risk Factors"
_ITEM_RE = re.compile(r"^item\s*(\d{1,2})(?:\s*\.?\s*([a-d])(?![a-z]))?\s*[.:\-]?\s*(.*)$", re.IGNORECASE)


class _BlockParser(HTMLParser):
    """Collect the text of each HTML block separately, so headings stay distinguishable from prose."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = [""]
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in {"script", "style", "noscript"}:
            self.skip += 1
        elif tag in _BLOCK_TAGS:
            self.blocks.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK_TAGS:
            self.blocks.append("")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.blocks[-1] += data


@dataclass(frozen=True)
class Heading:
    item: str   # "1", "1a", "4a", or "sub" for a 20-F "Risk Factors" sub-heading
    title: str  # lower-case heading text after the item number
    start: int  # character offset in FilingText.text


@dataclass(frozen=True)
class FilingText:
    text: str
    headings: tuple[Heading, ...]


def parse_filing(html: str) -> FilingText:
    """Flatten a filing to text while recording where its item headings are.

    A heading is a short block of its own that starts with "Item" (joined with
    the next block when the title sits on a separate line). References inside
    sentences, such as 'see "Item 1A. Risk Factors"', are prose, not headings.
    """
    parser = _BlockParser()
    parser.feed(html)
    blocks = [normalize(block) for block in parser.blocks]
    blocks = [block for block in blocks if block]
    parts: list[str] = []
    headings: list[Heading] = []
    offset = 0
    for index, block in enumerate(blocks):
        if len(block) <= _HEADING_MAX_CHARS:
            match = _ITEM_RE.match(block)
            if match:
                title = match.group(3)
                following = blocks[index + 1] if index + 1 < len(blocks) else ""
                if not title.strip(" .") and len(following) <= _HEADING_MAX_CHARS and not _ITEM_RE.match(following):
                    title = following
                item = (match.group(1) + (match.group(2) or "")).lower()
                headings.append(Heading(item, title.lower().strip(" ."), offset))
            elif block.lower().strip(" .") in {"risk factors", "d. risk factors", "d risk factors"}:
                headings.append(Heading("sub", "risk factors", offset))
        parts.append(block)
        offset += len(block) + 1
    return FilingText(" ".join(parts), tuple(headings))


def _span(doc: FilingText, is_start: Any, is_end: Any, min_chars: int) -> tuple[int, int] | None:
    """First start heading whose next end heading is far enough away.

    Table-of-contents entries sit a few hundred characters apart, so this skips
    them and lands on the real section.
    """
    for index, heading in enumerate(doc.headings):
        if not is_start(heading):
            continue
        end = next((later.start for later in doc.headings[index + 1:] if is_end(later)), None)
        if end is not None and end - heading.start >= min_chars:
            return heading.start, end
    return None


def locate_sections(doc: FilingText, min_chars: int = 2000) -> dict[str, tuple[int, int]]:
    """Business and risk-factor sections for 10-K and 20-F layouts."""
    business = (
        _span(doc, lambda h: h.item == "1" and "business" in h.title,
              lambda h: h.item in {"1a", "1b", "1c", "2"}, min_chars)
        # 20-F: "Item 4. Information on the Company" (some issuers write "Information about <name>")
        or _span(doc, lambda h: h.item == "4" and h.title.startswith("information"),
                 lambda h: h.item in {"4a", "5"}, min_chars)
    )
    risks = (
        _span(doc, lambda h: h.item == "1a" and "risk" in h.title,
              lambda h: h.item in {"1b", "1c", "2"}, min_chars)
        or _span(doc, lambda h: h.item == "sub" or (h.item == "3d" and "risk" in h.title),
                 lambda h: h.item == "4", min_chars)
    )
    sections: dict[str, tuple[int, int]] = {}
    if business:
        sections["business"] = business
    if risks:
        sections["risk_factors"] = risks
    return sections


@dataclass(frozen=True)
class Excerpt:
    section: str
    fields: tuple[str, ...]
    sentences: tuple[str, ...]

    @property
    def text(self) -> str:
        return " ".join(self.sentences)


def split_sentences(text: str, min_chars: int = 30, max_chars: int = 500) -> list[str]:
    """Split flattened filing text into sentences, dropping fragments and walls of text."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"(])", normalize(text))
    return [part.strip() for part in parts if min_chars <= len(part.strip()) <= max_chars]


def select_sentences(sentences: list[str], fields: tuple[str, ...], *, lead: int, per_field: int) -> tuple[str, ...]:
    """Keep the first ``lead`` sentences plus up to ``per_field`` keyword matches per field, in text order."""
    chosen = set(range(min(lead, len(sentences))))
    for field in fields:
        keywords = FIELD_KEYWORDS[field]
        matches = [index for index, sentence in enumerate(sentences)
                   if index not in chosen and any(word in sentence.lower() for word in keywords)]
        chosen.update(matches[:per_field])
    return tuple(sentences[index] for index in sorted(chosen))


def build_excerpts(doc: FilingText, config: dict[str, Any]) -> list[Excerpt]:
    """Pick a short list of relevant sentences from the business and risk sections."""
    sections = locate_sections(doc)
    excerpts: list[Excerpt] = []
    for section, lead in (("business", int(config["business_lead_sentences"])), ("risk_factors", 0)):
        if section not in sections:
            continue
        start, end = sections[section]
        body = doc.text[start:end][: int(config["section_scan_chars"])]
        fields = SECTION_FIELDS[section]
        chosen = select_sentences(split_sentences(body), fields, lead=lead, per_field=int(config["sentences_per_field"]))
        if chosen:
            excerpts.append(Excerpt(section, fields, chosen))
    return excerpts


# ---------------------------------------------------------------- model I/O


class ModelClient(Protocol):
    def models(self) -> dict[str, str] | None: ...
    def generate(self, *, model: str, system: str, prompt: str, schema: dict[str, Any]) -> str: ...


class OllamaClient:
    """Minimal HTTP client for a local Ollama server. It never launches Ollama."""

    def __init__(self, host: str, *, timeout_seconds: float, num_ctx: int, num_predict: int,
                 num_thread: int | None = None) -> None:
        self.host = host.rstrip("/")
        self.timeout = timeout_seconds
        self.options: dict[str, Any] = {"temperature": 0, "seed": 0, "num_ctx": num_ctx, "num_predict": num_predict}
        if num_thread:
            self.options["num_thread"] = num_thread

    def models(self) -> dict[str, str] | None:
        """Installed models as {name: digest}, or None if Ollama is not reachable."""
        try:
            with urllib.request.urlopen(self.host + "/api/tags", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            return None
        return {str(item.get("name")): str(item.get("digest") or "") for item in payload.get("models") or []}

    def generate(self, *, model: str, system: str, prompt: str, schema: dict[str, Any]) -> str:
        body = {"model": model, "system": system, "prompt": prompt, "stream": False,
                "format": schema, "options": self.options}
        request = urllib.request.Request(self.host + "/api/generate", data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return str(json.loads(response.read().decode("utf-8")).get("response") or "")


MAX_SENTENCES_PER_FIELD = 2


def output_schema(fields: tuple[str, ...]) -> dict[str, Any]:
    numbers = {"type": "array", "items": {"type": "integer"}, "maxItems": MAX_SENTENCES_PER_FIELD}
    return {"type": "object", "properties": {field: numbers for field in fields}, "required": list(fields)}


def build_prompt(excerpt: Excerpt) -> str:
    lines = ["Fields:"]
    lines += [f"- {field}: {FIELD_DEFINITIONS[field]}" for field in excerpt.fields]
    lines += ["", f"Sentences ({excerpt.section.replace('_', ' ')}):"]
    lines += [f"[{number}] {sentence}" for number, sentence in enumerate(excerpt.sentences, start=1)]
    return "\n".join(lines)


def parse_output(raw: str, fields: tuple[str, ...]) -> dict[str, list[Any]] | None:
    """Parse {field: [numbers]}; anything that is not a list becomes an empty list."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return {field: payload[field] if isinstance(payload.get(field), list) else [] for field in fields}


def resolve_sentences(numbers: list[Any], excerpt: Excerpt) -> tuple[str, list[int]]:
    """Map the model's sentence numbers back to our own exact sentences.

    The evidence text always comes from the filing, never from the model, so the
    model cannot put words in a company's mouth. A number outside the list fails.
    """
    valid: list[int] = []
    for number in numbers[:MAX_SENTENCES_PER_FIELD]:
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= len(excerpt.sentences):
            return "invalid_sentence_number", []
        if number not in valid:
            valid.append(number)
    return ("verified" if valid else "not_in_excerpt"), valid


def extract_excerpt(client: ModelClient, model: str, excerpt: Excerpt, config: dict[str, Any]) -> list[dict[str, Any]]:
    """One model call for one excerpt; returns one result per requested field."""
    try:
        raw = client.generate(model=model, system=SYSTEM_PROMPT, prompt=build_prompt(excerpt),
                              schema=output_schema(excerpt.fields))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return [{"field": field, "status": "model_error", "summary": None, "quote": None, "detail": str(exc)[:300]}
                for field in excerpt.fields]
    parsed = parse_output(raw, excerpt.fields)
    if parsed is None:
        return [{"field": field, "status": "invalid_output", "summary": None, "quote": None, "detail": raw[:300]}
                for field in excerpt.fields]
    results = []
    for field in excerpt.fields:
        status, numbers = resolve_sentences(parsed[field], excerpt)
        quote = " ".join(excerpt.sentences[number - 1] for number in numbers) or None
        results.append({"field": field, "status": status, "summary": None, "quote": quote,
                        "detail": json.dumps({"sentence_numbers": parsed[field]})})
    return results


# ---------------------------------------------------------------- storage


def schema(db: sqlite3.Connection) -> None:
    db.executescript("""
      CREATE TABLE IF NOT EXISTS filing_llm_extraction_run(
        run_id TEXT PRIMARY KEY, model TEXT NOT NULL, model_digest TEXT NOT NULL,
        prompt_version TEXT NOT NULL, status TEXT NOT NULL, filings_processed INTEGER NOT NULL,
        llm_calls INTEGER NOT NULL, created_at TEXT NOT NULL, finished_at TEXT, summary_json TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS filing_llm_extraction(
        extraction_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, ticker TEXT NOT NULL, cik TEXT,
        filing_url TEXT NOT NULL, filing_form TEXT, filing_date TEXT, document_sha256 TEXT NOT NULL,
        section TEXT NOT NULL, field TEXT NOT NULL, status TEXT NOT NULL, summary TEXT, quote TEXT,
        detail TEXT, model TEXT NOT NULL, model_digest TEXT NOT NULL, prompt_version TEXT NOT NULL,
        verification_state TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(document_sha256, section, field, model, prompt_version));
    """)


def cached_filings(registry_db: sqlite3.Connection, cache_path: Path) -> list[dict[str, Any]]:
    """Annual filings already on disk, joined to their ticker. No network access."""
    records: dict[str, dict[str, Any]] = {}
    for row in registry_db.execute(
        "SELECT ticker, cik, filing_form, filing_date, filing_url FROM software_filing_evidence_candidate "
        "WHERE filing_url IS NOT NULL ORDER BY ticker"
    ):
        records.setdefault(row["filing_url"], dict(row))
    cache = sqlite3.connect(f"file:{cache_path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        output = []
        for url, content, sha in cache.execute(
            "SELECT url, content_gzip, content_sha256 FROM filing_cache WHERE status='ok' AND content_gzip IS NOT NULL"
        ):
            if url in records:
                output.append({**records[url], "content_gzip": content, "sha256": sha})
        return sorted(output, key=lambda item: item["ticker"])
    finally:
        cache.close()


def _already_done(db: sqlite3.Connection, sha: str, section: str, fields: tuple[str, ...], model: str) -> bool:
    # A failed call (timeout, Ollama error) is not "done": the next run retries it.
    count = db.execute(
        "SELECT COUNT(*) FROM filing_llm_extraction WHERE document_sha256=? AND section=? AND model=? "
        "AND prompt_version=? AND status != 'model_error'",
        (sha, section, model, PROMPT_VERSION),
    ).fetchone()[0]
    return count >= len(fields)


def _store(db: sqlite3.Connection, *, run_id: str, filing: dict[str, Any], section: str,
           result: dict[str, Any], model: str, digest: str) -> None:
    key = f"{filing['sha256']}|{section}|{result['field']}|{model}|{PROMPT_VERSION}"
    db.execute(
        "INSERT OR REPLACE INTO filing_llm_extraction VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("llmx_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24], run_id, filing["ticker"], filing.get("cik"),
         filing["filing_url"], filing.get("filing_form"), filing.get("filing_date"), filing["sha256"], section,
         result["field"], result["status"], result["summary"], result["quote"], result["detail"], model, digest,
         PROMPT_VERSION, "provisional_needs_review" if result["status"] == "verified" else "rejected_or_unknown",
         now()),
    )


# ---------------------------------------------------------------- commands


class OllamaNotReady(RuntimeError):
    pass


def check_model(client: ModelClient, model: str, host: str) -> str:
    """Return the installed model's digest or explain exactly what is missing."""
    if not model:
        raise OllamaNotReady(f"No model configured. Set \"model\" in {CONFIG_PATH.name} or pass --model.")
    installed = client.models()
    if installed is None:
        raise OllamaNotReady(f"Ollama is not running at {host}. Open the Ollama app yourself, then rerun. "
                             "This command never starts Ollama.")
    for name, digest in installed.items():
        if name == model or name == f"{model}:latest":
            return digest
    raise OllamaNotReady(f"Model {model} is not installed in Ollama. Install it yourself with: ollama pull {model}")


def run(*, client: ModelClient, model: str, host: str, config: dict[str, Any], limit: int,
        tickers: set[str] | None = None, db_path: Path = DEFAULT_DB, cache_path: Path = CACHE_DB) -> dict[str, Any]:
    digest = check_model(client, model, host)
    run_id = "filing-llm-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    db = connect(db_path)
    try:
        schema(db)
        filings = [item for item in cached_filings(db, cache_path) if not tickers or item["ticker"] in tickers]
        counts: dict[str, int] = {}
        processed = calls = 0
        db.execute("INSERT INTO filing_llm_extraction_run VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (run_id, model, digest, PROMPT_VERSION, "running", 0, 0, now(), None, "{}"))
        db.commit()
        for filing in filings:
            if processed >= limit:
                break
            doc = parse_filing(gzip.decompress(filing["content_gzip"]).decode("utf-8", errors="replace"))
            excerpts = build_excerpts(doc, config)
            todo = [item for item in excerpts if not _already_done(db, filing["sha256"], item.section, item.fields, model)]
            if not todo:
                continue
            for excerpt in todo:
                calls += 1
                for result in extract_excerpt(client, model, excerpt, config):
                    _store(db, run_id=run_id, filing=filing, section=excerpt.section, result=result, model=model, digest=digest)
                    counts[result["status"]] = counts.get(result["status"], 0) + 1
                db.commit()
            processed += 1
            print(f"[{processed}/{min(limit, len(filings))}] {filing['ticker']}: {len(todo)} excerpt(s)", flush=True)
        summary = {"schema": RUN_SCHEMA, "run_id": run_id, "model": model, "model_digest": digest,
                   "prompt_version": PROMPT_VERSION, "filings_processed": processed, "llm_calls": calls,
                   "result_counts": counts, "packet_writes": 0, "network_sources": ["local ollama only"]}
        db.execute("UPDATE filing_llm_extraction_run SET status='completed', filings_processed=?, llm_calls=?, "
                   "finished_at=?, summary_json=? WHERE run_id=?",
                   (processed, calls, now(), json.dumps(summary, ensure_ascii=False, sort_keys=True), run_id))
        db.commit()
        return summary
    finally:
        db.close()


def write_review(db_path: Path = DEFAULT_DB, output_dir: Path = OUTPUT_DIR) -> tuple[Path, Path]:
    """Write a CSV you can mark up (correct / partly / wrong) and a short summary."""
    db = connect(db_path)
    try:
        schema(db)
        rows = [dict(row) for row in db.execute(
            "SELECT ticker, field, status, summary, quote, filing_url, model, prompt_version, created_at "
            "FROM filing_llm_extraction ORDER BY ticker, field")]
    finally:
        db.close()
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "filing_llm_extraction_review_latest.csv"
    md_path = output_dir / "filing_llm_extraction_review_latest.md"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=[*(rows[0] if rows else {"ticker": ""}), "reviewer_verdict", "reviewer_note"])
        writer.writeheader()
        writer.writerows({**row, "reviewer_verdict": "", "reviewer_note": ""} for row in rows)
    by_field: dict[str, dict[str, int]] = {}
    for row in rows:
        by_field.setdefault(row["field"], {}).setdefault(row["status"], 0)
        by_field[row["field"]][row["status"]] += 1
    lines = ["# Filing evidence extraction review", "",
             f"- Results: {len(rows)} across {len({row['ticker'] for row in rows})} companies",
             "- `verified`: the model pointed to real sentences; the `quote` column is the filing's own text. "
             "Check that it actually states the field.",
             "- `invalid_sentence_number`: the model gave a number that does not exist; rejected.",
             "- `not_in_excerpt`: the model found nothing for this field; stays unknown.", "",
             "| Field | verified | invalid_sentence_number | not_in_excerpt | other |", "|---|---:|---:|---:|---:|"]
    for field in FIELD_DEFINITIONS:
        stats = by_field.get(field, {})
        other = sum(v for k, v in stats.items() if k not in {"verified", "invalid_sentence_number", "not_in_excerpt"})
        lines.append(f"| {field} | {stats.get('verified', 0)} | {stats.get('invalid_sentence_number', 0)} | "
                     f"{stats.get('not_in_excerpt', 0)} | {other} |")
    lines += ["", f"Mark `reviewer_verdict` in `{csv_path.name}` as correct, partly or wrong. "
              "Nothing here is used by any agent until promotion is decided."]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path, md_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Quote-verified filing evidence via local Ollama (candidates only).")
    sub = parser.add_subparsers(dest="command", required=True)
    run_cmd = sub.add_parser("run", help="Extract from cached filings with a local Ollama model.")
    run_cmd.add_argument("--model", default=None)
    run_cmd.add_argument("--limit", type=int, default=5, help="Maximum filings to process (default 5).")
    run_cmd.add_argument("--tickers", default="", help="Comma-separated tickers to restrict to.")
    sub.add_parser("review", help="Write the review CSV and summary from stored results.")
    args = parser.parse_args(argv)
    if args.command == "review":
        csv_path, md_path = write_review()
        print(f"[OUTPUT] {csv_path}\n[OUTPUT] {md_path}")
        return 0
    config = load_config()
    model = args.model or str(config.get("model") or "")
    host = str(config["ollama_host"])
    client = OllamaClient(host, timeout_seconds=float(config["timeout_seconds"]),
                          num_ctx=int(config["num_ctx"]), num_predict=int(config["num_predict"]),
                          num_thread=int(config["num_thread"]) if config.get("num_thread") else None)
    tickers = {item.strip().upper() for item in args.tickers.split(",") if item.strip()} or None
    try:
        summary = run(client=client, model=model, host=host, config=config, limit=max(0, args.limit), tickers=tickers)
    except OllamaNotReady as exc:
        print(f"[STOPPED] {exc}")
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
