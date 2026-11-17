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
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
from typing import Any, Callable, Protocol
import unicodedata
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
CHAT_DB = ROOT / "data" / "jarvis" / "chat.sqlite3"
CONFIG = ROOT / "jarvis_config.json"
KNOWLEDGE = "jarvis_knowledge.json"
# owner 2026-11-16: Jarvis starts the Ollama server itself, only when the owner asks a question on its page
OLLAMA_EXE = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
OLLAMA_LISTEN = "127.0.0.1:11434"
START_WAIT_SECONDS = 30
NO_WINDOW_FLAGS = 0x00000200 | 0x08000000  # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
READABLE_ROOTS = ("output", "docs")
MAX_SOURCES = 3
SEARCH_HITS = 3
PASSAGE_CHARS = 600
NUMBER = re.compile(r"\d+(?:[.,]\d+)?%?")
TICKER = re.compile(r"\b[A-Z]{1,5}\b")
DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
LEARNING_WORDS = ("μάθ", "κανόν", "πρόταση", "προτάσ")
SYSTEM = ("Είσαι ο Jarvis, ο βοηθός του οικοσυστήματος. Απαντάς στα ελληνικά, σύντομα, ΜΟΝΟ από τα κείμενα που σου "
          "δίνονται. Γράψε από ποιο αρχείο είναι κάθε στοιχείο. Μην επινοείς αριθμούς, μετοχές ή αποτελέσματα. "
          "Αν τα κείμενα δεν απαντούν, πες «δεν το ξέρω». Δεν αποφασίζεις και δεν προτείνεις συναλλαγές. "
          "Γράψε το πολύ τρεις σύντομες προτάσεις. Πρόσεξε την κατεύθυνση: πάνω ή κάτω, καλύτερα ή χειρότερα, "
          "όπως ακριβώς τη γράφει το κείμενο.")


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
        self.timeout = float(config.get("timeout_seconds") or 300)
        self.keep_alive = str(config.get("keep_alive") or "30m")  # stay loaded between questions
        self.options = {"temperature": 0, "seed": 0, "num_ctx": int(config.get("num_ctx") or 4096),
                        "num_predict": int(config.get("num_predict") or 250)}
        if config.get("num_thread"):
            self.options["num_thread"] = int(config["num_thread"])

    def available(self) -> bool:
        try:
            with urllib.request.urlopen(self.host + "/api/tags", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, ValueError):
            return False
        return any(str(m.get("name")) == self.model for m in payload.get("models") or [])

    def generate(self, system: str, prompt: str) -> str:
        body = {"model": self.model, "system": system, "prompt": prompt, "stream": False, "options": self.options,
                "keep_alive": self.keep_alive}
        request = urllib.request.Request(self.host + "/api/generate", data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return str(json.loads(response.read().decode("utf-8")).get("response") or "")


def start_ollama_server(*, exe: Path = OLLAMA_EXE, log_dir: Path = ROOT / "logs") -> bool:
    """Start ``ollama serve`` hidden, listening on this computer only (not the Ollama app). False when Ollama is not
    installed. Called only from the Jarvis page when the owner asks a question and Ollama is not running."""
    if not exe.is_file():
        return False
    log_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "OLLAMA_HOST": OLLAMA_LISTEN}
    with (log_dir / "ollama_serve.log").open("ab") as log:
        subprocess.Popen([str(exe), "serve"], env=env, creationflags=NO_WINDOW_FLAGS, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    return True


def _check_outputs(root: Path) -> dict[str, str]:
    try:
        checks = json.loads((root / "jarvis_checks.json").read_text(encoding="utf-8-sig")).get("checks") or []
    except (OSError, ValueError):
        return {}
    return {str(c.get("id")): str(c["output"]) for c in checks if c.get("output")}


def fold(text: str) -> str:
    """Lower case without accents, so "Αγορά", "ΑΓΟΡΆ" and "αγορα" match the same keyword."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def knowledge_topics(root: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads((root / KNOWLEDGE).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    return [t for t in payload.get("topics") or [] if isinstance(t, dict)]


def _topic_files(question: str, root: Path) -> list[Path]:
    """The knowledge map's files for the topics the question names, the most-named topic first."""
    folded = fold(question)
    scored = []
    for order, topic in enumerate(knowledge_topics(root)):
        hits = sum(1 for word in topic.get("keywords") or [] if fold(str(word)) in folded)
        if hits:
            scored.append((-hits, order, topic))
    return [root / str(rel) for _, _, topic in sorted(scored, key=lambda x: x[:2]) for rel in topic.get("files") or []]


def _readable(path: Path, root: Path) -> bool:
    allowed = [(root / name).resolve() for name in READABLE_ROOTS]
    return path.is_file() and any(base in path.parents for base in allowed)


def select_sources(question: str, root: Path) -> list[Path]:
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
    wanted += _topic_files(question, root)
    out: list[Path] = []
    for path in wanted:
        resolved = path.resolve()
        if _readable(resolved, root) and resolved not in out:
            out.append(resolved)
        if len(out) == MAX_SOURCES:
            break
    return out


def _terms(question: str) -> set[str]:
    """Word stems of the question (five letters, no accents), so "αγορά" also finds "αγοράς"."""
    return {word[:5] for word in re.findall(r"\w+", fold(question)) if len(word) >= 4}


def excerpt(text: str, question: str, budget: int) -> str:
    """The lines of ``text`` that concern the question, each under its heading, within ``budget`` characters; the
    start of the text when no line does. The owner's CPU reads ~28 tokens/s (2026-11-16), so only this goes in."""
    terms = _terms(question)
    kept: list[str] = []
    heading: str | None = None
    for line in text.splitlines():
        folded = fold(line)
        if line.startswith("#"):
            heading = line
        if not terms or not any(term in folded for term in terms):
            continue
        if heading is not None and heading != line and heading not in kept:
            kept.append(heading)
        kept.append(line)
    if not kept:
        return text[:budget]
    out = ""
    for line in kept:
        if len(out) + len(line) + 1 > budget:
            room = budget - len(out)
            if room > 40:
                out += line[:room - 1] + "\n"
            break
        out += line + "\n"
    return out[:budget]


def whole_sentences(text: str) -> str:
    """Drop a last sentence the token limit cut off (2026-11-16: an answer ended "..., με"); kept as is when the text
    has no sentence end at all."""
    text = text.strip()
    if not text or text[-1] in ".;!?»)":
        return text
    end = max(text.rfind(mark) for mark in (". ", "; ", "! ", "? ", ".\n"))
    return text[:end + 1] if end > 0 else text


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root.resolve()).as_posix()


