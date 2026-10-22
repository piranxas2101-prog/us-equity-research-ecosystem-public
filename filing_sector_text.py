"""Sector evidence from a 10-K's own text: verbatim sentences for every sector-framework field, no model.

Owner's direction 2026-10-06: every field of a company's sector framework should be known, judged per company. This
module reads the company's latest 10-K (fetched once per 10-K by ``filing_business_description``) and keeps, word
for word, the sentences that state each field:

- KPIs: sentences in Item 7 (MD&A) or Item 1 that name the KPI; KPIs that are numbers need a number in the sentence.
- Risks: the headline sentences of Item 1A (each risk factor starts with one), split into industry-wide and
  company-specific by their wording.
- Competitive position: the opening of the "Competition" part of Item 1.
- Revenue and cost drivers: MD&A sentences that explain a change in revenue or in costs ("primarily due to").
- Value-chain position: Item 1 sentences that say who the company sells to, through whom, or what it buys.

Every sentence is the company's own claim (``issuer_management_claim``) and carries the rule that selected it
(``basis``), so a reviewer can see why it was chosen. A field with no matching sentence stays unknown. Nothing here
interprets or summarises.
"""
from __future__ import annotations

import re
from typing import Any

import filing_llm_extractor as fx

EXTRACTOR_VERSION = "filing-sector-text.v1"
MAX_PER_FIELD = 2
NUMBER = re.compile(r"\d[\d,.]*\s*(?:%|percent|million|billion|thousand|basis points|bps)|\$\s?\d|\b\d{2,}[\d,.]*\b",
                    re.IGNORECASE)

