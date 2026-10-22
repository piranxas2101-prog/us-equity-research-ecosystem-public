"""Build a static local portal for comparing immutable research runs."""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def build(root: Path = ROOT) -> Path:
    runs = []
    for manifest in sorted((root / "output" / "runs").glob("*/manifest.json"), reverse=True):
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
            run_id = str(payload.get("run_id") or manifest.parent.name)
            dashboard = root / "output" / "dashboards" / f"symbol_research_{run_id}.html"
            runs.append({"run_id": run_id, "captured_at": payload.get("captured_at"),
                         "artifacts": len(payload.get("artifacts") or []),
                         "dashboard": dashboard.name if dashboard.exists() else None})
        except (OSError, ValueError):
            continue
    def _row(r: dict) -> str:
        dashboard = r["dashboard"]
        if dashboard:
            link = "<a href=\"" + html.escape(str(dashboard)) + "\">Άνοιγμα</a>"
        else:
            link = "Δεν δημιουργήθηκε"
        return (
            "<tr>"
            f"<td>{html.escape(r['run_id'])}</td>"
            f"<td>{html.escape(str(r['captured_at'] or ''))}</td>"
            f"<td>{r['artifacts']}</td>"
            f"<td>{link}</td>"
            "</tr>"
        )

    rows = "".join(_row(r) for r in runs) or "<tr><td colspan='4'>Δεν υπάρχουν αποθηκευμένα runs.</td></tr>"
    page = f"""<!doctype html><html lang='el'><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>Research Portal</title>
    <style>body{{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#172235}}table{{width:100%;border-collapse:collapse}}th,td{{padding:.7rem;border-bottom:1px solid #d8e0e9;text-align:left}}a{{color:#174ea6}}.note{{background:#eaf1fc;padding:1rem;border-radius:10px}}</style>
    <h1>Τοπικό Research Portal</h1><p>Παραγωγή: {datetime.now(timezone.utc).isoformat()}</p>
    <p class='note'>Στατικά, τοπικά και research-only αποτελέσματα. Δεν απαιτείται server και δεν υπάρχει σύνδεση με broker.</p>
    <p><a href='agent_learning_latest.html'>Agent learning dashboard</a></p>
    <table><tr><th>Run</th><th>Ημερομηνία</th><th>Artifacts</th><th>Dashboard</th></tr>{rows}</table></html>"""
    output = root / "output" / "dashboards" / "index.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")
    return output


if __name__ == "__main__":
    print(build())