def _answer(question: str, *, root: Path, client: Client, max_total_chars: int = 1800,
           start_ollama: Callable[[], bool] | None = None, wait_seconds: float = START_WAIT_SECONDS,
           searcher: Callable[[str], list[dict[str, Any]]] | None = None,
           poll: Callable[[], None] = lambda: time.sleep(1)) -> dict[str, Any]:
    import jarvis_facts

    facts = jarvis_facts.answer_facts(question, root)  # numbers come from the files, not the model (owner 2026-11-16)
    if facts is not None:
        return facts
    started = False
    ready = client.available()
    if not ready and start_ollama is not None:
        started = bool(start_ollama())
        deadline = time.monotonic() + wait_seconds
        while started and not ready and time.monotonic() < deadline:
            poll()
            ready = client.available()
    if not ready:
        text = ("Ξεκίνησα το Ollama, αλλά δεν απάντησε ακόμα (ή δεν έχει το μοντέλο). Ξαναρώτα σε λίγο."
                if started else "Το Ollama δεν τρέχει (ή δεν έχει το μοντέλο), οπότε δεν μπορώ να απαντήσω τώρα. "
                                "Η αναφορά της ημέρας είναι στη σελίδα.")
        return {"answer": text, "sources": [], "unverified_numbers": [], "ollama": "down", "ollama_started": started}
    sources = select_sources(question, root)
    hits: list[dict[str, Any]] = []
    if searcher is not None:  # the chunks closest in meaning (jarvis_search, owner 2026-11-17)
        try:
            hits = list(searcher(question))[:SEARCH_HITS]
        except Exception:  # noqa: BLE001 - the search is a help; the answer goes on without it
            hits = []
    share = max_total_chars // max(1, len(sources) + len(hits))  # the whole prompt must fit the model's context
    texts = {_rel(p, root): excerpt(p.read_text(encoding="utf-8", errors="replace"), question, share) for p in sources}
    for hit in hits:
        texts.setdefault(f"{hit['path']} — {hit.get('heading') or ''}".rstrip(" —"), str(hit["text"])[:share])
    prompt = "\n\n".join(f"### Αρχείο: {name}\n{text}" for name, text in texts.items())
    prompt += f"\n\n### Ερώτηση\n{question}"
    try:
        text = whole_sentences(client.generate(SYSTEM, prompt))
    except (OSError, urllib.error.URLError, ValueError) as exc:
        timed_out = isinstance(exc, TimeoutError) or "timed out" in str(exc).lower()
        return {"answer": f"Το Ollama δεν απάντησε ({type(exc).__name__}).", "sources": list(texts), "timed_out": timed_out,
                "unverified_numbers": [], "ollama": "down", "ollama_started": started}
    corpus = "\n".join(texts.values())
    unverified = [n for n in dict.fromkeys(NUMBER.findall(text)) if n not in corpus]
    if unverified:
        text += ("\n\n⚠ Οι αριθμοί " + ", ".join(unverified) + " δεν βρίσκονται στις πηγές: μην τους εμπιστευτείς.")
    return {"answer": text or "δεν το ξέρω", "sources": list(texts), "unverified_numbers": unverified, "ollama": "ok",
            "ollama_started": started,
            # the found passages, verbatim (2026-11-17: the small model can garble what the search found)
            "passages": [{"path": h["path"], "heading": h.get("heading") or "", "text": str(h["text"])[:PASSAGE_CHARS]}
                         for h in hits]}


