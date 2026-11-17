"""Jarvis's document search (owner 2026-11-17; the deep-research idea of OpenJarvis, built for the ecosystem).

The ecosystem's own reports (Markdown under output/, plus RULES_REVIEW and the pipeline overview) are cut into
chunks under their headings and embedded locally with bge-m3 through Ollama (multilingual, Greek included). A question
is answered from the chunks closest in meaning, each cited by file and heading. When the embeddings model is not
there, the search falls back to words. Read-only; broker files (alpaca_*) and anything outside output/ and the listed
docs are never read. The index only re-embeds files that changed, within a time budget.
"""
from __future__ import annotations

from array import array
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Protocol
import urllib.error
import urllib.request

import numpy as np

INDEX = Path("data") / "jarvis" / "search.sqlite3"
MODEL = "bge-m3"
OLLAMA_HOST = "http://127.0.0.1:11434"
MAX_CHARS = 800
MAX_FILE_BYTES = 300_000
RECENT_DAILY = 30           # dated reports kept in the index (Jarvis reports, session reports)
DOCS = ("docs/RULES_REVIEW.md", "PIPELINE_OVERVIEW.md")
SKIP_PREFIXES = ("alpaca_",)
BATCH = 16


class Embedder(Protocol):
    model: str

    def available(self) -> bool: ...
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    """Local Ollama embeddings; never starts Ollama."""

    def __init__(self, model: str = MODEL, host: str = OLLAMA_HOST, timeout: float = 300) -> None:
        self.model, self.host, self.timeout = model, host.rstrip("/"), timeout

    def available(self) -> bool:
        try:
            with urllib.request.urlopen(self.host + "/api/tags", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, ValueError):
            return False
        return any(str(m.get("name", "")).split(":")[0] == self.model for m in payload.get("models") or [])

    def embed(self, texts: list[str]) -> list[list[float]]:
        body = {"model": self.model, "input": texts, "keep_alive": "10m", "options": {"num_thread": 4}}
        request = urllib.request.Request(self.host + "/api/embed", data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return list(json.loads(response.read().decode("utf-8")).get("embeddings") or [])


def _dated(paths: list[Path]) -> list[Path]:
    """Of the dated reports in one folder, only the most recent RECENT_DAILY."""
    dated = sorted([p for p in paths if re.search(r"_\d{4}-?\d{2}-?\d{2}\.md$", p.name)])
    undated = [p for p in paths if p not in set(dated)]
    return undated + dated[-RECENT_DAILY:]


def source_files(root: Path) -> list[Path]:
    output = root / "output"
    files: list[Path] = []
    if output.exists():
        by_folder: dict[Path, list[Path]] = {}
        for path in output.rglob("*.md"):
            if path.name.startswith(SKIP_PREFIXES):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            by_folder.setdefault(path.parent, []).append(path)
        for folder in sorted(by_folder):
            files += _dated(sorted(by_folder[folder]))
    files += [root / rel for rel in DOCS if (root / rel).is_file()]
    return files


def chunk(text: str, max_chars: int = MAX_CHARS) -> list[dict[str, str]]:
    """Paragraphs under their nearest heading, merged up to ``max_chars``; long paragraphs split by words."""
    out: list[dict[str, str]] = []
    heading, buffer = "", ""

    def flush() -> None:
        nonlocal buffer
        if buffer.strip():
            out.append({"heading": heading, "text": buffer.strip()})
        buffer = ""

    paragraphs = re.split(r"\n\s*\n", text or "")
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if para.startswith("#"):
            first, _, rest = para.partition("\n")
            flush()
            heading = first.lstrip("#").strip()
            para = rest.strip()
            if not para:
                continue
        pieces = [para]
        if len(para) > max_chars:
            pieces, current = [], ""
            for word in para.split():
                if len(current) + len(word) + 1 > max_chars:
                    pieces.append(current)
                    current = ""
                current = f"{current} {word}".strip()
            if current:
                pieces.append(current)
        for piece in pieces:
            if len(buffer) + len(piece) + 2 > max_chars:
                flush()
            buffer = f"{buffer}\n\n{piece}" if buffer else piece
    flush()
    return out


def _connect(root: Path) -> sqlite3.Connection:
    path = root / INDEX
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, model TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS chunks (path TEXT NOT NULL, heading TEXT, text TEXT NOT NULL,"
                 " vector BLOB NOT NULL)")
    conn.commit()
    return conn


def _rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def build(root: Path, embedder: Embedder, *, budget_seconds: float = 300) -> dict[str, Any]:
    """Bring the index up to date: embed changed files, drop deleted ones, stop at the time budget."""
    stats: dict[str, Any] = {"model": embedder.model, "embedded_files": 0, "chunks": 0, "stopped_for_time": False,
                             "available": embedder.available()}
    if not stats["available"]:
        return stats
    deadline = time.monotonic() + budget_seconds
    sources = {_rel(p, root): p for p in source_files(root)}
    with closing(_connect(root)) as conn:
        known = {path: (sha, model) for path, sha, model in conn.execute("SELECT path, sha256, model FROM files")}
        for gone in set(known) - set(sources):
            conn.execute("DELETE FROM chunks WHERE path=?", (gone,))
            conn.execute("DELETE FROM files WHERE path=?", (gone,))
        conn.commit()
        for rel, path in sources.items():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if known.get(rel) == (sha, embedder.model):
                continue
            if time.monotonic() >= deadline:
                stats["stopped_for_time"] = True
                break
            pieces = chunk(text)
            vectors: list[list[float]] = []
            for start in range(0, len(pieces), BATCH):
                batch = pieces[start:start + BATCH]
                vectors += embedder.embed([f"{p['heading']}\n{p['text']}" for p in batch])
            conn.execute("DELETE FROM chunks WHERE path=?", (rel,))
            conn.executemany("INSERT INTO chunks VALUES (?,?,?,?)",
                             [(rel, p["heading"], p["text"], array("f", v).tobytes()) for p, v in zip(pieces, vectors)])
            conn.execute("INSERT OR REPLACE INTO files VALUES (?,?,?)", (rel, sha, embedder.model))
            conn.commit()
            stats["embedded_files"] += 1
        stats["chunks"] = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    return stats


def _fold(text: str) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _words(question: str, root: Path, k: int) -> list[dict[str, Any]]:
    terms = {w[:5] for w in re.findall(r"\w+", _fold(question)) if len(w) >= 3}
    scored = []
    for path in source_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for piece in chunk(text):
            folded = _fold(f"{piece['heading']} {piece['text']}")
            score = sum(1 for t in terms if t in folded)
            if score:
                scored.append((score, _rel(path, root), piece))
    scored.sort(key=lambda x: -x[0])
    return [{"path": rel, "heading": p["heading"], "text": p["text"], "score": float(s), "mode": "words"}
            for s, rel, p in scored[:k]]


def search(question: str, root: Path, embedder: Embedder, *, k: int = 4) -> list[dict[str, Any]]:
    path = root / INDEX
    if path.exists() and embedder.available():
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as conn:
            rows = conn.execute("SELECT c.path, c.heading, c.text, c.vector FROM chunks c JOIN files f USING(path)"
                                " WHERE f.model=?", (embedder.model,)).fetchall()
        if rows:
            query = np.asarray(embedder.embed([question])[0], dtype=np.float32)
            matrix = np.vstack([np.frombuffer(r[3], dtype=np.float32) for r in rows])
            norms = np.linalg.norm(matrix, axis=1) * (np.linalg.norm(query) or 1.0)
            scores = matrix @ query / np.where(norms == 0, 1.0, norms)
            best = np.argsort(-scores)[:k]
            return [{"path": rows[i][0], "heading": rows[i][1], "text": rows[i][2], "score": round(float(scores[i]), 4),
                     "mode": "meaning"} for i in best]
    return _words(question, root, k)