# kpi -> (phrases as companies write them, needs a number)
KPI_PHRASES: dict[str, tuple[tuple[str, ...], bool]] = {
    "active_users": (("active users", "monthly active", "daily active", "active accounts", "maus", "daus"), True),
    "advertising_mix": (("advertising revenue", "ad revenue", "advertising represented"), True),
    "aftermarket_mix": (("aftermarket", "parts and service", "services revenue"), True),
    "allowed_return": (("allowed return", "authorized return on equity", "allowed roe", "return on equity of"), True),
    "arpu": (("arpu", "average revenue per user", "average revenue per unit", "average revenue per account"), True),
    "arr_or_subscription_growth": (("annual recurring revenue", "arr", "subscription revenue", "recurring revenue"), True),
    "asset_quality": (("class a", "investment grade tenants", "portfolio quality", "newly constructed"), False),
    "assets_under_management": (("assets under management", "aum", "fee-earning assets"), True),
    "audience_reach": (("audience", "viewers", "listeners", "reach of"), True),
    "average_ticket": (("average ticket", "average transaction", "average order value", "average check", "average selling price"), True),
    "backlog": (("backlog",), True),
    "book_to_bill": (("book-to-bill", "book to bill"), True),
    "bookings": (("bookings", "net orders", "gross bookings"), True),
    "brand_strength": (("our brands", "brand recognition", "well-known brands", "brand portfolio"), False),
    "breakeven": (("breakeven", "break-even"), True),
    "capacity": (("capacity of", "production capacity", "nameplate capacity", "capacity utilization"), True),
    "capacity_and_node_exposure": (("nanometer", "process node", "wafer capacity", "fab capacity"), True),
    "capacity_factor": (("capacity factor",), True),
    "capital_plan": (("capital plan", "capital expenditures of", "capital investment plan", "planned capital"), True),
    "capital_ratios": (("cet1", "common equity tier 1", "tier 1 capital ratio", "total capital ratio", "leverage ratio"), True),
    "cash_cost": (("cash cost", "all-in sustaining cost", "aisc", "cost per ounce", "cost per pound"), True),
    "cash_runway_context": (("sufficient to fund", "fund our operations", "cash runway", "into 20"), False),
    "category_share": (("market share", "share of the category", "category share"), True),
    "channel_mix": (("e-commerce", "digital sales", "wholesale", "direct-to-consumer", "omnichannel"), True),
    "churn": (("churn",), True),
    "client_assets": (("client assets", "total client assets", "customer assets"), True),
    "combined_ratio": (("combined ratio",), True),
    "commodity_exposure": (("commodity prices", "price of copper", "price of gold", "oil prices", "natural gas prices"), False),
    "completion_risk": (("completion of", "construction delays", "cost overruns"), False),
    "containerboard_or_pulp_price": (("containerboard", "pulp prices", "linerboard"), True),
    "content_cost": (("content costs", "content amortization", "programming costs", "content spending"), True),
    "contract_backlog": (("contract backlog", "backlog", "contracted revenue"), True),
    "contract_retention": (("retention rate", "renewal rate", "contract renewals", "client retention"), True),
    "contract_structure": (("take-or-pay", "fixed-price contracts", "cost-plus", "long-term contracts", "fee-based"), False),
    "contracted_output": (("contracted", "power purchase agreement", "ppa", "under contract"), True),
    "credit_quality": (("net charge-offs", "nonperforming", "non-performing", "allowance for credit losses", "nonaccrual"), True),
    "customer_acquisition": (("new customers", "customer acquisition", "added customers", "net new"), True),
    "customer_concentration": (("accounted for", "largest customer", "ten largest customers", "% of our revenue", "percent of our revenue"), True),
    "customer_growth": (("customer growth", "number of customers", "customers increased", "customer count"), True),
    "deposit_mix": (("noninterest-bearing deposits", "non-interest-bearing deposits", "core deposits", "deposit mix"), True),
    "design_wins": (("design wins", "design win"), False),
    "distribution_reach": (("points of distribution", "retail outlets", "distribution network", "countries"), True),
    "end_market_mix": (("end market", "end-market", "automotive", "data center", "industrial market"), True),
    "engagement": (("engagement", "time spent", "hours watched", "sessions"), True),
    "feedstock_cost": (("feedstock", "raw material costs", "ethane", "propane costs"), False),
    "fiber_cost": (("fiber costs", "recycled fiber", "wood fiber", "occ prices"), False),
    "fleet_capacity": (("fleet of", "available seat miles", "asms", "vessels", "aircraft in our fleet", "tractors"), True),
    "fuel_exposure": (("fuel costs", "fuel expense", "price of fuel", "fuel hedg"), False),
    "fuel_mix": (("fuel mix", "natural gas-fired", "coal-fired", "renewable fuels"), False),
    "fuel_or_power_price_exposure": (("power prices", "electricity prices", "natural gas prices", "fuel costs"), False),
    "generation_mix": (("generation mix", "megawatts", "mw of", "gw of", "generating capacity"), True),
    "grade": (("grade", "grams per tonne", "g/t", "ounces per ton"), True),
    "housing_cycle": (("housing starts", "housing market", "mortgage rates", "home sales"), False),
    "input_costs": (("input costs", "raw material costs", "commodity costs", "cost inflation"), False),
    "installed_base": (("installed base",), False),
    "inventory": (("inventory", "inventories"), True),
    "investment_income": (("net investment income",), True),
    "labor_intensity": (("employees", "labor costs", "headcount", "wage"), True),
    "land_or_project_exposure": (("lots owned", "land holdings", "acres", "lots controlled"), True),
    "lease_duration": (("weighted average lease term", "remaining lease term", "walt", "lease expirations"), True),
    "load_factor": (("load factor",), True),
    "loan_mix": (("commercial real estate loans", "residential mortgage", "commercial and industrial", "consumer loans", "loan portfolio"), True),
    "loss_reserves": (("loss reserves", "reserves for losses", "loss and loss adjustment expense reserves"), True),
    "monetization": (("monetization", "revenue per user", "take rate", "average revenue per"), True),
    "net_flows": (("net flows", "net inflows", "net outflows", "organic growth"), True),
    "net_interest_margin": (("net interest margin",), True),
    "network_coverage": (("coverage", "covers", "population covered", "pops"), True),
    "network_effects": (("network effect", "more users", "flywheel"), False),
    "occupancy": (("occupancy", "occupied", "leased percentage", "percent leased", "% leased"), True),
    "orders": (("orders", "order intake", "new orders"), True),
    "patent_exclusivity": (("patent", "exclusivity", "patents expire", "market exclusivity"), False),
    "pipeline_stage": (("phase 1", "phase 2", "phase 3", "phase i", "phase ii", "phase iii", "pivotal", "clinical trial"), False),
    "premium_growth": (("premiums written", "premiums earned", "gross written premiums", "net written premiums"), True),
    "pricing_power": (("price increases", "pricing actions", "favorable pricing", "pricing power", "higher prices"), False),
    "private_label_risk": (("private label", "store brands"), False),
    "procedure_volume": (("procedures", "procedure volume", "utilization"), True),
    "product_cycle": (("product cycle", "new product introductions", "product launch", "next-generation"), False),
    "product_mix": (("product mix", "mix of"), False),
    "production": (("production of", "barrels of oil equivalent", "boe", "mboe", "production averaged"), True),
    "production_volume": (("production of", "ounces", "tonnes", "pounds of", "production volume"), True),
    "project_pipeline": (("pipeline of", "development pipeline", "projects under development", "under construction"), True),
    "rate_base": (("rate base",), True),
    "realized_price": (("realized price", "average realized", "realized prices"), True),
    "refining_or_transport_utilization": (("utilization", "throughput", "refinery utilization"), True),
    "regulatory_clearance": (("fda clearance", "510(k)", "fda approval", "ce mark", "pma"), False),
    "regulatory_jurisdictions": (("public utility commission", "regulated by the", "state commissions", "ferc"), False),
    "regulatory_milestones": (("pdufa", "nda", "bla", "fda approval", "submission", "regulatory approval"), False),
    "reimbursement": (("reimbursement", "medicare", "medicaid", "payors", "payers"), False),
    "remaining_performance_obligations": (("remaining performance obligations", "rpo"), True),
    "reserves": (("proved reserves", "proven and probable", "mineral reserves", "reserves of"), True),
    "retention": (("net revenue retention", "dollar-based net retention", "retention rate", "net retention", "gross retention"), True),
    "rig_or_fleet_activity": (("rig count", "active rigs", "fleet utilization", "frac fleets"), True),
    "same_property_growth": (("same-property", "same property", "same-store noi", "same store noi", "comparable property"), True),
    "same_store_sales": (("same-store sales", "same store sales", "comparable sales", "comparable store sales", "comp sales"), True),
    "service_intensity": (("service intensity", "service revenue per"), True),
    "service_mix": (("services revenue", "service revenue", "recurring services"), True),
    "shipment_volume": (("shipments", "tons shipped", "shipment volume"), True),
    "spectrum_position": (("spectrum", "mhz"), True),
    "stock_based_compensation_context": (("stock-based compensation", "share-based compensation"), True),
    "subscriber_metrics": (("subscribers", "paid memberships", "subscriptions"), True),
    "subscribers": (("subscribers", "connections", "customers"), True),
    "take_rate": (("take rate",), True),
    "tenant_concentration": (("largest tenant", "top 10 tenants", "ten largest tenants", "annualized base rent"), True),
    "traffic": (("traffic", "transactions", "guest counts", "visits"), True),
    "transaction_volume": (("transaction volume", "payment volume", "total payment volume", "tpv", "trading volume"), True),
    "trial_design": (("randomized", "double-blind", "primary endpoint", "placebo-controlled"), False),
    "unit_or_usage_growth": (("units", "usage", "volume growth", "unit growth"), True),
    "units": (("units", "deliveries", "vehicles delivered", "units sold"), True),
    "utilization": (("utilization",), True),
    "volume_price_mix": (("volume", "price/mix", "price and mix", "pricing"), True),
    "volumes": (("volumes", "shipments", "revenue ton-miles", "carloads", "tonnage"), True),
    "warranty_risk": (("warranty", "recall"), False),
    "yield": (("yield", "passenger revenue per", "revenue per available"), True),
}

