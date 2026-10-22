from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from us_equity_monitor import (
    call_ollama,
    load_dotenv,
    load_json,
    read_prompt,
    resolve_agent_prompt_path,
    to_float,
)


ATHENS_TZ = ZoneInfo("Europe/Athens")


@dataclass
class CyclePaths:
    root: Path
    cycle_dir: Path
    index_path: Path
    manifest_path: Path
    rulebook_path: Path
    latest_cycle_ptr: Path
    latest_manifest_ptr: Path


def now_athens() -> datetime:
    return datetime.now(ATHENS_TZ)


def read_json_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError(f"JSON file is not an object: {path}")
    return parsed


def build_paths(output_root: Path) -> CyclePaths:
    output_root.mkdir(parents=True, exist_ok=True)
    ts = now_athens().strftime("%Y%m%d_%H%M%S")
    cycle_dir = output_root / f"cycle_{ts}"
    cycle_dir.mkdir(parents=True, exist_ok=True)
    return CyclePaths(
        root=output_root,
        cycle_dir=cycle_dir,
        index_path=cycle_dir / "reports_index.md",
        manifest_path=cycle_dir / "reports_manifest.json",
        rulebook_path=output_root / "rulebook.json",
        latest_cycle_ptr=output_root / "latest_cycle.txt",
        latest_manifest_ptr=output_root / "latest_manifest.json",
    )


def tier_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("giannis_tier") or "UNKNOWN")
        counts[key] = counts.get(key, 0) + 1
    return counts


