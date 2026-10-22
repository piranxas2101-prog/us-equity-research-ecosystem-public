"""Fast deterministic agent-panel synthesis from observed evidence only."""

from __future__ import annotations

import json
from typing import Any, Iterable


LABELS = {
    "babis": "Babis",
    "giannis": "Giannis",
    "yankos": "Yankos",
    "periklis": "Periklis",
    "sokratis": "Sokratis",
    "petros": "Petros",
    "koulis": "Koulis",
    "thymios": "Thymios",
    "miltos": "Miltos",
}


def _num(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def _market_cap(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if abs(number) >= 1_000_000_000:
        return f"{number / 1_000_000_000:.1f}B"
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    return f"{number:,.0f}"


class RuleBasedAgentEngine:
    """Produces named views without an LLM, network call, or free-form hallucination."""

    def generate_panel(self, team: str, agents: Iterable[str], prompt: str) -> str:
        payload = json.loads(prompt)
        packets = list(payload.get("research_packets") or [])
        greek = payload.get("reply_language") == "Greek"
        agent_list = list(agents)
        views = [self._view(agent, packets, greek) for agent in agent_list]
        challenge = self._challenge(team, agent_list, packets, greek)
        final = self._final(team, packets, greek)
        round_one = "\n".join(
            f"*{LABELS.get(agent, agent)}:* {view}" for agent, view in zip(agent_list, views)
        )
        if greek:
            return (
                "*ROUND 1 — Ανεξάρτητες απόψεις*\n"
                f"{round_one}\n\n"
                "*ROUND 2 — Ονομαστικός αντίλογος*\n"
                f"{challenge}\n\n"
                "*FINAL — Σύνθεση*\n"
                f"{final}"
            )
        return (
            "*ROUND 1 — Independent views*\n"
            f"{round_one}\n\n"
            "*ROUND 2 — Named challenge*\n"
            f"{challenge}\n\n"
            "*FINAL — Synthesis*\n"
            f"{final}"
        )

    @staticmethod
    def _packet_summary(packet: dict[str, Any], greek: bool) -> str:
        ticker = packet.get("ticker") or "?"
        price = packet.get("price") or {}
        last = _num(price.get("last"))
        day = _num(price.get("return_1d_pct"))
        month = _num(price.get("return_1m_pct"))
        provider = price.get("provider") or "unknown"
        if greek:
            return f"{ticker}: {last}, 1ημ {day}%, 1μ {month}% (πηγή {provider})"
        return f"{ticker}: {last}, 1d {day}%, 1m {month}% (source {provider})"

    def _view(self, agent: str, packets: list[dict[str, Any]], greek: bool) -> str:
        if not packets:
            return (
                "Δεν δόθηκε ticker· χρειάζεται συγκεκριμένο σύμβολο για evidence-based άποψη."
                if greek
                else "No ticker was supplied; a symbol is required for an evidence-based view."
            )
        packet = packets[0]
        ticker = str(packet.get("ticker") or "?")
        price = packet.get("price") or {}
        fundamentals = packet.get("fundamentals") or {}
        news = packet.get("news") or []
        filings = packet.get("filings") or []
        warnings = packet.get("warnings") or []
        last = _num(price.get("last"))
        sma20 = _num(price.get("sma_20"))
        sma50 = _num(price.get("sma_50"))
        low = _num(price.get("low_52w"))
        high = _num(price.get("high_52w"))
        provider = price.get("provider") or "unknown"
        if greek:
            if agent == "babis":
                if news:
                    return f"Για {ticker}, ο πλησιέστερος ειδησεογραφικός καταλύτης προς έλεγχο είναι «{news[0].get('title')}»· είναι context, όχι απόδειξη της θέσης."
                return f"Για {ticker} δεν επέστρεψε διαθέσιμη είδηση από το API και δεν θα επινοήσω καταλύτη."
            if agent == "giannis":
                filing = filings[0] if filings else {}
                filing_text = f"τελευταίο SEC {filing.get('form')} στις {filing.get('filing_date')}" if filing else "χωρίς διαθέσιμο SEC filing"
                return f"{fundamentals.get('company', ticker)}: κεφαλαιοποίηση {_market_cap(fundamentals.get('market_cap'))}, κλάδος {fundamentals.get('sector', 'n/a')}, {filing_text}· δεν υπάρχει επαρκής αποτίμηση χωρίς statements/πολλαπλασιαστές."
            if agent == "yankos":
                return f"{ticker} στα {last} έναντι SMA20 {sma20} και SMA50 {sma50}· η τακτική κατεύθυνση επιβεβαιώνεται μόνο με καθαρή διατήρηση πάνω ή κάτω από αυτά τα επίπεδα."
            if agent == "periklis":
                return f"Το εύρος 52 εβδομάδων είναι {low}–{high}· χρειάζεται υπομονή και επιβεβαίωση δομής, όχι συμπέρασμα από μία συνεδρίαση."
            if agent == "sokratis":
                checks = price.get("cross_checks") or {}
                return f"Η μέθοδος έχει {len(checks)} price cross-checks και κύρια τιμή από {provider}· οι διαφωνίες πηγών και τα κενά πρέπει να προηγούνται κάθε συμπεράσματος."
            if agent == "petros":
                return f"Τα παρατηρημένα στοιχεία για {ticker} είναι τιμή {last}, SMA20 {sma20}, SMA50 {sma50} και {len(filings)} πρόσφατα SEC filings· η επενδυτική ερμηνεία παραμένει υπόθεση μέχρι να ελεγχθούν τα statements."
            if agent == "koulis":
                return f"Read-only κριτική: το downside πρέπει να αξιολογηθεί έναντι 52w low {low} και των warnings ({len(warnings)}), χωρίς sizing ή έγκριση εντολής."
            if agent == "thymios":
                statuses = packet.get("provider_status") or {}
                ok_count = sum(value == "ok" for value in statuses.values())
                return f"Validation: {ok_count}/{len(statuses)} ρυθμισμένες πηγές απάντησαν, κύρια τιμή από {provider}, warnings {len(warnings)}· κάθε fallback παραμένει εμφανές."
            if agent == "miltos":
                return f"Read-only monitoring: παρακολούθηση {last}, SMA20 {sma20}, SMA50 {sma50} και νέου SEC/news event· καμία αυτόματη εκτέλεση."
        else:
            if agent == "babis":
                return f"For {ticker}, the nearest news item to verify is “{news[0].get('title')}”; it is context, not thesis proof." if news else f"No API news was available for {ticker}; no catalyst is inferred."
            if agent == "giannis":
                return f"{fundamentals.get('company', ticker)} has market cap {_market_cap(fundamentals.get('market_cap'))}; valuation remains incomplete without statements and multiples."
            if agent == "yankos":
                return f"{ticker} is {last} versus SMA20 {sma20} and SMA50 {sma50}; tactical direction needs persistence around those levels."
            if agent == "periklis":
                return f"The 52-week range is {low}–{high}; structure requires confirmation rather than a one-session conclusion."
            if agent == "sokratis":
                return f"Primary price comes from {provider}; source disagreement and missing fields must be resolved before inference."
            if agent == "petros":
                return f"Observed: {ticker} {last}, SMA20 {sma20}, SMA50 {sma50}, with {len(filings)} recent SEC filings; interpretation remains conditional."
            if agent == "koulis":
                return f"Read-only risk critique: test downside against 52w low {low} and {len(warnings)} warnings; no sizing or approval."
            if agent == "thymios":
                return f"Validation shows primary price from {provider} and {len(warnings)} warnings; fallbacks remain explicit."
            if agent == "miltos":
                return f"Read-only monitoring: watch {last}, SMA20 {sma20}, SMA50 {sma50}, and new SEC/news events; no execution."
        return self._packet_summary(packet, greek)

    @staticmethod
    def _challenge(
        team: str, agents: list[str], packets: list[dict[str, Any]], greek: bool
    ) -> str:
        challenger = LABELS.get(agents[-1], agents[-1]) if agents else "Lead"
        challenged = LABELS.get(agents[0], agents[0]) if agents else "Agent"
        missing = []
        if packets:
            packet = packets[0]
            if not packet.get("fundamentals"):
                missing.append("fundamentals")
            if not packet.get("news"):
                missing.append("news")
            missing.extend(packet.get("warnings") or [])
        gap = ", ".join(str(item) for item in missing[:3]) or "κανένα κρίσιμο καταγεγραμμένο κενό"
        if greek:
            return f"*{challenger} → {challenged}:* Η άποψή σου δεν πρέπει να μετατραπεί σε συμπέρασμα πριν ελεγχθούν τα κενά/προειδοποιήσεις: {gap}."
        return f"*{challenger} → {challenged}:* Do not turn the view into a conclusion before resolving these gaps/warnings: {gap}."

    def _final(self, team: str, packets: list[dict[str, Any]], greek: bool) -> str:
        if not packets:
            return (
                "**Συναίνεση:** δεν υπάρχει evidence packet. **Επόμενο βήμα:** δώσε ticker. **Εκτέλεση:** καμία."
                if greek
                else "**Consensus:** no evidence packet. **Next step:** provide a ticker. **Execution:** none."
            )
        summaries = "; ".join(self._packet_summary(packet, greek) for packet in packets)
        warnings = [warning for packet in packets for warning in (packet.get("warnings") or [])]
        if greek:
            return (
                f"**Συναίνεση:** τα παρατηρημένα στοιχεία είναι {summaries}. "
                "**Ανοιχτή διαφωνία:** τα δεδομένα δεν αρκούν από μόνα τους για bullish/bearish συμπέρασμα. "
                "**Prove:** επιβεβαίωση από SEC/FMP και συνέπεια τιμής μεταξύ Alpaca/Polygon. "
                "**Kill:** αντίθετο SEC στοιχείο ή ουσιώδης ασυμφωνία πηγών. "
                f"**Επόμενο βήμα:** έλεγχος του νεότερου filing και των {len(warnings)} warnings. "
                "**Εκτέλεση:** καμία."
            )
        return (
            f"**Consensus:** observed evidence is {summaries}. "
            "**Open disagreement:** the packet alone is insufficient for a bullish/bearish conclusion. "
            "**Prove:** SEC/FMP confirmation and consistent Alpaca/Polygon prices. "
            "**Kill:** contrary SEC evidence or material source disagreement. "
            f"**Next step:** review the latest filing and {len(warnings)} warnings. **Execution:** none."
        )