INDUSTRY_WORDS = re.compile(r"\b(industry|industries|market conditions|economic|economy|competition|competitive|"
                            r"regulat\w*|legislation|government|interest rates?|inflation|tariffs?|commodity|"
                            r"pandemic|geopolitical|climate|macroeconomic|customers? demand)\b", re.IGNORECASE)
DRIVER_LINK = re.compile(r"\b(primarily|mainly|largely|partially|partly)?\s*(due to|driven by|as a result of|attributable to|"
                         r"reflecting|resulting from|offset by)\b", re.IGNORECASE)
REVENUE_WORDS = re.compile(r"\b(revenue|revenues|net sales|sales increased|sales decreased|total sales)\b", re.IGNORECASE)
COST_WORDS = re.compile(r"\b(cost of (?:revenue|sales|goods sold)|operating expenses|gross margin|expenses increased|"
                        r"expenses decreased|cost increase|costs increased|costs decreased)\b", re.IGNORECASE)
VALUE_CHAIN = re.compile(r"\b(our customers include|we sell (?:our products )?(?:to|through|directly)|sold (?:primarily )?"
                         r"(?:to|through)|distributors?|resellers|we (?:purchase|source|obtain) (?:our )?(?:raw materials|"
                         r"components|products)|suppliers?|end users|original equipment manufacturers|oems)\b", re.IGNORECASE)


