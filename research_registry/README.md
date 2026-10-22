# Research registry

Local issuer registry and internal four-level taxonomy: 11 sectors, 106 subindustries, 196 total nodes in version 2.1.0. No network, LLM or broker calls. This is an internal classification, not official GICS.

## Commands (from the project root)

```powershell
.\run_py.cmd -m research_registry init
.\run_py.cmd -m research_registry import-identities cache/sec_company_tickers_exchange.json --as-of 2026-09-01T12:00:00+00:00
.\run_py.cmd -m research_registry import-profiles output/babis_shortlist_latest.json
.\run_py.cmd -m research_registry summary
.\run_py.cmd -m research_registry browse --node technology
.\run_py.cmd -m research_registry pending
```

Replace the illustrative `--as-of` with the actual known cache observation time. The importer does not use a newer identity snapshot to resolve older profiles. An unresolved identity is retained in `pending_record`. Reimporting identical input with the same observation timestamp is a no-op. A future reconciliation command is needed to promote previously pending records after new evidence; they are not silently rewritten.

Initial installation on 2026-09-07 used the exchange cache file modification time (2026-09-02 09:21:34 UTC, including its stored fractional seconds) as an observation-time proxy because the cache has no embedded retrieval timestamp. This is not proof of listing inception or a verified point-in-time security master. Future cache writers should persist retrieval metadata explicitly. All imported classifications are provisional.

Also available through `market_intel_db.py registry summary` and the other registry commands. `python -m research_registry --db PATH ...` overrides the dedicated default `data/research_registry.db`; the old market intelligence database is not replaced.

Existing `symbol_classification.classify_symbol()` now adds `research_classification`: version, deepest supported node, full ancestor path, depth and provisional status. Existing fields stay compatible. Provider text is mapped only through explicit aliases; broad semiconductor labels stay at industry depth and unknown industries at sector depth. Add reviewed aliases in `core.py` as coverage improves. Do not label provisional mappings verified automatically.

`company` uses SEC CIK identity. The SEC ticker cache provides issuer/ticker observations, not a verified security/share-class master; the importer intentionally does not invent securities or listing effective dates. Cached profile payloads preserve provenance and historical technical fields only as raw source material. They do not produce technical signals. Normalized financial facts, events, themes, securities and listing tables are installed by `schema.sql`; specialized loaders for those tables are subsequent research work, not fabricated data in this migration.

Versioned taxonomy JSON is immutable once installed in a database. For taxonomy changes use a new version and an explicit migration. SQLite enforces foreign keys and uniqueness of open primary classifications. Importers handle monotonic snapshot changes; conflicting/older classification changes enter the review queue.

The module does not disable useful ecosystem loops. The separate removal of the old technical-analysis execution path is outside this registry integration.

## Reviewed production batches

`classification_policy.json` defines the required evidence and review policy per instrument type. After a Babis v2 job has fully finished, `classification_governance.py` can create an immutable dry-run batch, validate it, apply only explicitly approved change IDs to a reversible overlay, and roll back that batch. It never calls a network service or a model. See `docs/babis_production_readiness.md` for the approval checklist and taxonomy-gap process. Do not prepare a batch from a running/partial job and do not treat `approved_provisional` as verified.

Validation: `python -m unittest discover -s tests -p test_research_registry.py`. Tests use temporary files and an in-memory database.

## Full US exchange directory

```powershell
.\run_py.cmd -m research_registry universe build
.\run_py.cmd -m research_registry universe summary
.\run_py.cmd -m research_registry universe browse --node technology
.\run_py.cmd -m research_registry universe browse --ticker AAPL
```

Build uses already downloaded files in `data/research_universe_sources`; it makes no network or LLM calls. Explicit source refresh is a separate command, `python -m research_registry.acquire_universe`. There is no scheduler, polling, automatic retry or symbol-by-symbol model request in this module.

Sources: [Nasdaq Trader directory definitions](https://www.nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs), its Nasdaq-listed and other-listed directories, and the [Nasdaq stock screener](https://www.nasdaq.com/market-activity/stocks/screener). Source URLs, retrieval timestamps and SHA-256 checksums are preserved in the manifest and snapshot provenance. Directory creation timestamps are preserved verbatim without assuming a timezone.

The September 4, 2026 directories contain 13,155 non-test issues across Nasdaq, NYSE, NYSE American, NYSE Arca and Cboe BZX. All are retained without a size, price or shortlist filter; 33 test issues are excluded. This covers the equity/ETP listing directory, not OTC, options or futures, nor every venue where an already-listed security can trade. Foreign issuers listed in the US are retained. It is a dated snapshot, not a live listing guarantee.

The internal hierarchy is sector, industry group, industry, subindustry. Explicit provider-industry crosswalks take precedence over conflicting provider-sector labels. All sector assignments remain provisional, not verified GICS. Broad source descriptions stop at the deepest supported level. Exact directory aliases are used for profile matching; multiple distinct matches are left unresolved. ETFs and special instruments are retained in separate instrument categories; no issuer classification is invented for them. Name-based instrument categories remain candidates where stated in `instrument_basis`.

Current coverage: 4,943 provisional corporate-sector assignments (2,399 at subindustry depth and 2,544 at broader levels); 418 corporate classifications awaiting disambiguation; 127 instruments of unknown type awaiting an asset master; 7,667 other instruments separated by type. These four groups reconcile to 13,155. Partial classifications and unresolved types are in the review export. Generic REIT labels require distinguishing equity from mortgage REITs before assignment.

Exports in `data/research_universe_exports`: `all_us_listed_symbols.csv` (all symbols and hierarchy columns), `coverage.json`, `provenance.json`, and `classification_review.json`. Blank hierarchy fields mean unsupported or inapplicable, never a verified absence of business activity. These are application data exports, not a valuation workbook. The snapshot tables are listing observations and do not fabricate issuer/share-class identities in the normalized security master. Existing issuer-level registry commands and the new `universe` commands are distinct views until an explicit identity reconciliation is implemented.

Snapshot identity includes source hashes, taxonomy, crosswalk and classifier source hash, so changed rules cannot silently reuse a prior classification snapshot. Existing snapshots are preserved. Offline tests cover retained special symbols, missing data, instrument separation, row reconciliation, source integrity, conservative hierarchy depth and idempotent/versioned builds. Run `python -B -m unittest discover -s tests -p "test_research_*.py"`.

Remaining work: enrich missing/ambiguous corporate profiles and unknown instrument types from an authoritative asset master; review broad provider descriptions against business evidence before promoting them to subindustry. Do not fill the gaps by guessing, and do not describe the current mapping as fully verified. This work does not disconnect the old technical-analysis pipeline.
