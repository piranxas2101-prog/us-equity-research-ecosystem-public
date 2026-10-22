"""Pre-commit invariant check for the US Equity Research Ecosystem.

Reads a git diff (never runs the pipeline, never touches the network) and reports
violations of the rules in CLAUDE.md as STOP / WARN / REVIEW findings.

    python .claude/skills/invariants/check_invariants.py [--base REF] [--json]

Default scope: working tree + index versus HEAD, plus untracked files.
--base REF compares REF...HEAD instead (e.g. --base origin/main for a branch).
Exit code 1 when any STOP finding exists, otherwise 0. Standard library only.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROTECTED_PATHS = (
    "alpaca_*.py",
    "start_trader_*",
    "resume_trader_*",
    "thymios_order_intent.py",
    "koulis_risk_gate.py",
)
RUNTIME_DIRS = ("data/", "cache/", "logs/", "models/", "output/")
CODE_SUFFIXES = (".py", ".ps1", ".cmd", ".json")
SELF_EXEMPT = (".claude/skills/invariants/", "tests/test_check_invariants.py")

LAUNCHER_RE = re.compile(r"\b(?:start|run|resume)_[A-Za-z0-9_]+\.(?:ps1|cmd)\b")
PROCESS_RE = re.compile(r"\bsubprocess\b|\bPopen\b|\bos\.system\b|\bos\.startfile\b")
HOSTED_LLM_RE = re.compile(
    r"\b(?:import|from)\s+(?:anthropic|openai)\b|api\.anthropic\.com|api\.openai\.com"
    r"|generativelanguage\.googleapis\.com|ANTHROPIC_API_KEY|OPENAI_API_KEY",
    re.IGNORECASE,
)
OLLAMA_APP_RE = re.compile(r"Start-Process[^\n]*ollama|ollama\s+app|ollama app\.exe", re.IGNORECASE)
SILENT_DEFAULT_RE = re.compile(
    r"\.get\([^()]*,\s*0(?:\.0*)?\s*\)|\bor\s+0(?:\.0*)?\b(?!\.\d)|fillna\(\s*0(?:\.0*)?\s*\)"
)
ENV_READ_RE = re.compile(r"""['"]\.env['"]|Get-Content[^\n]*\.env\b""")
THRESHOLD_FILE_RE = re.compile(r"risk|threshold|promotion|gate|limit", re.IGNORECASE)
FROZEN_RE = re.compile(r"frozen|packet_hash|manifest", re.IGNORECASE)
PERSONALITY_RE = re.compile(r"personality", re.IGNORECASE)

REVIEW_ITEMS = (
    "Frozen packets / run manifests are never mutated after freezing.",
    "Personality never changes evidence, scoring, risk limits, authority or execution eligibility.",
    "Learning stays candidate -> shadow -> champion with manual promotion.",
    "Default/full-universe workflows still cannot submit orders.",
    "Unknown stays unknown: missing values are explicit unknowns, not zero or a default.",
)


@dataclass
class Finding:
    level: str  # STOP | WARN | REVIEW
    rule: str
    message: str
    location: str = ""


@dataclass
class FileDiff:
    path: str
    added: list[tuple[int, str]] = field(default_factory=list)
    deleted: bool = False


def run_git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def parse_diff(diff_text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    current: FileDiff | None = None
    new_line = 0
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            match = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            current = FileDiff(match.group(2) if match else line)
            if match:
                current.path = match.group(2)
            files.append(current)
        elif current is None:
            continue
        elif line.startswith("+++ "):
            if line.endswith("/dev/null"):
                current.deleted = True
        elif line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            new_line = int(match.group(1)) if match else 0
        elif line.startswith("+"):
            current.added.append((new_line, line[1:]))
            new_line += 1
    return files


def collect_diff(root: Path, base: str | None) -> list[FileDiff]:
    if base:
        files = parse_diff(run_git(["diff", "--unified=0", f"{base}...HEAD"], root))
    else:
        files = parse_diff(run_git(["diff", "--unified=0", "HEAD"], root))
        for rel in run_git(["ls-files", "--others", "--exclude-standard"], root).splitlines():
            if not rel:
                continue
            path = root / rel
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                text = ""
            files.append(FileDiff(rel, [(n, t) for n, t in enumerate(text.splitlines(), 1)]))
    return files


def is_protected(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, pattern) for pattern in PROTECTED_PATHS)