PAGE_FURNITURE = re.compile(r"(?:^|\s)(?:\d{1,3}\s+)?table of contents\s*", re.IGNORECASE)
TABLE_HEADER = re.compile(r"\(in (?:millions|thousands)|except per|change compared to prior|% change|"
                          r"years? ended [a-z]+ \d+, \d{4} (?:and|change|\d)", re.IGNORECASE)
LEADING_MARKS = re.compile(r"^[^A-Za-z0-9(\"$]+")


def clean(sentence: str) -> str | None:
    """Drop page furniture and bullets; reject flattened table rows (a sentence that is mostly numbers)."""
    text = LEADING_MARKS.sub("", PAGE_FURNITURE.sub(" ", sentence)).strip()
    tokens = text.split()
    if len(text) < 30 or not tokens:
        return None
    numeric = sum(1 for token in tokens if re.fullmatch(r"[\d$,.%()\-–]+", token))
    if numeric / len(tokens) > 0.25 or text.count("$") > 4 or TABLE_HEADER.search(text):
        return None
    return text


def _blocks(html: str) -> list[str]:
    parser = fx._BlockParser()
    parser.feed(html)
    return [block for block in (fx.normalize(b) for b in parser.blocks) if block]


def _sections(doc: fx.FilingText) -> dict[str, tuple[int, int]]:
    sections = fx.locate_sections(doc)
    mdna = fx._span(doc, lambda h: h.item == "7" and ("management" in h.title or "discussion" in h.title),
                    lambda h: h.item in {"7a", "8"}, 2000)
    mdna = mdna or fx._span(doc, lambda h: h.item == "5" and "operating" in h.title,  # 20-F: operating review
                            lambda h: h.item in {"5a", "6"} or (h.item == "6" and "director" in h.title), 2000)
    if mdna:
        sections["mdna"] = mdna
    return sections


def _sentences(doc: fx.FilingText, span: tuple[int, int] | None) -> list[str]:
    raw = fx.split_sentences(doc.text[span[0]:span[1]]) if span else []
    return [text for text in (clean(sentence) for sentence in raw) if text]


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z])" + re.escape(phrase.strip()) + r"(?![a-z])", re.IGNORECASE)


_PATTERNS = {kpi: [_phrase_pattern(p) for p in phrases] for kpi, (phrases, _numeric) in KPI_PHRASES.items()}


