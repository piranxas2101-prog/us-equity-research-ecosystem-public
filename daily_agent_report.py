"""Daily report: what each agent did in a run, and why, in Greek.

Built only from the run's archived, hash-checked artifacts (output/runs/<run_id>/)
and the pipeline's own step timings: no model, no network, no new judgement.
Every sentence restates a decision and the reasons the agent itself recorded; a
value the artifacts do not carry is written as "άγνωστο", never guessed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import macro_environment

ROOT = Path(__file__).resolve().parent
TOP = 10
UNKNOWN = "άγνωστο"
MEMBERSHIP_REASON_GR = {
    "below_exit_composite": "έπεσε κάτω από το όριο εξόδου",
    "below_min_composite": "κάτω από το όριο εισόδου",
    "price_above_max": "τιμή πάνω από το μέγιστο",
    "missing_price": "λείπει η τιμή",
    "below_min_fundamental_coverage": "ανεπαρκής κάλυψη θεμελιωδών",
    "insufficient_history": "ανεπαρκές ιστορικό τιμών",
    "no_provider_coverage": "χωρίς κάλυψη από πάροχο",
    "not_company_route": "δεν είναι πια εταιρεία",
    "deferred_route": "μεταφέρθηκε σε άλλη διαδρομή",
}
LANE_GR = {"etf_lane": "ETF/funds", "spac_lane": "SPAC", "issuer_card": "τίτλοι εκδότη"}
STEP_GR = {
    "sec_prefetch": "SEC companyfacts για όλες τις εταιρείες", "price_history": "Τιμές shortlist στην αποθήκη", "macro_environment": "Περιβάλλον οικονομίας και αγοράς", "babis_refresh": "Babis · σάρωση συμβόλων", "giannis_triage": "Giannis · πρώτη διαλογή",
    "information_ledger": "Καταγραφή πληροφοριών",
    "control_plane_batch": "Προφίλ, τομεακοί agents, οικονομικά SEC, ανάλυση",
    "petros_synthesis": "Petros · σύνθεση", "deep_research": "Βαθιά έρευνα εταιρειών",
    "freeze_packet": "Πάγωμα Research Packet", "yankos": "Yankos · βραχυπρόθεσμα", "periklis": "Periklis · swing",
    "lock_predictions": "Κλείδωμα προβλέψεων", "outcome_baseline": "Βάση αποτελεσμάτων",
    "outcome_refresh": "Ενημέρωση αποτελεσμάτων", "backtest": "Backtest στρατηγικών",
    "sokratis": "Sokratis · μαθήματα traders", "petros_training": "Petros · εκπαίδευση μοντέλου",
    "meeting_report": "Αναφορά συνάντησης", "action_report": "Αναφορά ενεργειών",
    "trader_handoff": "Έλεγχος παράδοσης σε traders", "coordination_audit": "Συντονισμός και έλεγχος agents",
}


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _rows(payload: dict[str, Any], key: str = "results") -> list[dict[str, Any]]:
    return [row for row in payload.get(key) or [] if isinstance(row, dict)]


def _v(value: Any, digits: int = 2) -> str:
    if value is None or value == "":
        return UNKNOWN
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _duration(seconds: Any) -> str:
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return UNKNOWN
    if seconds < 90:
        return f"{seconds:.0f}″"
    if seconds < 5400:
        return f"{seconds / 60:.0f}′"
    return f"{seconds / 3600:.1f} ώρες"


def _counts(rows: list[dict[str, Any]], key: str) -> str:
    counts = Counter(str(row.get(key) or UNKNOWN) for row in rows)
    return ", ".join(f"{name}: {count}" for name, count in counts.most_common()) or "—"


def _rule_reasons(row: dict[str, Any]) -> str:
    details = [d for d in row.get("fundamental_rule_details") or [] if isinstance(d, dict)]
    passed = [d.get("metric") for d in details if d.get("status") == "passed"]
    failed = [d.get("metric") for d in details if d.get("status") == "failed"]
    missing = [d.get("metric") for d in details if d.get("status") not in ("passed", "failed")]
    parts = []
    if passed:
        parts.append("πέρασαν " + ", ".join(map(str, passed)))
    if failed:
        parts.append("δεν πέρασαν " + ", ".join(map(str, failed)))
    if missing:
        parts.append("χωρίς στοιχείο " + ", ".join(map(str, missing)))
    return "; ".join(parts) or "χωρίς καταγεγραμμένους κανόνες"


def _previous_run(run_dir: Path) -> Path | None:
    """The latest earlier full-research run that has archived artifacts."""
    runs = run_dir.parent
    mine = _load(run_dir / "manifest.json").get("captured_at") or ""
    earlier = []
    for manifest in runs.glob("full_research_*/manifest.json"):
        if manifest.parent == run_dir:
            continue
        captured = _load(manifest).get("captured_at") or ""
        if captured and (not mine or captured < mine) and (manifest.parent / "artifacts").is_dir():
            earlier.append((captured, manifest.parent))
    return max(earlier)[1] if earlier else None


def _by_ticker(payload: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    return {str(row.get("ticker")): row for row in _rows(payload, key) if row.get("ticker")}


def _flipped_rules(old: dict[str, Any], new: dict[str, Any]) -> str:
    def status(row: dict[str, Any]) -> dict[str, Any]:
        return {d.get("metric"): d.get("status") for d in row.get("fundamental_rule_details") or [] if isinstance(d, dict)}
    before, after = status(old), status(new)
    changed = [f"{metric}: {before.get(metric, UNKNOWN)} -> {after.get(metric, UNKNOWN)}"
               for metric in after if before.get(metric) != after.get(metric)]
    if changed:
        return "; ".join(changed[:4])

    def observed(row: dict[str, Any]) -> dict[str, Any]:
        return {d.get("metric"): d.get("observed") for d in row.get("fundamental_rule_details") or [] if isinstance(d, dict)}
    seen_before, seen_after = observed(old), observed(new)
    moved = []
    for metric, value in seen_after.items():
        prior = seen_before.get(metric)
        if isinstance(value, (int, float)) and isinstance(prior, (int, float)) and prior and abs(value - prior) / abs(prior) >= 0.1:
            moved.append(f"{metric} {prior:.3g} -> {value:.3g}")
    if moved:
        return "οι κανόνες έμειναν ίδιοι· άλλαξαν οι τιμές: " + "; ".join(moved[:4])
    return "οι κανόνες και οι τιμές τους έμειναν ίδιοι· " + _score_components(old, new)


SCORE_PARTS = (("score_base", "βάση"), ("score_liquidity", "ρευστότητα"), ("score_catalyst", "καταλύτης"),
               ("score_smart_money", "smart money"), ("score_penalty", "ποινή ρίσκου"))


def _score_components(old: dict[str, Any], new: dict[str, Any]) -> str:
    """Which parts of the Giannis score moved, with the reason when the rows carry one."""
    parts = []
    for key, label in SCORE_PARTS:
        before, after = old.get(key), new.get(key)
        if isinstance(before, (int, float)) and isinstance(after, (int, float)) and abs(after - before) >= 0.05:
            parts.append(f"{label} {after - before:+.2f}")
    if not parts:
        return "άλλαξε άλλος παράγοντας του βαθμού"
    reasons = []
    if old.get("catalyst_category_id") != new.get("catalyst_category_id"):
        reasons.append(f"καταλύτης {old.get('catalyst_category_id') or UNKNOWN} -> {new.get('catalyst_category_id') or UNKNOWN}")
    fresh = new.get("fundamentals_fresh")
    if fresh is False and old.get("fundamentals_fresh") is not False:
        reasons.append(f"παλιό περιοδικό report ({_v(new.get('periodic_filing_age_days'), 0)} ημέρες)")
    elif fresh is not False and old.get("fundamentals_fresh") is False:
        reasons.append("το περιοδικό report έγινε φρέσκο")
    if old.get("fresh_primary_data") != new.get("fresh_primary_data"):
        reasons.append("φρέσκο filing τις τελευταίες 30 ημέρες: "
                       f"{'ναι' if new.get('fresh_primary_data') else 'όχι'}")
    return "άλλαξαν: " + ", ".join(parts) + (f" ({'; '.join(reasons)})" if reasons else "")


def _changes_section(run_dir: Path, previous: Path, routes: dict[str, dict[str, Any]]) -> list[str]:
    old_art, new_art = previous / "artifacts", run_dir / "artifacts"
    lines = [f"## Αλλαγές από το προηγούμενο run ({previous.name})", ""]
    old_b = _by_ticker(_load(old_art / "babis_shortlist_latest.json"), "shortlist")
    new_b = _by_ticker(_load(new_art / "babis_shortlist_latest.json"), "shortlist")
    added, removed = sorted(set(new_b) - set(old_b)), sorted(set(old_b) - set(new_b))
    lines.append(f"- Shortlist Babis: {len(old_b)} -> {len(new_b)} · μπήκαν {len(added)}, βγήκαν {len(removed)}.")
    for ticker in added[:6]:
        lines.append(f"  - μπήκε **{ticker}**: βαθμός {_v(new_b[ticker].get('composite_score'))}, "
                     f"καταλύτης {_v(new_b[ticker].get('catalyst_bucket'))}")
    member_reasons = {item.get("ticker"): item.get("reason") for item in
                      ((_load(new_art / "babis_shortlist_latest.json").get("membership") or {}).get("removed") or [])
                      if isinstance(item, dict)}
    for ticker in removed[:6]:
        route = routes.get(ticker) or {}
        why = (f"βγήκε από το μέλος του shortlist: {MEMBERSHIP_REASON_GR.get(member_reasons[ticker], member_reasons[ticker])}"
               if member_reasons.get(ticker) else
               f"τώρα στην κατηγορία {LANE_GR.get(route.get('separate_lane'), route.get('separate_lane'))}"
               if route.get("separate_lane") else
               f"δεν είναι πια εταιρεία (διαδρομή {route.get('research_route')})" if route and not route.get("company_fundamental_eligible")
               else "δεν πέρασε πια τα φίλτρα του Babis")
        lines.append(f"  - βγήκε **{ticker}**: {why}")

    old_g, new_g = _by_ticker(_load(old_art / "giannis_rule_based_latest.json"), "results"),         _by_ticker(_load(new_art / "giannis_rule_based_latest.json"), "results")
    moves = Counter((old_g[t].get("giannis_tier"), new_g[t].get("giannis_tier")) for t in set(old_g) & set(new_g)
                    if old_g[t].get("giannis_tier") != new_g[t].get("giannis_tier"))
    lines.append(f"- Giannis: {sum(moves.values())} εταιρείες άλλαξαν κατηγορία"
                 + (": " + ", ".join(f"{a} -> {b}: {n}" for (a, b), n in moves.most_common(5)) if moves else "") + ".")
    deltas = sorted(((new_g[t].get("score_final") or 0) - (old_g[t].get("score_final") or 0), t)
                    for t in set(old_g) & set(new_g))
    for delta, ticker in (deltas[:3] + deltas[-3:] if len(deltas) > 6 else deltas):
        if abs(delta) >= 0.3:
            lines.append(f"  - **{ticker}** {delta:+.2f} ({_v(old_g[ticker].get('score_final'))} -> "
                         f"{_v(new_g[ticker].get('score_final'))}): {_flipped_rules(old_g[ticker], new_g[ticker])}")

    old_p, new_p = _by_ticker(_load(old_art / "petros_review_latest.json"), "results"),         _by_ticker(_load(new_art / "petros_review_latest.json"), "results")
    pm = Counter((old_p[t].get("petros_verdict"), new_p[t].get("petros_verdict")) for t in set(old_p) & set(new_p)
                 if old_p[t].get("petros_verdict") != new_p[t].get("petros_verdict"))
    lines.append(f"- Petros: {sum(pm.values())} αποφάσεις άλλαξαν"
                 + (": " + ", ".join(f"{a} -> {b}: {n}" for (a, b), n in pm.most_common(5)) if pm else "") + ".")
    for name, key in (("Yankos", "yankos"), ("Periklis", "periklis")):
        # A symbol has several playbook rows: it is a candidate if any of them is.
        old_t = {str(r.get("ticker")) for r in _rows(_load(old_art / f"{key}_strategy_latest.json"))
                 if r.get("post_gate_verdict") == "candidate"}
        new_t = {str(r.get("ticker")) for r in _rows(_load(new_art / f"{key}_strategy_latest.json"))
                 if r.get("post_gate_verdict") == "candidate"}
        lines.append(f"- {name}: υποψήφιοι {len(old_t)} -> {len(new_t)} · νέοι: "
                     f"{', '.join(sorted(new_t - old_t)[:8]) or '—'} · έφυγαν: {', '.join(sorted(old_t - new_t)[:8]) or '—'}.")
    lines.append("")
    return lines


TIER_GR = {"complete": "πλήρη", "adequate": "επαρκή", "partial": "μερικά", "insufficient": "ανεπαρκή"}
KNOWN_OPEN = ("τα περισσότερα πακέτα τομέα είναι ακόμη ανεπαρκή ή μερικά (κάλυψη πλαισίου κάτω από 50%)",
              "εταιρείες με κατηγορία μόνο σε επίπεδο τομέα μένουν χωρίς τομεακό agent")


def _attention_section(run_dir: Path, previous: Path | None, state: dict[str, Any],
                       outcome_evaluation: dict[str, Any] | None = None) -> list[str]:
    """The few things that need the owner today; the long-known open gaps are one line, not a reason to look."""
    items: list[str] = []
    status = state.get("status")
    if status and status not in {"completed", "completed_review_required"}:
        items.append(f"Το run δεν ολοκληρώθηκε κανονικά (κατάσταση {status}).")
    if state.get("technical_errors"):
        items.append(f"Τεχνικά σφάλματα: {state['technical_errors']}.")
    art = run_dir / "artifacts"
    counts = _load(art / "fundamental_control_plane_batch_latest.json").get("counts") or {}
    old_counts = _load(previous / "artifacts" / "fundamental_control_plane_batch_latest.json").get("counts") or {} if previous else {}
    if counts.get("failed"):
        items.append(f"Η ανάλυση εταιρειών απέτυχε για {counts['failed']} εταιρείες.")
    for key, label in (("blocked", "μπλοκαρισμένα οικονομικά SEC"), ("identity_gap", "εταιρείες χωρίς ταυτότητα SEC"),
                       ("sector_context_unroutable", "εταιρείες χωρίς τομέα")):
        now, before = counts.get(key), old_counts.get(key)
        if isinstance(now, int) and isinstance(before, int) and now - before >= max(5, before // 10):
            items.append(f"Αύξηση: {label} {before} -> {now}.")
    new_p = _by_ticker(_load(art / "petros_review_latest.json"), "results")
    old_p = _by_ticker(_load(previous / "artifacts" / "petros_review_latest.json"), "results") if previous else {}
    if old_p:
        entered = sorted(t for t, row in new_p.items() if row.get("petros_verdict") == "candidate"
                         and (old_p.get(t) or {}).get("petros_verdict") != "candidate")
        left = sorted(t for t, row in old_p.items() if row.get("petros_verdict") == "candidate"
                      and (new_p.get(t) or {}).get("petros_verdict") != "candidate")
        if entered:
            items.append(f"Νέοι υποψήφιοι του Petros ({len(entered)}): {', '.join(entered[:10])}.")
        if left:
            items.append(f"Έπαψαν να είναι υποψήφιοι του Petros ({len(left)}): {', '.join(left[:10])}.")
    for result in (outcome_evaluation or {}).get("results") or []:
        if result.get("ordering") == "inverted":
            items.append(f"Τα αποτελέσματα αγοράς δείχνουν αντίστροφη σειρά για {result.get('agent_id')} "
                         f"στις {result.get('horizon_days')} συνεδριάσεις (υποψήφιο μάθημα για έγκριση).")
    lines = ["## Τι θέλει την προσοχή σου", ""]
    lines += [f"- {item}" for item in items] or ["- Τίποτα καινούριο σήμερα."]
    lines += ["- Γνωστά ανοιχτά θέματα (δεν αλλάζουν από μέρα σε μέρα): " + "· ".join(KNOWN_OPEN) + ".", ""]
    return lines


def _paper_learning_lines(evaluation: dict[str, Any]) -> list[str]:
    """What the paper trades taught so far (paper_learning.py, after each afternoon session)."""
    if not evaluation:
        return []
    overall = evaluation.get("overall") or {}

    def pct(value: Any) -> str:
        return UNKNOWN if value is None else f"{_v(value)}%"

    judged = [n for n, c in (evaluation.get("strategies") or {}).items() if c.get("status") == "judged"]
    lessons = evaluation.get("lesson_candidates") or []
    lines = ["## Μάθηση από paper trades", "",
             f"- Συνεδρίες {evaluation.get('sessions')} · trades {evaluation.get('trades')} · κερδισμένα "
             f"{pct(overall.get('win_rate_pct'))} · μέσο αποτέλεσμα {pct(overall.get('mean_pct'))} · νίκησαν το SPY "
             f"{pct(overall.get('beat_spy_pct'))}.",
             f"- Στρατηγικές με αρκετό δείγμα για κρίση (≥{(evaluation.get('minimums') or {}).get('trades')} trades): "
             + (", ".join(judged) or "καμία ακόμη") + ".",
             f"- Υποψήφιες αλλαγές για έγκριση: {len(lessons)}."]
    lines += [f"  - {item.get('target_agent')}: {item.get('proposed_change')}" for item in lessons]
    lines += ["- Πλήρης αναφορά: output/paper_learning/evaluation_latest.md", ""]
    return lines


def build_report(run_dir: Path, timings: dict[str, Any] | None = None, state: dict[str, Any] | None = None,
                 outcome_evaluation: dict[str, Any] | None = None) -> dict[str, Any]:
    art = run_dir / "artifacts"
    manifest = _load(run_dir / "manifest.json")
    babis = _load(art / "babis_shortlist_latest.json")
    giannis = _load(art / "giannis_rule_based_latest.json")
    petros = _load(art / "petros_review_latest.json")
    yankos = _load(art / "yankos_strategy_latest.json")
    periklis = _load(art / "periklis_strategy_latest.json")
    sokratis = _load(art / "sokratis_strategy_teacher_latest.json")
    batch = _load(art / "fundamental_control_plane_batch_latest.json")
    state = state or {}
    run_id = run_dir.name
    lines = [f"# Ημερήσια αναφορά agents · {run_id}", ""]
    lines += [f"- Κατάσταση run: {_v(state.get('status'))} · ξεκίνησε {_v(state.get('started_at'))} · "
              f"τέλος {_v(state.get('finished_at'))}",
              f"- Αρχεία run: {len(manifest.get('artifacts') or [])} (επαληθεύονται με hash στο manifest)",
              "- Όλα τα runs είναι μόνο έρευνα: καμία εντολή, καμία θέση.", ""]
    if state.get("status") == "interrupted":
        step = STEP_GR.get(state.get("last_step"), state.get("last_step") or UNKNOWN)
        lines += [f"- **Το run διακόπηκε** στο βήμα: {step}. Η διεργασία του δεν υπήρχε πια (έκλεισε ή επανεκκινήθηκε ο υπολογιστής, "
                  "ή τερματίστηκε). Η ώρα λήξης είναι η στιγμή που εντοπίστηκε η διακοπή· τα αποτελέσματα είναι ημιτελή.", ""]

    route_map = _by_ticker(_load(art / "all_listed_universe_routes_latest.json"), "routes")
    previous = _previous_run(run_dir)
    lines += _attention_section(run_dir, previous, state, outcome_evaluation)
    lines += _paper_learning_lines(_load(art / "evaluation_latest.json"))
    lines += macro_environment.report_lines(_load(art / "macro_environment_latest.json"),
                                            _load(art / "sokratis_strategy_teacher_latest.json").get("market_backdrop_lesson"))
    if previous is not None:
        lines += _changes_section(run_dir, previous, route_map)

    steps = [s for s in (timings or {}).get("steps") or [] if isinstance(s, dict)]
    if steps:
        lines += ["## Πόσο κράτησε κάθε βήμα", "", "| Βήμα | Διάρκεια |", "|---|---|"]
        lines += [f"| {STEP_GR.get(s.get('key'), s.get('key'))} | "
                  f"{_duration(s.get('seconds')) if s.get('finished_at') else 'δεν ολοκληρώθηκε'} |" for s in steps]
        lines.append("")

    routing = babis.get("universe_routing") or {}
    shortlist = _rows(babis, "shortlist")
    runtime = babis.get("runtime_summary") or {}
    lanes = routing.get("instrument_observations_deferred_to_lane") or {}
    quality = babis.get("data_quality_summary") or {}
    top_score = shortlist[0].get("composite_score") if shortlist else None
    ties = sum(row.get("composite_score") == top_score for row in shortlist) if top_score is not None else 0
    lines += ["## Babis · τι σάρωσε και τι κράτησε", "",
              f"- Σύμβολα στη λίστα: {_v(babis.get('universe_total'))}. Εκτός σάρωσης: "
              + (", ".join(f"{LANE_GR.get(k, k)} {v}" for k, v in lanes.items()) or "κανένα")
              + f", τίτλοι εκδότη {_v(routing.get('instrument_observations_deferred_to_issuer'))}.",
              f"- Χρόνος σάρωσης: {_duration(runtime.get('elapsed_seconds'))} "
              f"(τιμές {_duration(runtime.get('market_data_seconds'))}, στοιχεία/news "
              f"{_duration(runtime.get('fundamentals_and_news_seconds'))}, SEC {_duration(runtime.get('sec_seconds'))}).",
              f"- Κράτησε {_v(babis.get('shortlist_total'))} εταιρείες. Χωρίς αρκετό ιστορικό τιμών: "
              f"{_v(quality.get('history_exclusion_count'))}, χωρίς κάλυψη από πάροχο: "
              f"{_v(quality.get('coverage_exclusion_count'))}.",
              f"- Ισοβαθμίες: {ties} εταιρείες έχουν τον ανώτατο βαθμό {_v(top_score)}, οπότε η σειρά τους είναι αλφαβητική."
              if ties > 1 else "- Χωρίς ισοβαθμία στην κορυφή.", ""]
    if shortlist:
        lines += [f"Οι πρώτες {min(TOP, len(shortlist))} και γιατί:", ""]
        for row in shortlist[:TOP]:
            lines.append(f"- **{row.get('ticker')}** ({_v(row.get('company'))}) · βαθμός {_v(row.get('composite_score'))} · "
                         f"κίνδυνος {_v(row.get('risk_level'))} · καταλύτης: {_v(row.get('catalyst_bucket'))} "
                         f"({_v(row.get('catalyst_classification_reason'))}) · κανόνες: {_rule_reasons(row)}")
        lines.append("")

    routes = _rows(_load(art / "all_listed_universe_routes_latest.json"), "routes")
    spacs = [str(r.get("ticker")) for r in routes
             if r.get("separate_lane") == "spac_lane" or r.get("instrument_type") == "spac"]
    if spacs:
        from datetime import date
        from spac_watch import issuer_count, merger_issuers, summarize_filings

        root_dir = run_dir.parents[2] if len(run_dir.parents) > 2 else run_dir
        sec_tickers = root_dir / "cache" / "sec_company_tickers_exchange.json"
        mergers = merger_issuers(spacs, until=date.today(), sec_tickers=sec_tickers,
                                 registry_db=root_dir / "data" / "research_registry.db")
        lines += ["## SPAC · ανακοινώσεις συγχώνευσης (30 ημέρες)", "",
                  f"- SPAC στη δική τους κατηγορία: {len(spacs)} τίτλοι από {issuer_count(spacs, sec_tickers)} εκδότες. "
                  f"Με κατάθεση συγχώνευσης στο ημερολόγιο SEC: {len(mergers)} εκδότες (οι πιο πρόσφατοι πρώτοι).", ""]
        for issuer in mergers[:TOP * 2]:
            others = issuer["tickers"][1:]
            lines.append(f"- **{issuer['tickers'][0]}**" + (f" (+{', '.join(others)})" if others else "")
                         + f" · {_v(issuer.get('company_name'))} · {summarize_filings(issuer['filings'])}")
        lines.append("")

    counts = batch.get("counts") or {}
    if counts:
        lines += ["## Τομεακοί agents και οικονομικά SEC", "",
                  f"- Ολοκληρώθηκαν {_v(counts.get('completed'))}, μπλοκαρίστηκαν {_v(counts.get('blocked'))}, "
                  f"απέτυχαν {_v(counts.get('failed'))}.",
                  f"- Τομεακό πλαίσιο δημοσιεύτηκε για {_v(counts.get('sector_context_published'))}, "
                  f"χωρίς τομέα {_v(counts.get('sector_context_unroutable'))}.",
                  f"- Companyfacts από την αποθήκη (χωρίς νέα λήψη): {_v(counts.get('companyfacts_from_store'), 0)}."]
        coverage = [item.get("sector_evidence_coverage") for item in _rows(batch)
                    if isinstance(item.get("sector_evidence_coverage"), dict)]
        if coverage:
            answered = sum(int(c.get("answered") or 0) for c in coverage)
            required = sum(int(c.get("required") or 0) for c in coverage)
            lines.append(f"- Τομεακά στοιχεία από τη SEC: δείκτες κλάδου σε {_v(counts.get('sector_kpis_from_sec'), 0)}, "
                         f"ομάδα ομοειδών (ίδιο SIC) σε {_v(counts.get('sector_peer_groups'), 0)}, "
                         f"περιγραφή από το 10-K σε {_v(counts.get('business_descriptions'), 0)} εταιρείες· "
                         f"απαντημένα πεδία πλαισίου {answered} από {required} ({answered / required * 100:.1f}%).")
            tiers = Counter(str(item.get("sector_evidence_tier") or UNKNOWN) for item in _rows(batch)
                            if isinstance(item.get("sector_evidence_coverage"), dict))
            lines.append("- Βαθμίδες πακέτων τομέα: " + ", ".join(
                f"{TIER_GR.get(name, name)} {tiers[name]}" for name in ("complete", "adequate", "partial", "insufficient", UNKNOWN)
                if tiers.get(name)) + ".")
        permanent = sum(1 for item in _rows(batch) if (item.get("financial") or {}).get("permanent"))
        if permanent:
            lines.append(f"- Από τα μπλοκαρισμένα, {permanent} είναι μόνιμα: η SEC δεν δημοσιεύει companyfacts γι' αυτές (404)· "
                         "δεν ζητούνται ξανά για 14 ημέρες και μένουν άγνωστα.")
        lines.append("")

    root = run_dir.parents[2] if len(run_dir.parents) > 2 else run_dir
    gate = _load(root / "output" / "refresh_gate_latest.json")
    modes = _load(root / "data" / "refresh_modes.json")
    if gate or modes:
        lines += ["## Αποθήκες στοιχείων (πύλη Β/Γ)", ""]
        prefetch = _load(root / "output" / "companyfacts_prefetch_latest.json")
        if prefetch.get("counts"):
            c = prefetch["counts"]
            lines.append(f"- SEC companyfacts για όλες τις εταιρείες: κάλυψη {_v(prefetch.get('coverage_pct'), 1)}% "
                         f"({_v(c.get('current'), 0)} τρέχουσες, {_v(c.get('downloaded'), 0)} νέες λήψεις απόψε, "
                         f"{_v(c.get('deferred'), 0)} για επόμενα βράδια, {_v(c.get('failed'), 0)} αποτυχίες).")
        for key, label in (("companyfacts", "SEC companyfacts (Β)"), ("stage1_info", "yfinance Babis (Γ)")):
            decision = gate.get(key) or {}
            lines.append(f"- {label}: κατάσταση {_v((modes.get(key) or {}).get('mode'), 0) if modes.get(key) else 'όπως στη ρύθμιση'}"
                         f" · απόφαση απόψε: {_v(decision.get('decision'))} · {_v(decision.get('reason'))}")
        lines.append("")

    g_rows = _rows(giannis)
    lines += ["## Giannis · θεμελιώδη", "", f"- Εξέτασε {len(g_rows)}. Κατηγορίες: {_counts(g_rows, 'giannis_tier')}.", ""]
    for row in sorted(g_rows, key=lambda r: -(r.get("score_final") or 0))[:TOP]:
        lines.append(f"- **{row.get('ticker')}** · {_v(row.get('giannis_tier'))} · βαθμός {_v(row.get('score_final'))} · "
                     f"κίνδυνος {_v(row.get('risk_level'))} · {_rule_reasons(row)}")
    lines.append("")

    p_rows = _rows(petros)
    lines += ["## Petros · σύνθεση", "", f"- Εξέτασε {len(p_rows)}. Αποφάσεις: {_counts(p_rows, 'petros_verdict')}.", ""]
    for row in sorted(p_rows, key=lambda r: -(r.get("petros_score") or 0))[:TOP]:
        why = row.get("rationale")
        why = " ".join(map(str, why)) if isinstance(why, list) else _v(why)
        lines.append(f"- **{row.get('ticker')}** · {_v(row.get('petros_verdict'))} · βαθμός {_v(row.get('petros_score'))} · "
                     f"σιγουριά {_v(row.get('confidence'))} · {why}")
    lines.append("")

    for name, payload in (("Yankos · βραχυπρόθεσμα", yankos), ("Periklis · swing", periklis)):
        rows = _rows(payload)
        eligible = [r for r in rows if r.get("execution_eligible")]
        lines += [f"## {name}", "",
                  f"- Σήματα: {len(rows)}. Αποφάσεις: {_counts(rows, 'post_gate_verdict')}. "
                  f"Περνούν τους ελέγχους: {len(eligible)} (μόνο καταγραφή, καμία εντολή).", ""]
        for row in (eligible or rows)[:TOP]:
            reasons = row.get("gate_decision_reasons") or []
            lines.append(f"- **{row.get('ticker')}** · {_v(row.get('post_gate_verdict'))} · playbook "
                         f"{_v(row.get('playbook_name'))} · πλευρά {_v(row.get('entry_side'))} · σιγουριά "
                         f"{_v(row.get('confidence'))}" + (f" · έλεγχοι: {'; '.join(map(str, reasons))}" if reasons else ""))
        lines.append("")

    proposals = [p for p in sokratis.get("change_proposals") or [] if isinstance(p, dict)]
    lines += ["## Sokratis · τι προτείνει στους traders", "",
              f"- Προτάσεις αλλαγής: {len(proposals)} (όλες περιμένουν έγκριση, καμία δεν εφαρμόζεται μόνη της).", ""]
    for proposal in proposals[:TOP]:
        title = next((proposal.get(k) for k in ("title", "summary", "proposal", "change", "description") if proposal.get(k)), None)
        lines.append(f"- {_v(title)} · αφορά {', '.join(map(str, proposal.get('affected_agents') or [])) or UNKNOWN} · "
                     f"κατάσταση {_v(proposal.get('approval_status'))}")
    lines.append("")
    return {"run_id": run_id, "generated_at": datetime.now(timezone.utc).isoformat(), "markdown": "\n".join(lines)}


def _same_or_later(stamp: Any, reference: str) -> bool:
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")) >= datetime.fromisoformat(reference.replace("Z", "+00:00"))
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    if not args.run_id or any(part in args.run_id for part in ("/", "\\", "..")):
        raise SystemExit(f"Invalid run id: {args.run_id}")
    # A failed run may have no archived artifacts; the report then shows the timings and unknowns.
    run_dir = args.root / "output" / "runs" / args.run_id
    timings = _load(args.root / "output" / "pipeline_step_timings_latest.json")
    state = _load(args.root / "output" / "full_research_control_plane_background_latest.json")
    # The outcome evaluation is written at the end of the same run, before this report.
    evaluation = _load(args.root / "output" / "outcome_evaluation_latest.json")
    started = str(state.get("started_at") or "") if state.get("run_id") == args.run_id else ""
    fresh_evaluation = evaluation if started and _same_or_later(evaluation.get("generated_at"), started) else None
    report = build_report(run_dir, timings if timings.get("run_id") == args.run_id else None,
                          state if state.get("run_id") == args.run_id else None, fresh_evaluation)
    out = args.root / "output" / "daily_reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.run_id}.md").write_text(report["markdown"], encoding="utf-8")
    (out / "latest.md").write_text(report["markdown"], encoding="utf-8")
    print(json.dumps({"run_id": args.run_id, "report": str(out / f"{args.run_id}.md")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