def check(files: list[FileDiff]) -> list[Finding]:
    findings: list[Finding] = []
    changed_tests = any(f.path.startswith("tests/") for f in files)
    changed_code = [f for f in files if f.path.endswith(".py") and not f.path.startswith("tests/")]

    for f in files:
        if is_protected(f.path):
            findings.append(
                Finding(
                    "STOP",
                    "alpaca-execution",
                    "protected execution/risk file changed; needs explicit owner approval",
                    f.path,
                )
            )
        if f.path == ".env" or (f.path.startswith(".env.") and f.path != ".env.example"):
            findings.append(Finding("STOP", "secrets", "credential file is part of the change", f.path))
        if f.path.startswith(RUNTIME_DIRS):
            findings.append(Finding("STOP", "runtime-state", "generated runtime state must not be committed", f.path))

    for f in files:
        if f.path.startswith(SELF_EXEMPT) or not f.path.endswith(CODE_SUFFIXES):
            continue
        is_test = f.path.startswith("tests/")
        spawns = any(PROCESS_RE.search(text) for _, text in f.added)
        for number, text in f.added:
            where = f"{f.path}:{number}"
            if HOSTED_LLM_RE.search(text):
                findings.append(
                    Finding(
                        "STOP", "hosted-llm", "hosted LLM provider referenced; LLMs run only locally via Ollama", where
                    )
                )
            if OLLAMA_APP_RE.search(text):
                findings.append(Finding("STOP", "ollama-app", "starting the Ollama app is not allowed", where))
            if LAUNCHER_RE.search(text) and f.path.endswith(".py"):
                level = "STOP" if spawns else "WARN"
                findings.append(
                    Finding(
                        level,
                        "long-run-launcher",
                        "start_/run_/resume_ launcher referenced" + (" next to process spawning" if spawns else ""),
                        where,
                    )
                )
            if ENV_READ_RE.search(text) and not is_test:
                findings.append(
                    Finding(
                        "WARN",
                        "env-read",
                        "code reads .env directly; credentials must come from the environment",
                        where,
                    )
                )
            if not is_test and f.path.endswith(".py") and SILENT_DEFAULT_RE.search(text):
                findings.append(
                    Finding(
                        "WARN",
                        "silent-default",
                        "missing value may be silently treated as zero; unknown must stay unknown",
                        where,
                    )
                )
        if not is_test and THRESHOLD_FILE_RE.search(f.path.rsplit("/", 1)[-1]) and f.added:
            note = "tests/ also changed" if changed_tests else "no tests/ change in this diff"
            findings.append(
                Finding(
                    "WARN",
                    "threshold-gate",
                    f"risk/threshold/gate file changed; needs documented rationale and focused test ({note})",
                    f.path,
                )
            )
        if (
            not is_test
            and f.path.endswith(".py")
            and PERSONALITY_RE.search(f.path)
            and any(re.search(r"score|risk|limit|eligib", t, re.IGNORECASE) for _, t in f.added)
        ):
            findings.append(
                Finding(
                    "WARN",
                    "personality-scope",
                    "personality module touches scoring/risk/eligibility terms; confirm it only changes tone",
                    f.path,
                )
            )
        if (
            not is_test
            and f.path.endswith(".py")
            and any(
                FROZEN_RE.search(t) and re.search(r"\[[^\]]+\]\s*=|\.update\(|\bsetdefault\(", t) for _, t in f.added
            )
        ):
            findings.append(
                Finding(
                    "WARN",
                    "frozen-artifact",
                    "mutation near frozen/hash/manifest code; confirm nothing changes after freezing",
                    f.path,
                )
            )

    if changed_code and not changed_tests:
        names = ", ".join(f.path for f in changed_code[:5])
        findings.append(Finding("WARN", "missing-tests", f"code changed without any tests/ change ({names})"))

    findings.extend(Finding("REVIEW", "judgment", item) for item in REVIEW_ITEMS)
    return findings


def render(findings: list[Finding], file_count: int) -> str:
    lines = [f"Invariant check: {file_count} changed file(s)"]
    for level in ("STOP", "WARN", "REVIEW"):
        group = [f for f in findings if f.level == level]
        if not group:
            continue
        lines.append(f"\n{level} ({len(group)})")
        for f in group:
            suffix = f"  [{f.location}]" if f.location else ""
            lines.append(f"  - {f.rule}: {f.message}{suffix}")
    stops = sum(f.level == "STOP" for f in findings)
    warns = sum(f.level == "WARN" for f in findings)
    verdict = "STOP - ask the owner before continuing" if stops else ("PASS with warnings" if warns else "PASS")
    lines.append(f"\nVerdict: {verdict}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", help="compare BASE...HEAD instead of the working tree")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--root", default=".", help="repository root (default: current directory)")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    files = collect_diff(root, args.base)
    findings = check(files)
    if args.json:
        print(json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=2))
    else:
        print(render(findings, len(files)))
    return 1 if any(f.level == "STOP" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
