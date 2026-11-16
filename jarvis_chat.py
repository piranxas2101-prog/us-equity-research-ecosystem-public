"""Jarvis chat: plain-language questions answered by the local Ollama model from Jarvis's own files (design 5.3).

The files are picked by fixed rules (the latest report always; a date, a ticker or a check id in the question add
that day's or that check's file), at most three, and only from under ``output/``. The model is told to answer only
from them, to name the file, and to say "δεν το ξέρω" otherwise. Every number in the answer that does not appear in
the sources is flagged. The chat only reads: it has no tools, runs nothing, and never starts Ollama.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Protocol
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
CHAT_DB = ROOT / "data" / "jarvis" / "chat.sqlite3"
CONFIG = ROOT / "jarvis_config.json"
MAX_SOURCES = 3
NUMBER = re.compile(r"\d+(?:[.,]\d+)?%?")
TICKER = re.compile(r"\b[A-Z]{1,5}\b")
DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
LEARNING_WORDS = ("μάθ", "κανόν", "πρόταση", "προτάσ")
SYSTEM = ("Είσαι ο Jarvis, ο βοηθός του οικοσυστήματος. Απαντάς στα ελληνικά, σύντομα, ΜΟΝΟ από τα κείμενα που σου "
          "δίνονται. Γράψε από ποιο αρχείο είναι κάθε στοιχείο. Μην επινοείς αριθμούς, μετοχές ή αποτελέσματα. "
          "Αν τα κείμενα δεν απαντούν, πες «δεν το ξέρω». Δεν αποφασίζεις και δεν προτείνεις συναλλαγές.")


class Client(Protocol):
    def available(self) -> bool: ...
    def generate(self, system: str, prompt: str) -> str: ...


def load_config(path: Path = CONFIG) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class OllamaClient:
    """Talks to an Ollama that is already running on this machine; never starts it."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = config or load_config()
        self.host = str(config["ollama_host"]).rstrip("/")
        self.model = str(config["model"])
        self.timeout = float(config.get("timeout_seconds") or 120)
        self.options = {"temperature": 0, "seed": 0, "num_ctx": int(config.get("num_ctx") or 4096),
                        "num_predict": int(config.get("num_predict") or 400)}

    def available(self) -> bool:
        try:
            with urllib.request.urlopen(self.host + "/api/tags", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, ValueError):
            return False
        return any(str(m.get("name")) == self.model for m in payload.get("models") or [])

    def generate(self, system: str, prompt: str) -> str:
        body = {"model": self.model, "system": system, "prompt": prompt, "stream": False, "options": self.options}
        request = urllib.request.Request(self.host + "/api/generate", data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return str(json.loads(response.read().decode("utf-8")).get("response") or "")


def _check_outputs(root: Path) -> dict[str, str]:
    try:
        checks = json.loads((root / "jarvis_checks.json").read_text(encoding="utf-8-sig")).get("checks") or []
    except (OSError, ValueError):
        return {}
    return {str(c.get("id")): str(c["output"]) for c in checks if c.get("output")}


def select_sources(question: str, root: Path) -> list[Path]:
    output = (root / "output").resolve()
    wanted: list[Path] = [root / "output" / "jarvis" / "report_latest.md"]
    for year, month, day in DATE.findall(question):
        wanted += [root / "output" / "jarvis" / f"report_{year}-{month}-{day}.md",
                   root / "output" / "paper_session" / f"session_report_{year}{month}{day}.md"]
    session_latest = root / "output" / "paper_session" / "session_report_latest.md"
    if session_latest.exists():
        text = session_latest.read_text(encoding="utf-8", errors="replace")
        if any(re.search(rf"\b{t}\b", text) for t in TICKER.findall(question)):
            wanted.append(session_latest)
    for cid, rel in _check_outputs(root).items():
        if cid in question:
            wanted.append(root / rel)
    if any(word in question.lower() for word in LEARNING_WORDS):
        wanted.append(root / "output" / "jarvis" / "report_latest.json")
    out: list[Path] = []
    for path in wanted:
        resolved = path.resolve()
        if resolved.is_file() and output in resolved.parents and resolved not in out:
            out.append(resolved)
        if len(out) == MAX_SOURCES:
            break
    return out


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root.resolve()).as_posix()


def answer(question: str, *, root: Path, client: Client, max_source_chars: int = 6000) -> dict[str, Any]:
    if not client.available():
        return {"answer": "Το Ollama δεν τρέχει (ή δεν έχει το μοντέλο), οπότε δεν μπορώ να απαντήσω τώρα. "
                          "Η αναφορά της ημέρας είναι στη σελίδα.", "sources": [], "unverified_numbers": [],
                "ollama": "down"}
    sources = select_sources(question, root)
    texts = {_rel(p, root): p.read_text(encoding="utf-8", errors="replace")[:max_source_chars] for p in sources}
    prompt = "\n\n".join(f"### Αρχείο: {name}\n{text}" for name, text in texts.items())
    prompt += f"\n\n### Ερώτηση\n{question}"
    try:
        text = client.generate(SYSTEM, prompt).strip()
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return {"answer": f"Το Ollama δεν απάντησε ({type(exc).__name__}).", "sources": list(texts),
                "unverified_numbers": [], "ollama": "down"}
    corpus = "\n".join(texts.values())
    unverified = [n for n in dict.fromkeys(NUMBER.findall(text)) if n not in corpus]
    if unverified:
        text += ("\n\n⚠ Οι αριθμοί " + ", ".join(unverified) + " δεν βρίσκονται στις πηγές: μην τους εμπιστευτείς.")
    return {"answer": text or "δεν το ξέρω", "sources": list(texts), "unverified_numbers": unverified, "ollama": "ok"}


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS chat (at TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL,"
                 " sources_json TEXT NOT NULL, unverified_json TEXT NOT NULL, ollama TEXT NOT NULL)")
    return conn


def save_turn(question: str, result: dict[str, Any], *, path: Path = CHAT_DB) -> None:
    with closing(_connect(path)) as conn:
        conn.execute("INSERT INTO chat VALUES (?,?,?,?,?,?)",
                     (datetime.now(timezone.utc).isoformat(timespec="seconds"), question, result["answer"],
                      json.dumps(result.get("sources") or [], ensure_ascii=False),
                      json.dumps(result.get("unverified_numbers") or [], ensure_ascii=False), result.get("ollama", "")))
        conn.commit()


def history(limit: int = 50, *, path: Path = CHAT_DB) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with closing(_connect(path)) as conn:
        rows = conn.execute("SELECT at, question, answer, sources_json, unverified_json FROM chat"
                            " ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
    return [{"at": a, "question": q, "answer": t, "sources": json.loads(s), "unverified_numbers": json.loads(u)}
            for a, q, t, s, u in reversed(rows)]