def answer(question: str, *, root: Path, client: Client, **options: Any) -> dict[str, Any]:
    """The answer, timed: ``seconds`` and ``outcome`` (ok / timeout / down) feed the AUTO tuning of the chat's
    settings (jarvis_tiers, owner 2026-11-17)."""
    started = time.monotonic()
    result = _answer(question, root=root, client=client, **options)
    result["seconds"] = round(time.monotonic() - started, 2)
    result["outcome"] = ("timeout" if result.pop("timed_out", False) else
                         "down" if result.get("ollama") == "down" else "ok")
    return result


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS chat (at TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL,"
                 " sources_json TEXT NOT NULL, unverified_json TEXT NOT NULL, ollama TEXT NOT NULL,"
                 " mode TEXT NOT NULL DEFAULT 'model')")
    have = {row[1] for row in conn.execute("PRAGMA table_info(chat)")}
    for column, kind in (("mode", "TEXT NOT NULL DEFAULT 'model'"), ("seconds", "REAL"), ("outcome", "TEXT"),
                         ("passages_json", "TEXT")):
        if column not in have:  # a table from an older version
            conn.execute(f"ALTER TABLE chat ADD COLUMN {column} {kind}")
    conn.commit()
    return conn


def save_turn(question: str, result: dict[str, Any], *, path: Path = CHAT_DB) -> None:
    with closing(_connect(path)) as conn:
        conn.execute("INSERT INTO chat (at, question, answer, sources_json, unverified_json, ollama, mode, seconds,"
                     " outcome, passages_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (datetime.now(timezone.utc).isoformat(timespec="seconds"), question, result["answer"],
                      json.dumps(result.get("sources") or [], ensure_ascii=False),
                      json.dumps(result.get("unverified_numbers") or [], ensure_ascii=False), result.get("ollama", ""),
                      result.get("mode") or "model", result.get("seconds"), result.get("outcome"),
                      json.dumps(result.get("passages") or [], ensure_ascii=False)))
        conn.commit()


def history(limit: int = 50, *, path: Path = CHAT_DB) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with closing(_connect(path)) as conn:
        rows = conn.execute("SELECT at, question, answer, sources_json, unverified_json, mode, passages_json FROM chat"
                            " ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
    return [{"at": a, "question": q, "answer": t, "sources": json.loads(s), "unverified_numbers": json.loads(u),
             "mode": m, "passages": json.loads(pj or "[]")} for a, q, t, s, u, m, pj in reversed(rows)]