def kpi_sentences(sentences: list[tuple[str, str]]) -> dict[str, list[dict[str, str]]]:
    """For every known KPI, up to MAX_PER_FIELD sentences that name it (with a number when the KPI is a number)."""
    found: dict[str, list[dict[str, str]]] = {}
    for kpi, patterns in _PATTERNS.items():
        numeric = KPI_PHRASES[kpi][1]
        for section, sentence in sentences:
            if len(found.get(kpi, [])) >= MAX_PER_FIELD:
                break
            if any(p.search(sentence) for p in patterns) and (not numeric or NUMBER.search(sentence)):
                found.setdefault(kpi, []).append({"text": sentence, "section": section,
                                                  "basis": "names_kpi_with_number" if numeric else "names_kpi"})
    return found


def risk_headlines(blocks: list[str], risk_text: str) -> list[str]:
    """Risk-factor headlines: short one-sentence blocks inside Item 1A that a longer paragraph follows."""
    if not risk_text:
        return []
    headlines = []
    for index, block in enumerate(blocks[:-1]):
        if 50 <= len(block) <= 400 and block.endswith(".") and len(blocks[index + 1]) > len(block) \
                and block.count(". ") == 0 and block in risk_text and not block.lower().startswith("item"):
            text = clean(block)
            if text:
                headlines.append(text)
    return headlines


def competition_lead(blocks: list[str], business_text: str) -> list[str]:
    """The first sentences after a "Competition" heading inside Item 1."""
    for index, block in enumerate(blocks):
        if block.lower().strip(" .:") in {"competition", "competitive environment", "competitive landscape", "competitors"} \
                and index + 1 < len(blocks) and blocks[index + 1] in business_text:
            sentences = [clean(s) for s in fx.split_sentences(" ".join(blocks[index + 1:index + 3]))]
            return [s for s in sentences if s][:MAX_PER_FIELD]
    return []


def extract(html: str) -> dict[str, Any]:
    """Everything this module can state about a 10-K, framework-independent (the packet picks what applies)."""
    doc = fx.parse_filing(html)
    sections = _sections(doc)
    business = doc.text[sections["business"][0]:sections["business"][1]] if "business" in sections else ""
    risk_text = doc.text[sections["risk_factors"][0]:sections["risk_factors"][1]] if "risk_factors" in sections else ""
    business_sentences = _sentences(doc, sections.get("business"))
    mdna_sentences = _sentences(doc, sections.get("mdna"))
    blocks = _blocks(html)
    headlines = risk_headlines(blocks, risk_text)
    revenue = [s for s in mdna_sentences if REVENUE_WORDS.search(s) and DRIVER_LINK.search(s)][:MAX_PER_FIELD]
    cost = [s for s in mdna_sentences if COST_WORDS.search(s) and DRIVER_LINK.search(s)][:MAX_PER_FIELD]
    value_chain = [s for s in business_sentences if VALUE_CHAIN.search(s)][:MAX_PER_FIELD]

    def items(texts: list[str], section: str, basis: str) -> list[dict[str, str]]:
        return [{"text": t, "section": section, "basis": basis} for t in texts]

    return {
        "extractor_version": EXTRACTOR_VERSION,
        "sections_found": sorted(sections),
        "kpis": kpi_sentences([("Item 7", s) for s in mdna_sentences] + [("Item 1", s) for s in business_sentences]),
        "industry_risks": items([h for h in headlines if INDUSTRY_WORDS.search(h)][:3], "Item 1A", "risk_headline_industry_wording"),
        "company_specific_risks": items([h for h in headlines if not INDUSTRY_WORDS.search(h)][:3], "Item 1A",
                                        "risk_headline_company_wording"),
        "competitive_position": items(competition_lead(blocks, business), "Item 1", "competition_section_opening"),
        "revenue_drivers": items(revenue, "Item 7", "revenue_change_explained"),
        "cost_drivers": items(cost, "Item 7", "cost_change_explained"),
        "value_chain_position": items(value_chain, "Item 1", "customers_channels_suppliers"),
    }
