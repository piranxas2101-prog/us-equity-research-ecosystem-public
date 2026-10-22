"""A fourth, independent classification source: what the company says it does.

A local Ollama model reads the provider business description and walks the
taxonomy one level at a time, choosing among the real child nodes or stopping
with "unclear". It never sees the other sources' votes, so it can break a tie
between them instead of echoing one. Answers are cached per (ticker,
description hash, model digest, prompt version), so a rerun costs nothing and
a model or prompt change is visible in the evidence.

The vote is advisory only: classification_reassessment attaches it to review
rows as evidence for the human reviewer and never counts it toward the
automatic agreement, because the local model must not make decisions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .classification_reassessment import JOBS_ROOT, PROJECT, Vote, load_profiles
from .core import taxonomy

PROMPT_VERSION = "description-vote-1"
CACHE_DB = JOBS_ROOT / "description_votes.sqlite3"
DEFAULT_MODEL = "qwen2.5:3b"
DEFAULT_HOST = "http://127.0.0.1:11434"
MAX_DEPTH = 2  # sector and group; finer levels need more than a short description
MAX_DESCRIPTION_CHARS = 1500
UNCLEAR = "unclear"

SYSTEM = ("You classify a listed company into an industry taxonomy using only its business description. "
          "Choose the option that describes how the company makes most of its revenue. "
          f"If the description does not make the choice clear, answer \"{UNCLEAR}\".")


class ModelClient(Protocol):
    def models(self) -> dict[str, str] | None: ...
    def generate(self, *, model: str, system: str, prompt: str, schema: dict[str, Any]) -> str: ...


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def children(nodes: dict, parent: str | None) -> list[str]:
    depth = 1 if parent is None else parent.count(".") + 2
    prefix = "" if parent is None else parent + "."
    return sorted(node for node in nodes if node.count(".") + 1 == depth and node.startswith(prefix))


def _option(node: str) -> str:
    return node.rsplit(".", 1)[-1]


def build_prompt(name: str, description: str, parent: str | None, options: list[str]) -> str:
    where = "the top-level sector" if parent is None else f"the sub-category inside \"{parent.replace('.', ' > ')}\""
    listing = "\n".join(f"- {_option(option)}" for option in options)
    return (f"Company: {name}\nDescription: {description[:MAX_DESCRIPTION_CHARS]}\n\n"
            f"Pick {where}. Options:\n{listing}\n- {UNCLEAR}\n\nAnswer with JSON: {{\"choice\": \"<option>\"}}")


def descend(client: ModelClient, model: str, name: str, description: str, nodes: dict,
            max_depth: int = MAX_DEPTH) -> tuple[str | None, list[str]]:
    """Deepest node the model is willing to choose, plus its raw choices for the evidence."""
    node: str | None = None
    trail: list[str] = []
    while (node.count(".") + 1 if node else 0) < max_depth:
        options = children(nodes, node)
        if not options:
            break
        schema = {"type": "object", "properties": {"choice": {"type": "string",
                  "enum": [_option(option) for option in options] + [UNCLEAR]}}, "required": ["choice"]}
        try:
            choice = str(json.loads(client.generate(model=model, system=SYSTEM,
                                                    prompt=build_prompt(name, description, node, options),
                                                    schema=schema)).get("choice") or UNCLEAR)
        except (json.JSONDecodeError, AttributeError):
            choice = UNCLEAR
        trail.append(choice)
        picked = next((option for option in options if _option(option) == choice), None)
        if picked is None:
            break
        node = picked
    return node, trail


# ---------------------------------------------------------------- cache


def schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS description_vote(
        ticker TEXT NOT NULL, description_sha256 TEXT NOT NULL, model TEXT NOT NULL, model_digest TEXT NOT NULL,
        prompt_version TEXT NOT NULL, node_id TEXT, trail_json TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(ticker, description_sha256, model_digest, prompt_version))""")


def open_cache(path: Path = CACHE_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    schema(db)
    return db


def vote_for(cache: sqlite3.Connection, client: ModelClient, model: str, digest: str, ticker: str,
             name: str, description: str, nodes: dict) -> str | None:
    sha = text_sha(description)
    row = cache.execute("SELECT node_id FROM description_vote WHERE ticker=? AND description_sha256=? AND model_digest=? "
                        "AND prompt_version=?", (ticker, sha, digest, PROMPT_VERSION)).fetchone()
    if row is not None:
        return row[0]
    node, trail = descend(client, model, name, description, nodes)
    cache.execute("INSERT INTO description_vote VALUES(?,?,?,?,?,?,?,?)",
                  (ticker, sha, model, digest, PROMPT_VERSION, node, json.dumps(trail), now()))
    cache.commit()
    return node


def cached_votes(profiles: dict[str, dict[str, Any]], path: Path = CACHE_DB) -> dict[str, list[Vote]]:
    """Votes for tickers whose current description was already read by the current prompt."""
    if not path.exists():
        return {}
    db = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT ticker, description_sha256, model, model_digest, node_id FROM description_vote "
                          "WHERE prompt_version=? AND node_id IS NOT NULL ORDER BY created_at", (PROMPT_VERSION,)).fetchall()
    finally:
        db.close()
    votes: dict[str, list[Vote]] = {}
    for ticker, sha, model, digest, node in rows:
        description = (profiles.get(ticker) or {}).get("description")
        if description and text_sha(description) == sha:
            votes[ticker] = [Vote("description", node, f"local_llm:{model}@{digest[:12]}:{PROMPT_VERSION}")]
    return votes


