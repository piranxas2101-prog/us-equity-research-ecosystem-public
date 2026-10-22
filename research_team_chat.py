"""Research-only multi-agent conversations for the Slack gateway.

The module deliberately has no broker imports and exposes no order-submission
surface. It builds a fresh, cited API-first research packet, applies deterministic
named-agent rules, and writes an immutable event for the Notion projection.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from market_research_provider import ApiFirstResearchProvider
from rule_based_agent_engine import RuleBasedAgentEngine


CONVERSATION_SCHEMA = "ecosystem.research_conversation.v1"
OUTBOX_SCHEMA = "ecosystem.integration_outbox.v2"
READ_ONLY_AGENTS = frozenset({"koulis", "thymios", "miltos"})

TEAM_AGENTS: dict[str, tuple[str, ...]] = {
    "research": ("babis", "giannis", "petros"),
    "traders": ("yankos", "periklis", "petros"),
    "strategy": ("sokratis", "yankos", "periklis"),
    "risk": ("koulis", "thymios", "miltos"),
    "ecosystem": (
        "babis",
        "giannis",
        "yankos",
        "periklis",
        "sokratis",
        "petros",
        "koulis",
        "thymios",
        "miltos",
    ),
}

TEAM_LEAD = {
    "research": "petros",
    "traders": "petros",
    "strategy": "sokratis",
    "risk": "koulis",
    "ecosystem": "petros",
}

AGENT_PROFILES: dict[str, dict[str, str]] = {
    "babis": {
        "label": "Babis — Catalysts",
        "role": "Find catalysts, narrative changes, news risk, and what the market may notice next.",
    },
    "giannis": {
        "label": "Giannis — Fundamentals",
        "role": "Audit evidence quality, business fundamentals, valuation, and long/short thesis logic.",
    },
    "yankos": {
        "label": "Yankos — Tactical Setup",
        "role": "Assess tactical price structure, timing, invalidation levels, and near-term setup quality.",
    },
    "periklis": {
        "label": "Periklis — Patient Structure",
        "role": "Assess slower swing structure, confirmation requirements, patience, and asymmetric scenarios.",
    },
    "sokratis": {
        "label": "Sokratis — Strategy",
        "role": "Challenge methodology, base rates, regime assumptions, and whether the strategy is testable.",
    },
    "petros": {
        "label": "Petros — Synthesis",
        "role": "Reconcile disagreements, separate facts from inference, and state what proves or kills the thesis.",
    },
    "koulis": {
        "label": "Koulis — Risk Critique",
        "role": "Provide read-only portfolio and downside critique. Never approve, size, or execute an order.",
    },
    "thymios": {
        "label": "Thymios — Contract Validation",
        "role": "Provide read-only validation of evidence, assumptions, and rule compliance. Never approve or execute.",
    },
    "miltos": {
        "label": "Miltos — Monitoring",
        "role": "Provide read-only monitoring conditions, readiness checks, and observable triggers. Never execute.",
    },
}

_TICKER = re.compile(r"(?<![A-Za-z0-9])\$?([A-Z][A-Z0-9.-]{0,9})(?![A-Za-z0-9])")
_STOPWORDS = {
    "AI", "ALL", "AND", "ARE", "BUY", "CEO", "CFO", "ETF", "FOR", "FROM",
    "I", "IPO", "IS", "IT", "LONG", "NOT", "OR", "SEC", "SELL", "SHORT",
    "THE", "THIS", "TO", "USA", "USD", "WHAT", "WITH", "YOU",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE pairs without printing or overriding the process."""

    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def extract_tickers(text: str, *, limit: int = 3) -> list[str]:
    """Extract explicit uppercase US-style tickers while rejecting prose tokens."""

    matches = list(_TICKER.finditer(text or ""))
    explicit = [match for match in matches if match.group(0).startswith("$")]
    candidates = explicit or matches
    found: list[str] = []
    for match in candidates:
        ticker = match.group(1).upper()
        if ticker in _STOPWORDS or ticker in found:
            continue
        found.append(ticker)
        if len(found) >= limit:
            break
    return found


def contains_execution_language(text: str) -> bool:
    terms = (
        r"\b(execute|submit|place|cancel)\s+(an?\s+)?(order|trade)\b",
        r"\b(buy|sell)\s+\d+\b",
        r"\b(αγορασε|πούλησε|πουλησε|εκτέλεσε|εκτελεσε)\b",
    )
    lower = (text or "").lower()
    return any(re.search(pattern, lower, re.IGNORECASE) for pattern in terms)


