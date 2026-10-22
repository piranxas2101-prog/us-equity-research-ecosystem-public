# Catalyst Taxonomy and Long/Short Lenses

## Purpose

The catalyst contract answers four different questions without mixing them:

1. What happened?
2. How material and well evidenced is it?
3. Is its observed direction positive, negative, mixed, or unknown?
4. How much does it support a long or short catalyst thesis?

The hierarchy is machine-readable:

```text
trade lens (long / short)
└── catalyst category
    └── subcategory / event rule
        └── evidence (source, date, SEC form/item, URL, confidence)
```

Long and short are analytical lenses, not properties of an event. A delisting notice is still a `listing_compliance` event; its negative direction and category relevance make its short-lens score high. This preserves objective classification and prevents downstream agents from interpreting materiality as bullishness.

## Categories

| ID | Reader question | Typical long/short relevance |
|---|---|---|
| `earnings_guidance` | Did results, guidance, KPIs, or accounting change the earnings path? | High / High |
| `commercial_customer` | Is there a commercially meaningful contract, customer, or partnership event? | High / High |
| `product_technology` | Did a product or technology milestone change monetization visibility? | Medium / Medium |
| `clinical_healthcare` | Did clinical or health-regulatory evidence change viability? | High / High |
| `regulatory_legal` | Did a regulator, permit, court, or policy change operating ability? | High / High |
| `financing_liquidity` | Did debt, covenant, runway, or financing availability change survival risk? | High / Very high |
| `dilution_capital_structure` | Can offering, ATM, convert, warrants, or share-count changes impair holders? | Low / Very high |
| `m_and_a_strategic` | Did M&A, activism, divestiture, or control change enterprise value? | Very high / Medium |
| `listing_compliance` | Is listing or reporting eligibility at risk? | Low / Very high |
| `governance_management` | Did leadership, auditor, voting, or control change execution risk? | Medium / Medium |
| `investor_events` | Will an event provide new decision-relevant disclosure? | Low / Low |
| `technical_market_structure` | Can float, ownership, lockup, borrow, or flows affect price formation? | Medium / Medium |
| `macro_sector` | Is there a direct, evidenced macro/sector exposure? | Medium / Medium |
| `unknown` | What evidence is missing before classification? | None / None |

## Scoring

The attention score is 0–100 and is calculated from:

- category materiality: 45%
- evidence/source confidence: 25%
- recency: 20%
- event specificity: 10%

Direction is evaluated separately. Each subcategory then gets a long and short relevance score:

```text
side score = attention × direction alignment × category side relevance × confidence multiplier
```

The category score aggregates its three strongest subcategories using weights `1.0 / 0.2 / 0.1`. The Long or Short parent repeats the same bounded aggregation over category scores. This rewards independent confirming evidence but prevents a pile of duplicate headlines from increasing a score without limit.

## Output contract

Each Babis handoff row includes:

- `catalyst_category_id`, direction, attention, priority, confidence and evidence
- `long_catalyst_score` and `short_catalyst_score`
- `catalyst_trade_bias`
- `catalyst_trade_lenses.long.categories[].subcategories[]`
- `catalyst_trade_lenses.short.categories[].subcategories[]`

`catalyst_trade_bias` always begins with `catalyst_only_`. It is explicitly advisory and must never create an order by itself. Valuation, technical setup, liquidity, borrow availability, portfolio exposure, execution rules, and hard risk controls remain independent gates.

## Examples

- Generic `8-K` with no mapped item: `unknown`, zero Long/Short support, content review required.
- `8-K Item 3.01`: `listing_compliance`, negative direction, strong Short catalyst support.
- `S-3` or `424B`: `dilution_capital_structure`, negative by default, strong Short catalyst support.
- `10-Q`: `earnings_guidance`, mixed until the filing content establishes direction.
- FDA approval headline: `clinical_healthcare`, positive when the text supports it, but with lower confidence when the only source is secondary news.

The configuration lives in `catalyst_taxonomy.json`; deterministic classification and aggregation live in `catalyst_taxonomy.py`.