# ---------------------------------------------------------------- runs


def _latest_proposals() -> list[dict[str, Any]]:
    jobs = sorted(JOBS_ROOT.glob("babis-reassessment-*/proposals.json"))
    if not jobs:
        raise SystemExit("No reassessment job yet; run python -m research_registry.classification_reassessment first.")
    return json.loads(jobs[-1].read_text(encoding="utf-8"))


def select_rows(mode: str, sample: int, readable: set[str], seed: int = 7) -> list[dict[str, Any]]:
    """Rows to vote on; only tickers with a description count toward the sample."""
    rows = [row for row in _latest_proposals() if row["ticker"].upper() in readable]
    if mode == "calibrate":
        # Companies whose classification two sources already agree on, at group level or deeper.
        pool = [row for row in rows if row["status"] == "confirmed_same_node" and (row.get("old_node") or "").count(".") >= 1]
        random.Random(seed).shuffle(pool)
        return pool[:sample] if sample else pool
    pool = [row for row in rows if row["status"] == "reassessment_review" and row["reason"] == "no_two_sources_agree"]
    return pool[:sample] if sample else pool


def run(client: ModelClient, model: str, mode: str, sample: int, cache_path: Path = CACHE_DB) -> dict[str, Any]:
    installed = client.models()
    if installed is None:
        raise SystemExit("Ollama is not running at 127.0.0.1:11434. Open it yourself; this command never starts it.")
    digest = next((d for n, d in installed.items() if n in {model, f"{model}:latest"}), None)
    if digest is None:
        raise SystemExit(f"Model {model} is not installed in Ollama.")
    _, nodes = taxonomy()
    profiles = load_profiles(PROJECT)
    cache = open_cache(cache_path)
    outcome: Counter = Counter()
    misses: list[dict[str, Any]] = []
    try:
        readable = {ticker for ticker, profile in profiles.items() if profile.get("description")}
        for row in select_rows(mode, sample, readable):
            ticker = row["ticker"].upper()
            description = (profiles.get(ticker) or {}).get("description")
            if not description:
                outcome["no_description"] += 1
                continue
            node = vote_for(cache, client, model, digest, ticker, row.get("name") or ticker, description, nodes)
            if mode != "calibrate":
                outcome["voted" if node else "unclear"] += 1
                continue
            truth = row["old_node"]
            if node is None:
                outcome["unclear"] += 1
            elif truth.split(".")[0] != node.split(".")[0]:
                outcome["wrong_sector"] += 1
                misses.append({"ticker": ticker, "truth": truth, "vote": node})
            elif truth == node or truth.startswith(node + ".") and node.count(".") >= 1:
                outcome["right_group"] += 1
            elif truth.startswith(node + ".") or node.startswith(truth + "."):
                outcome["right_sector_only"] += 1
            else:
                outcome["wrong_group"] += 1
                misses.append({"ticker": ticker, "truth": truth, "vote": node})
    finally:
        cache.close()
    result: dict[str, Any] = {"mode": mode, "model": model, "model_digest": digest, "prompt_version": PROMPT_VERSION,
                              "counts": dict(outcome)}
    if mode == "calibrate":
        voted = sum(outcome[key] for key in ("right_group", "right_sector_only", "wrong_group", "wrong_sector"))
        result["sector_precision"] = round(1 - outcome["wrong_sector"] / voted, 3) if voted else None
        result["group_precision"] = round(outcome["right_group"] / (outcome["right_group"] + outcome["wrong_group"]), 3) \
            if outcome["right_group"] + outcome["wrong_group"] else None
        result["misses"] = misses
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local-LLM description vote for Babis reassessment.")
    parser.add_argument("mode", choices=["calibrate", "run"])
    parser.add_argument("--sample", type=int, default=0, help="Limit rows (0 = all).")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--host", default=DEFAULT_HOST)
    args = parser.parse_args(argv)
    from filing_llm_extractor import OllamaClient  # the one local-only Ollama HTTP client

    client = OllamaClient(args.host, timeout_seconds=180, num_ctx=2048, num_predict=16)
    print(json.dumps(run(client, args.model, args.mode, args.sample), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