def _finite_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return round(result, 4)


class YFinanceResearchProvider:
    """Create a compact current packet without touching brokerage services."""

    def __init__(self, *, now: Callable[[], str] = utc_now) -> None:
        self.now = now

    def build(self, ticker: str) -> dict[str, Any]:
        symbol = ticker.upper().strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol):
            raise ValueError(f"Invalid ticker: {ticker!r}")

        observed_at = self.now()
        packet: dict[str, Any] = {
            "ticker": symbol,
            "observed_at": observed_at,
            "status": "degraded",
            "price": {},
            "fundamentals": {},
            "news": [],
            "sources": [
                {
                    "name": "SEC EDGAR company search",
                    "url": f"https://www.sec.gov/edgar/search/#/q={symbol}",
                    "observed_at": observed_at,
                },
                {
                    "name": "Yahoo Finance quote",
                    "url": f"https://finance.yahoo.com/quote/{symbol}/",
                    "observed_at": observed_at,
                },
            ],
            "warnings": [],
        }
        try:
            import yfinance as yf  # type: ignore

            security = yf.Ticker(symbol)
            history = security.history(
                period="1y", interval="1d", auto_adjust=False, timeout=12
            )
            if history is not None and not history.empty:
                close = history["Close"].dropna()
                volume = history["Volume"].dropna() if "Volume" in history else None
                latest = close.iloc[-1] if len(close) else None
                packet["price"] = {
                    "last": _finite_number(latest),
                    "as_of": str(close.index[-1]) if len(close) else None,
                    "return_1d_pct": _finite_number(
                        (close.iloc[-1] / close.iloc[-2] - 1) * 100 if len(close) >= 2 else None
                    ),
                    "return_1m_pct": _finite_number(
                        (close.iloc[-1] / close.iloc[-22] - 1) * 100 if len(close) >= 22 else None
                    ),
                    "sma_20": _finite_number(close.tail(20).mean() if len(close) >= 20 else None),
                    "sma_50": _finite_number(close.tail(50).mean() if len(close) >= 50 else None),
                    "high_52w": _finite_number(close.max() if len(close) else None),
                    "low_52w": _finite_number(close.min() if len(close) else None),
                    "avg_volume_20": _finite_number(
                        volume.tail(20).mean() if volume is not None and len(volume) else None
                    ),
                }

        except Exception as exc:
            packet["warnings"].append(f"market_data_unavailable:{type(exc).__name__}")

        # The broad yfinance info/news properties can block for minutes when a
        # vendor endpoint is degraded. Use small, explicitly timed endpoints
        # for a responsive Slack conversation instead.
        quote_url = (
            "https://query1.finance.yahoo.com/v7/finance/quote?symbols="
            + urllib.parse.quote(symbol)
        )
        try:
            request = urllib.request.Request(
                quote_url, headers={"User-Agent": "Mozilla/5.0 research-gateway"}
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                quote_payload = json.loads(response.read().decode("utf-8"))
            rows = quote_payload.get("quoteResponse", {}).get("result", [])
            quote = rows[0] if rows else {}
            wanted = {
                "shortName": "company",
                "marketCap": "market_cap",
                "trailingPE": "trailing_pe",
                "forwardPE": "forward_pe",
                "priceToBook": "price_to_book",
                "epsTrailingTwelveMonths": "eps_ttm",
                "epsForward": "eps_forward",
                "dividendYield": "dividend_yield",
            }
            packet["fundamentals"] = {
                target: (
                    _finite_number(quote.get(source))
                    if isinstance(quote.get(source), (int, float))
                    else quote.get(source)
                )
                for source, target in wanted.items()
                if quote.get(source) is not None
            }
        except Exception as exc:
            packet["warnings"].append(f"fundamentals_unavailable:{type(exc).__name__}")

        rss_url = "https://finance.yahoo.com/rss/headline?s=" + urllib.parse.quote(symbol)
        try:
            request = urllib.request.Request(
                rss_url, headers={"User-Agent": "Mozilla/5.0 research-gateway"}
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                root = ET.fromstring(response.read())
            for item in root.findall(".//item")[:5]:
                title = (item.findtext("title") or "").strip()
                url = (item.findtext("link") or "").strip()
                if title:
                    packet["news"].append({"title": title, "url": url})
                    if url:
                        packet["sources"].append(
                            {"name": title, "url": url, "observed_at": observed_at}
                        )
        except Exception as exc:
            packet["warnings"].append(f"news_unavailable:{type(exc).__name__}")

        packet["status"] = "fresh" if packet["price"] else "degraded"
        return packet


@dataclass(frozen=True)
class ResearchRequest:
    team: str
    text: str
    channel_id: str
    thread_ts: str
    user_id: str
    event_id: str


@dataclass
class ConversationResult:
    research_id: str
    team: str
    tickers: list[str]
    messages: list[str]
    event: dict[str, Any]
    outbox_path: Path | None = None


def _json_for_prompt(value: Any, *, limit: int = 7000) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return raw if len(raw) <= limit else raw[:limit] + "\n[TRUNCATED]"


def _message(label: str, body: str) -> str:
    return f"*[{label}]*\n{body.strip()}"


class TeamOrchestrator:
    def __init__(
        self,
        *,
        provider: Any | None = None,
        engine: Any | None = None,
        outbox: "ConversationOutbox | None" = None,
    ) -> None:
        self.provider = provider or ApiFirstResearchProvider()
        self.engine = engine or RuleBasedAgentEngine()
        self.outbox = outbox

    def run(self, request: ResearchRequest) -> ConversationResult:
        if request.team not in TEAM_AGENTS:
            raise ValueError(f"Unknown team: {request.team}")
        tickers = extract_tickers(request.text)
        packets = [self.provider.build(ticker) for ticker in tickers]
        research_id = self._research_id(request)
        safety_note = contains_execution_language(request.text)
        reply_language = "Greek" if re.search(r"[\u0370-\u03ff]", request.text) else "English"
        common = {
            "research_id": research_id,
            "team": request.team,
            "user_question_or_thesis": request.text,
            "research_packets": packets,
            "no_ticker_detected": not tickers,
            "execution_request_detected": safety_note,
        }

        agents = TEAM_AGENTS[request.team]
        lead = TEAM_LEAD[request.team]
        panel_text: str | None = None
        panel_method = getattr(self.engine, "generate_panel", None)
        if callable(panel_method):
            compact_packets = [
                {
                    "ticker": packet.get("ticker"),
                    "observed_at": packet.get("observed_at"),
                    "status": packet.get("status"),
                    "price": packet.get("price"),
                    "fundamentals": packet.get("fundamentals"),
                    "filings": list(packet.get("filings") or [])[:3],
                    "news": list(packet.get("news") or [])[:3],
                    "provider_status": packet.get("provider_status"),
                    "source_priority": packet.get("source_priority"),
                    "warnings": packet.get("warnings"),
                }
                for packet in packets
            ]
            panel_prompt = _json_for_prompt(
                {
                    "research_id": research_id,
                    "team": request.team,
                    "reply_language": reply_language,
                    "user_question_or_thesis": request.text,
                    "research_packets": compact_packets,
                    "execution_request_detected": safety_note,
                }
            )
            panel_text = panel_method(request.team, agents, panel_prompt)
            openings = {"moderated_panel": panel_text}
            challenges = {lead: "Included in the moderated panel transcript."}
            synthesis = panel_text
        else:
            openings: dict[str, str] = {}
            for agent in agents:
                prompt = (
                    "ROUND 1 — independent opening view. Do not defer to the other agents. "
                    "If data is missing, say exactly what is missing.\n\n" + _json_for_prompt(common)
                )
                openings[agent] = self.engine.generate(agent, prompt)

            challenge_prompt = (
                "ROUND 2 — moderate a cross-examination of the opening views. Name the agent whose "
                "claim is challenged, identify the strongest disagreement or hidden assumption, and "
                "state what evidence would resolve it. Preserve distinct agent viewpoints.\n\n"
                + _json_for_prompt({**common, "openings": openings})
            )
            challenges = {lead: self.engine.generate(lead, challenge_prompt)}
            synthesis_prompt = (
                "FINAL ROUND — synthesize the room. Separate facts, inferences, and disagreements. "
                "Return: consensus; unresolved disagreement; what proves the thesis; what kills it; "
                "next research step. Do not give or execute a personalized trade order.\n\n"
                + _json_for_prompt({**common, "openings": openings, "challenges": challenges})
            )
            synthesis = self.engine.generate(lead, synthesis_prompt)

        messages: list[str] = []
        if safety_note:
            messages.append(
                "*Research-only safety boundary*\nΤο αίτημα περιέχει γλώσσα εκτέλεσης. "
                "Οι Agents θα συζητήσουν το σενάριο, αλλά δεν θα υποβάλουν, εγκρίνουν ή αλλάξουν εντολή."
            )
        if not tickers:
            messages.append(
                "*Δεν εντοπίστηκε ticker*\nΗ ομάδα απαντά εννοιολογικά. Για ενημερωμένο packet γράψε "
                "το σύμβολο με κεφαλαία ή `$`, π.χ. `$COST`."
            )
        if panel_text is not None:
            messages.append("*Ομαδική συζήτηση Agents*\n\n" + panel_text)
        else:
            messages.extend(_message(AGENT_PROFILES[a]["label"], openings[a]) for a in agents)
            if challenges:
                messages.append(
                    "*Round 2 — Αντίλογος μεταξύ Agents*\n\n"
                    + _message(AGENT_PROFILES[lead]["label"], challenges[lead])
                )
            messages.append(_message(AGENT_PROFILES[lead]["label"] + " — Final", synthesis))

        event = self._build_event(
            request=request,
            research_id=research_id,
            tickers=tickers,
            packets=packets,
            openings=openings,
            challenges=challenges,
            lead=lead,
            synthesis=synthesis,
            execution_request_detected=safety_note,
        )
        result = ConversationResult(research_id, request.team, tickers, messages, event)
        if self.outbox is not None:
            result.outbox_path = self.outbox.write(event)
        return result

    @staticmethod
    def _research_id(request: ResearchRequest) -> str:
        basis = f"{request.event_id}|{request.channel_id}|{request.thread_ts}|{request.text}"
        suffix = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:10]
        return f"research-{datetime.now(timezone.utc):%Y%m%d}-{suffix}"

    @staticmethod
    def _build_event(**values: Any) -> dict[str, Any]:
        request: ResearchRequest = values["request"]
        packets: list[dict[str, Any]] = values["packets"]
        sources = [source for packet in packets for source in packet.get("sources", [])]
        event: dict[str, Any] = {
            "schema": OUTBOX_SCHEMA,
            "event_type": "research_conversation",
            "conversation_schema": CONVERSATION_SCHEMA,
            "research_id": values["research_id"],
            "created_at": utc_now(),
            "session_date": utc_now()[:10],
            "team": request.team,
            "tickers": values["tickers"],
            "user_id": request.user_id,
            "user_thesis": request.text,
            "slack": {
                "channel_id": request.channel_id,
                "thread_ts": request.thread_ts,
                "event_id": request.event_id,
            },
            "packets": packets,
            "sources": sources,
            "discussion": {
                "openings": values["openings"],
                "challenges": values["challenges"],
                "lead": values["lead"],
                "synthesis": values["synthesis"],
            },
            "execution_request_detected": values["execution_request_detected"],
            "execution_performed": False,
            "routing": {
                "notion_data_source": "collection://220db8b0-d543-460e-925a-177d90809945",
                "notion_control_center": "https://app.notion.com/p/3cd36364d4b281dba141cd768fe67f67?pvs=204",
            },
        }
        basis = json.dumps(event, ensure_ascii=False, sort_keys=True).encode("utf-8")
        event["event_sha256"] = hashlib.sha256(basis).hexdigest()
        return event


class ConversationOutbox:
    """Append-only writer used by the separate Notion projection automation."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, event: dict[str, Any]) -> Path:
        research_id = str(event.get("research_id") or "").strip()
        if event.get("schema") != OUTBOX_SCHEMA or not research_id:
            raise ValueError("Invalid research conversation event")
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.root / f"{research_id}_research_conversation.json"
        encoded = json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True)
        if destination.exists():
            existing = json.loads(destination.read_text(encoding="utf-8"))
            if existing.get("event_sha256") != event.get("event_sha256"):
                raise FileExistsError(f"Immutable conversation conflict: {destination}")
            return destination
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.root, delete=False, suffix=".tmp"
        ) as handle:
            handle.write(encoded)
            temporary = Path(handle.name)
        temporary.replace(destination)
        return destination


def split_slack_message(text: str, *, limit: int = 4500) -> Iterable[str]:
    """Split long messages at paragraphs while staying below Slack's limit."""

    if len(text) <= limit:
        yield text
        return
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = paragraph if not current else current + "\n\n" + paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            yield current
        while len(paragraph) > limit:
            yield paragraph[:limit]
            paragraph = paragraph[limit:]
        current = paragraph
    if current:
        yield current