def md_table(rows: list[dict[str, Any]], headers: list[str]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        vals = []
        for h in headers:
            val = row.get(h)
            vals.append(str(val) if val is not None else "")
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def summarize_context(
    babis: dict[str, Any],
    giannis: dict[str, Any],
    focus: dict[str, Any] | None,
) -> str:
    babis_short = list(babis.get("shortlist", []))
    babis_traps = list(babis.get("traps", []))
    giannis_rows = list(giannis.get("results", []))
    giannis_sorted = sorted(
        giannis_rows,
        key=lambda x: to_float(x.get("score_final")) or 0.0,
        reverse=True,
    )
    giannis_worst = sorted(
        giannis_rows,
        key=lambda x: to_float(x.get("score_final")) or 0.0,
    )
    tiers = tier_counts(giannis_rows)

    top10_babis = [
        {
            "rank": row.get("rank"),
            "ticker": row.get("ticker"),
            "catalyst_bucket": row.get("catalyst_bucket"),
            "risk_level": row.get("risk_level"),
            "verdict": row.get("verdict"),
        }
        for row in babis_short[:10]
    ]
    top15_g = [
        {
            "ticker": row.get("ticker"),
            "score_final": row.get("score_final"),
            "giannis_tier": row.get("giannis_tier"),
            "risk_level": row.get("risk_level"),
            "catalyst_bucket": row.get("catalyst_bucket"),
        }
        for row in giannis_sorted[:15]
    ]
    worst15_g = [
        {
            "ticker": row.get("ticker"),
            "score_final": row.get("score_final"),
            "giannis_tier": row.get("giannis_tier"),
            "risk_level": row.get("risk_level"),
            "share_price": row.get("share_price"),
        }
        for row in giannis_worst[:15]
    ]

    lines = [
        "TRAINING CONTEXT (real data snapshot)",
        "",
        f"Babis run timestamp: {babis.get('run_timestamp')}",
        f"Babis shortlist total: {babis.get('shortlist_total')}",
        f"Babis candidate pool: {babis.get('candidates_total')}",
        f"Giannis run timestamp: {giannis.get('run_timestamp')}",
        f"Giannis coverage count: {giannis.get('count')}",
        "",
        "Giannis tier counts:",
    ]
    for k, v in sorted(tiers.items(), key=lambda kv: kv[1], reverse=True):
        lines.append(f"- {k}: {v}")
    lines.extend(
        [
            "",
            "Top 10 from Babis:",
            md_table(top10_babis, ["rank", "ticker", "catalyst_bucket", "risk_level", "verdict"]),
            "",
            "Top 15 from Giannis:",
            md_table(top15_g, ["ticker", "score_final", "giannis_tier", "risk_level", "catalyst_bucket"]),
            "",
            "Worst 15 from Giannis:",
            md_table(worst15_g, ["ticker", "score_final", "giannis_tier", "risk_level", "share_price"]),
            "",
            "Top traps from Babis:",
        ]
    )
    for row in babis_traps[:10]:
        flags = row.get("dilution_flags", [])
        flag_text = ", ".join(flags) if isinstance(flags, list) else str(flags or "none")
        lines.append(
            f"- {row.get('ticker')} | risk={row.get('risk_level')} | verdict={row.get('verdict')} | flags={flag_text}"
        )
    if focus and isinstance(focus, dict):
        symbols = list(focus.get("symbols", []))
        lines.extend(["", f"Focus basket size: {len(symbols)}"])
        preview = symbols[:10]
        if preview:
            lines.append("Focus preview:")
            for row in preview:
                lines.append(f"- {row.get('ticker')} | score={row.get('score_final')} | tier={row.get('giannis_tier')}")
    return "\n".join(lines)


def write_md(path: Path, title: str, body: str, prompt: str | None = None) -> None:
    lines = [f"# {title}", "", f"- Timestamp: {now_athens().isoformat()}", ""]
    if prompt is not None:
        lines.extend(["## Prompt", "", prompt, "", "## Response", "", body])
    else:
        lines.append(body)
    path.write_text("\n".join(lines), encoding="utf-8")


def neutral_system_prompt() -> str:
    return (
        "You are a strict neutral investment process reviewer. "
        "Respond in Greek. Separate facts from interpretation and avoid hype."
    )


def normalize_rule_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def load_rulebook(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 0, "rules": [], "history": []}
    try:
        data = read_json_file(path)
    except Exception:
        return {"version": 0, "rules": [], "history": []}
    if "rules" not in data or not isinstance(data.get("rules"), list):
        data["rules"] = []
    if "history" not in data or not isinstance(data.get("history"), list):
        data["history"] = []
    if "version" not in data or not isinstance(data.get("version"), int):
        data["version"] = 0
    return data


def merge_rule_updates(
    rulebook: dict[str, Any],
    cycle_id: str,
    rule_updates: list[dict[str, Any]],
) -> tuple[dict[str, Any], int]:
    existing = {
        normalize_rule_text(str(row.get("rule_text") or "")): row
        for row in rulebook.get("rules", [])
        if str(row.get("rule_text") or "").strip()
    }
    added = 0
    for upd in rule_updates:
        rule_text = str(upd.get("rule_text") or "").strip()
        if not rule_text:
            continue
        key = normalize_rule_text(rule_text)
        if key in existing:
            continue
        new_row = {
            "rule_text": rule_text,
            "priority": str(upd.get("priority") or "medium"),
            "owner": str(upd.get("owner") or "both"),
            "applies_to": str(upd.get("applies_to") or "both"),
            "rationale": str(upd.get("rationale") or ""),
            "first_seen_cycle": cycle_id,
        }
        rulebook["rules"].append(new_row)
        existing[key] = new_row
        added += 1
    if added > 0:
        rulebook["version"] = int(rulebook.get("version", 0)) + 1
    return rulebook, added


def call_model(
    host: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    num_predict: int,
    timeout_seconds: int,
    output_json: bool = False,
) -> str:
    return call_ollama(
        host=host,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        num_predict=num_predict,
        timeout_seconds=timeout_seconds,
        output_json=output_json,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Self-training cycle for babis + giannis.")
    parser.add_argument("--config", default="scanner_config.json")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--babis-file", default="output/babis_shortlist_latest.json")
    parser.add_argument("--giannis-file", default="output/giannis_rule_based_latest.json")
    parser.add_argument("--focus-file", default="output/focus_basket_latest.json")
    parser.add_argument("--output-root", default="output/self_training")
    parser.add_argument("--meeting-minutes", type=int, default=30)
    parser.add_argument("--host", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--temperature", type=float, default=0.15)
    parser.add_argument("--num-predict", type=int, default=420)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args()

    load_dotenv(Path(args.env_file))
    config = load_json(Path(args.config))
    ollama_cfg = config.get("ollama", {})
    host = str(args.host or ollama_cfg.get("host", "http://127.0.0.1:11434"))
    model = str(args.model or ollama_cfg.get("model", "gemma:7b"))

    babis_path = Path(args.babis_file)
    giannis_path = Path(args.giannis_file)
    if not babis_path.exists():
        raise FileNotFoundError(f"Missing babis file: {babis_path}")
    if not giannis_path.exists():
        raise FileNotFoundError(f"Missing giannis file: {giannis_path}")

    babis = read_json_file(babis_path)
    giannis = read_json_file(giannis_path)
    focus = None
    focus_path = Path(args.focus_file)
    if focus_path.exists():
        try:
            focus = read_json_file(focus_path)
        except Exception:
            focus = None

    cycle_paths = build_paths(Path(args.output_root))
    cycle_id = cycle_paths.cycle_dir.name

    fallback_prompt = str(ollama_cfg.get("agent_prompt_file", "agent_prompt.txt"))
    babis_prompt_path = resolve_agent_prompt_path(config, "agent_2", fallback_prompt)
    giannis_prompt_path = resolve_agent_prompt_path(config, "agent_1", fallback_prompt)
    babis_system = read_prompt(babis_prompt_path, agent_name="babis")
    giannis_system = read_prompt(giannis_prompt_path, agent_name="giannis")
    if not babis_system:
        raise RuntimeError(f"Missing/empty babis prompt: {babis_prompt_path}")
    if not giannis_system:
        raise RuntimeError(f"Missing/empty giannis prompt: {giannis_prompt_path}")

    context = summarize_context(babis=babis, giannis=giannis, focus=focus)

    initial_prompt = (
        "Γράψε στα ελληνικά.\n"
        "Είσαι σε self-training cycle πάνω σε πραγματικά δεδομένα.\n"
        "Δώσε:\n"
        "1) 7 βασικά συμπεράσματα\n"
        "2) 3 λάθη που έκανες ή θα μπορούσες να κάνεις\n"
        "3) 5 κανόνες που πρέπει να τηρούνται αυστηρά στο επόμενο run\n"
        "4) 3 signposts που αλλάζουν investment thesis\n"
        "5) Τι χρειάζεται να μάθεις καλύτερα.\n\n"
        + context
    )

    babis_initial = call_model(
        host=host,
        model=model,
        system_prompt=babis_system,
        user_prompt=initial_prompt,
        temperature=args.temperature,
        num_predict=args.num_predict,
        timeout_seconds=args.timeout_seconds,
    )
    giannis_initial = call_model(
        host=host,
        model=model,
        system_prompt=giannis_system,
        user_prompt=initial_prompt,
        temperature=args.temperature,
        num_predict=args.num_predict,
        timeout_seconds=args.timeout_seconds,
    )

    p1 = cycle_paths.cycle_dir / "01_babis_initial_summary.md"
    p2 = cycle_paths.cycle_dir / "02_giannis_initial_summary.md"
    write_md(p1, "Babis Initial Summary", babis_initial, initial_prompt)
    write_md(p2, "Giannis Initial Summary", giannis_initial, initial_prompt)

    babis_peer_prompt = (
        f"Γράψε στα ελληνικά. Έγινε συζήτηση {args.meeting_minutes} λεπτών.\n"
        "Κάνε peer review στην ανάλυση του Γιαννη.\n"
        "Δώσε: συμφωνίες, διαφωνίες, τι άλλαξες στη σκέψη σου, και 5 βελτιώσεις process.\n\n"
        f"Γιαννης initial:\n{giannis_initial}\n\n"
        f"Babis initial:\n{babis_initial}\n"
    )
    giannis_peer_prompt = (
        f"Γράψε στα ελληνικά. Έγινε συζήτηση {args.meeting_minutes} λεπτών.\n"
        "Κάνε peer review στην ανάλυση του μπαμπη.\n"
        "Δώσε: συμφωνίες, διαφωνίες, τι άλλαξες στη σκέψη σου, και 5 βελτιώσεις process.\n\n"
        f"Babis initial:\n{babis_initial}\n\n"
        f"Giannis initial:\n{giannis_initial}\n"
    )

    babis_peer = call_model(
        host=host,
        model=model,
        system_prompt=babis_system,
        user_prompt=babis_peer_prompt,
        temperature=args.temperature,
        num_predict=args.num_predict,
        timeout_seconds=args.timeout_seconds,
    )
    giannis_peer = call_model(
        host=host,
        model=model,
        system_prompt=giannis_system,
        user_prompt=giannis_peer_prompt,
        temperature=args.temperature,
        num_predict=args.num_predict,
        timeout_seconds=args.timeout_seconds,
    )

    p3 = cycle_paths.cycle_dir / "03_babis_peer_review.md"
    p4 = cycle_paths.cycle_dir / "04_giannis_peer_review.md"
    write_md(p3, "Babis Peer Review", babis_peer, babis_peer_prompt)
    write_md(p4, "Giannis Peer Review", giannis_peer, giannis_peer_prompt)

    joint_prompt = (
        "Γράψε στα ελληνικά.\n"
        "Σύνθεσε κοινό report από τα 4 κείμενα (2 initial + 2 peer).\n"
        "Δομή:\n"
        "1) Τι πίστευε ο καθένας αρχικά\n"
        "2) Τι άλλαξε μετά τη συζήτηση\n"
        "3) Ποιες διαφωνίες παραμένουν\n"
        "4) Κοινές επιχειρησιακές αποφάσεις για το επόμενο run\n"
        "5) 10 συγκεκριμένα lessons learned.\n\n"
        f"Babis initial:\n{babis_initial}\n\n"
        f"Giannis initial:\n{giannis_initial}\n\n"
        f"Babis peer:\n{babis_peer}\n\n"
        f"Giannis peer:\n{giannis_peer}\n"
    )
    joint = call_model(
        host=host,
        model=model,
        system_prompt=neutral_system_prompt(),
        user_prompt=joint_prompt,
        temperature=args.temperature,
        num_predict=args.num_predict,
        timeout_seconds=args.timeout_seconds,
    )
    p5 = cycle_paths.cycle_dir / "05_joint_synthesis.md"
    write_md(p5, "Joint Synthesis", joint, joint_prompt)

    json_prompt = (
        "Return valid JSON only.\n"
        "Schema:\n"
        "{\n"
        '  "run_summary": "string",\n'
        '  "changes": [\n'
        '    {"agent":"babis|giannis|both","before":"string","after":"string","reason":"string"}\n'
        "  ],\n"
        '  "rule_updates": [\n'
        '    {"rule_text":"string","priority":"high|medium|low","owner":"babis|giannis|both","applies_to":"scan|triage|both","rationale":"string"}\n'
        "  ]\n"
        "}\n\n"
        "Use this synthesis text:\n"
        f"{joint}"
    )
    what_changed_json_text = call_model(
        host=host,
        model=model,
        system_prompt=neutral_system_prompt(),
        user_prompt=json_prompt,
        temperature=0.0,
        num_predict=700,
        timeout_seconds=args.timeout_seconds,
        output_json=True,
    )

    parsed_json: dict[str, Any] = {}
    try:
        parsed_json = json.loads(what_changed_json_text)
    except Exception:
        parsed_json = {
            "run_summary": "Failed to parse structured JSON from model output.",
            "changes": [],
            "rule_updates": [],
            "raw_model_output": what_changed_json_text,
        }

    p6_json = cycle_paths.cycle_dir / "06_what_changed.json"
    p6_json.write_text(json.dumps(parsed_json, ensure_ascii=False, indent=2), encoding="utf-8")

    changes = parsed_json.get("changes", [])
    rule_updates = parsed_json.get("rule_updates", [])
    if not isinstance(changes, list):
        changes = []
    if not isinstance(rule_updates, list):
        rule_updates = []

    lines = [
        "# What Changed Report",
        "",
        f"- Timestamp: {now_athens().isoformat()}",
        f"- Cycle: {cycle_id}",
        "",
        "## Run Summary",
        "",
        str(parsed_json.get("run_summary", "")),
        "",
        "## Changes",
        "",
    ]
    if changes:
        for idx, row in enumerate(changes, start=1):
            lines.append(
                f"{idx}. agent={row.get('agent')} | before={row.get('before')} | after={row.get('after')} | reason={row.get('reason')}"
            )
    else:
        lines.append("- No structured changes returned.")
    lines.extend(["", "## Proposed Rule Updates", ""])
    if rule_updates:
        for idx, row in enumerate(rule_updates, start=1):
            lines.append(
                f"{idx}. [{row.get('priority')}] {row.get('rule_text')} | owner={row.get('owner')} | scope={row.get('applies_to')}"
            )
            lines.append(f"   rationale: {row.get('rationale')}")
    else:
        lines.append("- No rule updates returned.")
    p6_md = cycle_paths.cycle_dir / "06_what_changed.md"
    p6_md.write_text("\n".join(lines), encoding="utf-8")

    rulebook = load_rulebook(cycle_paths.rulebook_path)
    rulebook, new_rules = merge_rule_updates(
        rulebook=rulebook,
        cycle_id=cycle_id,
        rule_updates=[row for row in rule_updates if isinstance(row, dict)],
    )
    rulebook["history"].append(
        {
            "cycle_id": cycle_id,
            "timestamp": now_athens().isoformat(),
            "changes_count": len(changes),
            "rule_updates_proposed": len(rule_updates),
            "new_rules_added": new_rules,
            "run_summary": str(parsed_json.get("run_summary", "")),
        }
    )
    cycle_paths.rulebook_path.write_text(json.dumps(rulebook, ensure_ascii=False, indent=2), encoding="utf-8")

    index_lines = [
        "# Self-Training Cycle Reports",
        "",
        f"- Cycle: {cycle_id}",
        f"- Generated: {now_athens().isoformat()}",
        f"- Rulebook version: {rulebook.get('version')}",
        f"- New rules added: {new_rules}",
        "",
        "1. [01_babis_initial_summary.md](./01_babis_initial_summary.md)",
        "2. [02_giannis_initial_summary.md](./02_giannis_initial_summary.md)",
        "3. [03_babis_peer_review.md](./03_babis_peer_review.md)",
        "4. [04_giannis_peer_review.md](./04_giannis_peer_review.md)",
        "5. [05_joint_synthesis.md](./05_joint_synthesis.md)",
        "6. [06_what_changed.md](./06_what_changed.md)",
        "7. [06_what_changed.json](./06_what_changed.json)",
        "",
        f"- Rulebook: {cycle_paths.rulebook_path}",
    ]
    cycle_paths.index_path.write_text("\n".join(index_lines), encoding="utf-8")

    manifest = {
        "generated_at": now_athens().isoformat(),
        "cycle_id": cycle_id,
        "cycle_dir": str(cycle_paths.cycle_dir),
        "inputs": {
            "babis_file": str(babis_path),
            "giannis_file": str(giannis_path),
            "focus_file": str(focus_path) if focus_path.exists() else None,
        },
        "model": model,
        "host": host,
        "reports": [
            str(p1),
            str(p2),
            str(p3),
            str(p4),
            str(p5),
            str(p6_md),
            str(p6_json),
            str(cycle_paths.index_path),
        ],
        "rulebook_path": str(cycle_paths.rulebook_path),
        "rulebook_version": rulebook.get("version"),
        "new_rules_added": new_rules,
    }
    cycle_paths.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    cycle_paths.latest_cycle_ptr.write_text(str(cycle_paths.cycle_dir), encoding="utf-8")
    cycle_paths.latest_manifest_ptr.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[SELF-TRAINING] cycle_dir={cycle_paths.cycle_dir}")
    print(f"[SELF-TRAINING] index={cycle_paths.index_path}")
    print(f"[SELF-TRAINING] manifest={cycle_paths.manifest_path}")
    print(f"[SELF-TRAINING] rulebook={cycle_paths.rulebook_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

