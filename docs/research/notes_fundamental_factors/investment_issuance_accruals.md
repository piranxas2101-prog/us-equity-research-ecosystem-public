# Investment, financing and earnings-quality factors (accruals, NOA, issuance/buybacks, asset growth/investment): out-of-sample and post-publication evidence, US stocks, 1-12 month horizons

Method note for the report writer: besides the papers cited, the numbers marked **[CZ-calc]** were computed by the researcher from the Chen-Zimmermann Open Source Asset Pricing data (October 2026 release, monthly portfolio returns through 2025-12), files `Portfolios/Full Sets OP/PredictorPortsFull.parquet` (the original paper's construction), `Full Sets Alt/PredictorAltPorts_LiqScreen_ME_gt_NYSE20pct.parquet` (microcaps below the NYSE 20th percentile removed) and `Full Sets Alt/PredictorAltPorts_LiqScreen_VWforce.parquet` (forced value weights), from [Hugging Face OpenSourceAP/CrossSection](https://huggingface.co/datasets/OpenSourceAP/CrossSection), with sample and publication years from [SignalDoc.csv](https://raw.githubusercontent.com/OpenSourceAP/CrossSection/master/SignalDoc.csv). Statistics are the mean monthly long-short (LS) return in %, with a plain t-stat (no Newey-West, no factor adjustment, no costs). The "long leg" and "short leg" are measured against the **equal-weighted average of that signal's own portfolios**, not against the market: long-avg = long portfolio minus the average portfolio; avg-short = average portfolio minus short portfolio. Most CZ portfolios are held for 12 months and rebalanced annually (Accruals, PctAcc, AssetGrowth, ShareIss1Y/5Y, XFIN), so they sit at the 12-month end of the 1-12 month horizon. These are the researcher's own calculations from a primary dataset, not published figures.

## 1. Original vs post-publication returns (McLean-Pontiff 2016, Hou-Xue-Zhang, Jensen-Kelly-Pedersen 2023, Chen-Zimmermann)

### Takeaway
Across the literature, returns fall about 26% out of sample and 58% after publication (McLean-Pontiff). Within this family the decay is uneven. Classic accruals (Sloan) and percent operating accruals are essentially dead after publication (about 0 since 2000 / 2011). Asset growth and NOA survive only in microcap-heavy or equal-weighted versions, and only through the short side. **Net share issuance and net external financing are the strongest survivors**: post-publication LS returns are as large as or larger than in-sample in the original construction, and still significant in 2015-2025.

### Cited Findings
**Cross-literature benchmarks**
- McLean-Pontiff (JF 2016), 97 predictors: "Portfolio returns are 26% lower out-of-sample and 58% lower post-publication"; they estimate a 32% (58% minus 26%) decline from publication-informed trading. Declines are larger for predictors with higher in-sample returns, and returns stay higher in stocks with high idiosyncratic risk and low liquidity — [McLean & Pontiff 2016 (PDF)](https://Www.Gwern.net/doc/economics/2016-mclean.pdf)
- Chen-Zimmermann replicate McLean-Pontiff and find post-publication returns about 50% smaller than in-sample; their bias adjustment is too small to explain the decline, which points to mispricing that gets arbitraged — [CFR working paper summary](https://www.cfr-cologne.de/download/workingpaper/cfr-24-02.pdf) (as reported in search results; per-signal tables not seen)
- Chen-Zimmermann's open-source replication: 98% of the 180 clearly significant predictors reproduce with t > 1.96 in-sample; published in Critical Finance Review — [Chen & Zimmermann (CFR)](https://cfr.ivo-welch.info/published/papers/chen2021open.pdf)
- Chen-Zimmermann documentation (March 2022 update, as reported in search results): after redefining Accruals to include depreciation (closer to Sloan), the in-sample t-stat rose from 5.07 to 6.70 but the **post-publication t-stat fell from 2.37 to 1.43** — [openassetpricing.com](https://www.openassetpricing.com/) (snippet; primary doc page not read directly)
- Hou-Xue-Zhang ("Replicating Anomalies", NBER WP 23394; NYSE breakpoints, value-weighted, 1967-2014): 286 of 447 anomalies (64%) are insignificant at 5%, and 380 (85%) at |t| < 3. Sloan operating accruals and Cooper-Gulen-Schill asset growth are listed as "significant but much smaller than originally reported" — [HXZ NBER w23394](https://www.nber.org/papers/w23394.pdf)
  - Asset growth (I/A) high-minus-low decile: **-0.46%/mo (t = -2.92)**, against CGS's original -1.05% (t = -5.04) value-weighted and -1.73% (t = -8.45) equal-weighted — [HXZ](https://www.nber.org/papers/w23394.pdf)
  - Operating accruals (Oa): **-0.27%/mo (t = -2.13)**, against Sloan's -10.4% per year (t = -4.71). The q-factor model cannot explain it: alpha -0.54% (t = -3.77) — [HXZ](https://www.nber.org/papers/w23394.pdf)
  - Discretionary accruals: -0.36% (t = -2.73), q-alpha -0.64% (t = -4.37). Net operating assets (Noa): -0.40% (t = -2.94), q-alpha -0.41% (t = -2.24). Total accruals (Richardson et al. 2005): insignificant. Accrual quality (Francis et al.): -0.07% (t = -0.36) — [HXZ](https://www.nber.org/papers/w23394.pdf)
  - HXZ's main explanation for the gap between replications and originals is microcaps (about 3% of market cap) — [HXZ](https://www.nber.org/papers/w23394.pdf)
- Jensen-Kelly-Pedersen (JF 2023, "Is There a Replication Crisis in Finance?"): 153 factors in 93 countries, grouped into 13 themes. Most replicate and work out of sample. A secondary summary says the replication rate is above 75% in 10 of the 13 themes, with Seasonality, Leverage and Size the exceptions — [CBS research portal](https://research.cbs.dk/en/publications/is-there-a-replication-crisis-in-finance/); [Alpha Architect summary](https://alphaarchitect.com/is-there-a-replication-crisis-in-finance/). The theme-level numbers for the Investment, Accruals and Debt-issuance themes were not checked (see Gaps).
- Green-Hand-Soliman (Management Science 2011, "Going, Going, Gone? The Apparent Demise of the Accruals Anomaly"): US accrual-anomaly hedge returns decayed to no longer reliably positive after about 2000. They attribute the decline partly to more hedge-fund capital trading it and only weakly to a smaller accrual signal — [Penn State record](https://pure.psu.edu/en/publications/going-going-gone-the-apparent-demise-of-the-accruals-anomaly/); [RePEc](https://ideas.repec.org/a/inm/ormnsc/v57y2011i5p797-816.html)
- Another compilation dates the end of Sloan's anomaly's significant returns to about 2002, and links a later weakening to SFAS 146 (2003, accounting for restructuring charges) — [NYU Stern compilation](https://www.stern.nyu.edu/sites/default/files/assets/documents/con_033123.pdf)

**Original-paper statistics (Chen-Zimmermann SignalDoc, as recorded from the original papers)** — [SignalDoc.csv](https://raw.githubusercontent.com/OpenSourceAP/CrossSection/master/SignalDoc.csv)
- Accruals (Sloan 1996, 1962-1991): 0.87%/mo, t = 4.71, equal-weighted deciles, |price| > $5, 12-month hold
- PctAcc (Hafzalla-Lundholm-Van Winkle 2011, 1989-2008): 0.97%/mo, t ≈ 3.29 (size-adjusted); PctTotAcc 0.71%/mo
- NOA (Hirshleifer et al. 2004, 1964-2002): 1.48%/mo, t = 8.45 (size, B/M and momentum adjusted)
- AssetGrowth (Cooper-Gulen-Schill 2008, 1968-2003): 1.73%/mo, t = 8.45 equal-weighted ("also works VW, t = 5")
- InvestPPEInv (Lyandres-Sun-Zhang 2008): 0.57%/mo, t = 7.13. ChInv (Thomas-Zhang 2002): 0.95%/mo size-adjusted
- ShareIss1Y (Pontiff-Woodgate 2008, 1970-2003): t = 7.08 in univariate regressions. ShareIss5Y / CompEquIss (Daniel-Titman 2006): t = 4.39
- NetEquityFinance (Bradshaw-Richardson-Sloan 2006): 0.93%/mo, t = 3.82. XFIN (net external financing): 1.29%/mo, t = 5.7
- ShareRepurchase (Ikenberry-Lakonishok-Vermaelen 1995), coded by CZ as a binary flag `prstkc > 0`: 0.17%/mo, t = 1.85, rated "2_likely / 3_distant" replication quality (weak proxy for announcement-based buyback studies)

**[CZ-calc] Original construction (`PredictorPortsFull`): LS mean %/mo (t)**

| Signal | In-sample | Post-publication (to 2025-12) | 2000-2014 | 2015-2025 | 2020-2025 |
|---|---|---|---|---|---|
| Accruals (pub 1996) | 0.69 (6.69) | 0.09 (0.93) | 0.05 (0.34) | -0.07 (-0.44) | -0.01 (-0.05) |
| PctAcc (pub 2011) | 0.47 (3.59) | 0.02 (0.18) | 0.33 (2.62) | 0.00 (-0.01) | 0.09 (0.41) |
| PctTotAcc (pub 2011) | 0.51 (4.79) | 0.31 (2.18) | 0.22 (1.94) | 0.30 (1.68) | 0.57 (1.92) |
| NOA (pub 2004) | 1.10 (7.64) | 0.03 (0.13) | 0.76 (2.55) | -0.02 (-0.07) | -0.15 (-0.26) |
| AssetGrowth (pub 2008) | 1.51 (7.76) | 0.49 (1.94) | 1.11 (3.15) | 0.21 (0.67) | 0.51 (1.00) |
| InvestPPEInv (pub 2008) | 0.80 (7.95) | 0.19 (1.72) | 0.41 (2.56) | 0.02 (0.12) | -0.14 (-0.63) |
| ChInv (pub 2002) | 0.76 (6.26) | 0.20 (1.68) | 0.31 (1.98) | 0.08 (0.46) | 0.14 (0.53) |
| ShareIss1Y (pub 2008, quintiles) | 0.69 (4.21) | 0.94 (2.83) | 0.74 (2.11) | 1.31 (2.84) | 1.58 (2.06) |
| ShareIss5Y (pub 2006, quintiles) | 0.51 (3.37) | 0.87 (3.29) | 0.50 (1.54) | 1.12 (2.81) | 1.16 (1.78) |
| CompEquIss (pub 2006, quintiles) | 0.24 (2.31) | 0.31 (2.10) | 0.24 (1.44) | 0.25 (1.29) | 0.25 (0.83) |
| NetEquityFinance (pub 2006) | 0.68 (2.85) | 0.95 (2.83) | 0.81 (1.81) | 1.12 (2.15) | 1.21 (1.42) |
| XFIN (pub 2006) | 1.07 (4.66) | 1.23 (3.53) | 1.17 (2.41) | 1.63 (3.07) | 2.20 (2.65) |
| ShareRepurchase flag (pub 1995) | 0.30 (3.79) | 0.28 (2.32) | 0.20 (1.32) | 0.50 (2.10) | 0.58 (1.45) |

Annualized ≈ ×12 (e.g., Accruals in-sample 8.2%/yr becomes 1.1%/yr post-publication; AssetGrowth 18.1%/yr becomes 5.9%/yr; XFIN 12.8%/yr becomes 14.8%/yr; ShareIss1Y 8.3%/yr becomes 11.3%/yr).

### Inferences
- In the original equal-weighted construction the decay ranking is: accruals (Sloan), NOA and percent operating accruals (about 90-100% decay, t < 1 since 2015) > investment (AssetGrowth about 67% decay; InvestPPEInv about 75%; ChInv about 75%) > issuance/external financing (**no decay or an increase**). This is far worse than McLean-Pontiff's average 58% for the accrual group and far better for the issuance group.
- The accrual anomaly **did not recover** after the ~2000 decline: Sloan accruals give -0.07%/mo (t = -0.44) in 2015-2025 and about 0 in 2020-2025 in every construction tested. One variant that does not use the balance sheet, percent *total* accruals (PctTotAcc, built from cash-flow-statement items), shows +0.30%/mo (t = 1.68) in 2015-2025 in the original construction. But it is only 0.13 (t = 0.90) without microcaps and 0.03 (t = 0.13) value-weighted, so it is a microcap effect, not a recovery.
- Issuance survives probably because shorting heavy issuers (often small, high-volatility, hard-to-borrow growth names) is costly. McLean-Pontiff's finding that returns stay higher in high-idiosyncratic-risk, illiquid stocks fits this.

### Gaps
- The exact JKP theme results (t-stats/alphas for "Investment", "Accruals", "Debt issuance") were not extracted; the paper's theme figure was not read.
- The per-signal McLean-Pontiff table was not read; only the aggregate 26%/58% figures are verified.
- The Hou-Xue-Zhang numbers come from the 2017 NBER working paper (sample to 2014), not the 2020 RFS final version; the numbers for net share issuance (Nsi), composite issuance (Cei) and percent accruals (Poa/Pta) could not be read from the scrambled PDF tables.
- The original Ikenberry-Lakonishok-Vermaelen (1995) 4-year buyback-announcement drift (about 12% is the commonly cited figure) was **not verified** here. The CZ binary `prstkc > 0` proxy is not an announcement study.

## 2. Small/micro caps vs large caps, transaction costs, which side (long or short) drives returns

### Takeaway
For accruals, NOA and asset growth, almost all of the remaining post-publication return is in **microcaps and on the short side** (high accruals, high NOA, high asset growth). Value-weighted, asset growth is negative after publication (-0.17%/mo) and NOA is weak (0.27, t = 1.14). Issuance is the exception: without microcaps it falls to about 0.35%/mo (t ≈ 1.7-2.3), but value-weighted 5-year issuance (ShareIss5Y) still earns **0.73%/mo, t = 3.35 in 2015-2025**, mostly from the short side (heavy issuers underperform).

### Cited Findings
- HXZ: the key reason replications differ from originals is microcaps; with NYSE breakpoints and value-weighting, magnitudes shrink sharply (I/A -0.46 vs -1.73 EW original) — [HXZ NBER w23394](https://www.nber.org/papers/w23394.pdf)
- Cooper-Gulen-Schill report the asset-growth effect in both large and small caps; HXZ still finds it significant value-weighted to 2014 — [Alpha Architect on CGS](https://alphaarchitect.com/the-asset-growth-effect-in-stock-returns/); [HXZ](https://www.nber.org/papers/w23394.pdf)
- Competing explanations of asset growth: Fu (2014) says it is mostly delisting bias plus the external-financing anomaly; Lipson-Mortal-Schill say arbitrage costs allow mispricing to persist — [Cambridge JFQA, "On the scope and drivers of the asset growth effect"](https://resolve.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/on-the-scope-and-drivers-of-the-asset-growth-effect/B8CFCF68A82E273E979221EF26A77096) (as summarized in search results)
- McLean-Pontiff: post-publication returns are higher in portfolios concentrated in high-idiosyncratic-risk, low-liquidity stocks — [McLean & Pontiff 2016](https://Www.Gwern.net/doc/economics/2016-mclean.pdf)

**[CZ-calc] Post-publication LS by construction, %/mo (t), and 2015-2025**

| Signal | Original (mostly EW, incl. microcaps) post-pub | No microcaps (ME > NYSE 20%) post-pub | Value-weighted post-pub | No microcaps 2015-25 | VW 2015-25 |
|---|---|---|---|---|---|
| Accruals | 0.09 (0.93) | 0.12 (0.95) | 0.12 (0.63) | -0.05 (-0.25) | 0.04 (0.15) |
| PctAcc | 0.02 (0.18) | 0.02 (0.12) | 0.07 (0.33) | 0.05 (0.28) | 0.13 (0.53) |
| PctTotAcc | 0.31 (2.18) | 0.18 (1.49) | 0.03 (0.15) | 0.13 (0.90) | 0.03 (0.13) |
| NOA | 0.03 (0.13) | 0.18 (0.89) | 0.27 (1.14) | 0.38 (1.32) | 0.49 (1.42) |
| AssetGrowth | 0.49 (1.94) | 0.46 (2.27) | -0.17 (-0.61) | 0.37 (1.33) | -0.28 (-0.73) |
| InvestPPEInv | 0.19 (1.72) | 0.06 (0.54) | -0.28 (-1.66) | -0.06 (-0.43) | -0.44 (-1.94) |
| ShareIss1Y | 0.94 (2.83) | 0.35 (1.68) | 0.30 (1.49) | 0.37 (1.27) | 0.42 (1.53) |
| ShareIss5Y | 0.87 (3.29) | 0.36 (2.26) | 0.49 (3.14) | 0.36 (1.56) | **0.73 (3.35)** |
| CompEquIss | 0.31 (2.10) | 0.32 (1.78) | 0.54 (2.37) | 0.14 (0.63) | 0.52 (1.67) |
| NetEquityFinance | 0.95 (2.83) | 0.39 (1.61) | 0.26 (0.81) | 0.29 (0.80) | 0.24 (0.48) |
| XFIN | 1.23 (3.53) | 0.47 (1.67) | 0.51 (1.42) | 0.40 (0.90) | 0.64 (1.14) |
| ShareRepurchase flag | 0.28 (2.32) | 0.17 (1.79) | 0.13 (1.14) | 0.15 (1.01) | 0.16 (0.87) |

**[CZ-calc] Long vs short leg, post-publication, original construction (long-avg | avg-short, %/mo (t))**
- Accruals: long -0.07 (-0.76) | short **0.16 (2.39)** — only the short side (avoid/short high accruals) still works post-publication; 2015-2025 both legs about 0
- NOA: long -0.29 (-1.58) | short **0.32 (3.06)**; 2015-2025 short 0.37 (2.43), long -0.39 (-1.27). Value-weighted 2015-2025 short leg 0.37 (2.44)
- AssetGrowth: long -0.03 (-0.12) | short **0.52 (3.12)**; 2015-2025 short 0.60 (2.62), long -0.39. Value-weighted, both legs about 0 or negative
- InvestPPEInv: long 0.02 | short 0.17 (2.70); 2015-2025 about 0
- ShareIss1Y: long 0.18 (1.42) | short **0.76 (3.52)**; 2015-2025 short 1.03 (3.34)
- ShareIss5Y: long 0.21 (2.29) | short 0.66 (3.60); value-weighted 2015-2025 long 0.17 (1.61) | short **0.55 (3.57)**
- XFIN: long 0.28 (2.97) | short 0.95 (3.45); value-weighted post-pub long 0.31 (2.34) | short 0.20 (0.74) — under value weights the *long* side (net payers of capital: buybacks/dividends/debt paydown) carries more
- CompEquIss: value-weighted post-pub long 0.36 (2.32) | short 0.18 (1.70) — the long side (net repurchasers, low composite issuance) carries most of the value-weighted return
- PctTotAcc: long 0.30 (2.97) | short 0.01 — the only accrual-family signal where the long side (low/negative percent total accruals) carries the post-publication return, but it disappears without microcaps

### Inferences
- For a portfolio that can only hold longs (or shorts only rarely), the usable information is mostly **negative**: high asset growth, high NOA, high accruals and heavy net equity issuance mark stocks to avoid or underweight, not stocks to buy. The positive long-side signal that survives value-weighting is **net payout / low composite issuance** (CompEquIss, XFIN long legs, t ≈ 2.3).
- Transaction costs: these are annual-rebalance, low-turnover signals, so trading cost per year is small. The binding cost is shorting microcap heavy issuers (borrow fees, short-sale constraints). Equal-weighted LS numbers that rely on microcap shorts probably overstate what can be captured after costs. No cost-adjusted estimate specific to these signals was found (see Gaps).
- The leg decomposition is against the signal's own average decile, not the market or a factor model, so "short-side alpha" here means relative underperformance, not necessarily negative CAPM/FF alpha.

### Gaps
- No published net-of-transaction-cost estimate for these exact signals post-2015 was found (Novy-Marx-Velikov 2016 "A Taxonomy of Anomalies and Their Trading Costs" and Chen-Velikov 2023 "Zeroing in on the Expected Returns of Anomalies" are the relevant sources but were not read).
- The ShareRepurchase leg split is meaningless (binary signal, two portfolios), so it is not reported.

## 3. Performance 2015-2026

### Takeaway
2015-2025 (data to Dec 2025; nothing for 2026 is in the CZ release): accruals ≈ 0; NOA and AssetGrowth LS insignificant, with only their short legs alive in equal-weighted/microcap-inclusive versions; issuance/external financing strong (ShareIss1Y 1.31%/mo t = 2.84 EW; XFIN 1.63 t = 3.07 EW; ShareIss5Y VW 0.73 t = 3.35). The value-weighted investment effect (AssetGrowth VW -0.28, InvestPPEInv VW -0.44 t = -1.94) **reversed** in 2015-2025, matching the widely discussed weakness of the FF CMA factor during the growth/tech-led market.

### Cited Findings
- [CZ-calc] 2015-2025 and 2020-2025 numbers are in the tables above. Selected: Accruals EW -0.07 (-0.44); NOA EW -0.02 (-0.07); AssetGrowth EW 0.21 (0.67), VW -0.28 (-0.73); ShareIss1Y EW 1.31 (2.84); ShareIss5Y VW 0.73 (3.35); XFIN EW 1.63 (3.07), 2020-25 2.20 (2.65); NetEquityFinance EW 1.12 (2.15); CompEquIss VW 0.52 (1.67) — data: [OpenSourceAP/CrossSection (Oct 2026 release)](https://huggingface.co/datasets/OpenSourceAP/CrossSection); release info [openassetpricing.com](https://www.openassetpricing.com/)
- The Open Source AP October 2026 update moved to CRSP v2 (CIZ) and fixed bugs; the monthly portfolio files end 2025-12 — [openassetpricing.com](https://www.openassetpricing.com/)

### Inferences
- Over 2015-2025 the issuance signals have been the most dependable member of this family. Investment signals (asset growth, PPE+inventory) depend on the regime: they were strongly negative value-weighted when mega-cap high-investment firms led.
- 2020-2025 windows (72 months) give t-stats around 1-2 even for real effects, so they should not be read as decisive.

### Gaps
- Ken French CMA factor returns for 2015-2026 could not be downloaded (the Dartmouth server refused the automated request), so no CMA/q-factor I/A numbers for 2015-2026 are reported here. The researcher's VW asset-growth decile LS is a proxy, not the CMA factor.
- No 2026 data (the CZ portfolios end 2025-12).
- No published 2020-2026 replication specific to buyback announcements was found.

## 4. Exact inputs and computability from free SEC XBRL companyfacts; pitfalls

### Takeaway
All of these signals can in principle be built from companyfacts on an annual (10-K) basis. The definitions that use only the **cash-flow statement and totals** are the most robust from XBRL: percent total/operating accruals via NetIncomeLoss − NetCashProvidedByUsedInOperatingActivities; asset growth via Assets; net equity financing via ProceedsFromIssuanceOfCommonStock − PaymentsForRepurchaseOfCommonStock. Share-count signals (Pontiff-Woodgate) are the riskiest because of splits, scale errors and the difference between the cover-page date and the balance-sheet date.

### Cited Findings
- Exact academic definitions (Compustat items), from [SignalDoc.csv](https://raw.githubusercontent.com/OpenSourceAP/CrossSection/master/SignalDoc.csv):
  - Accruals (Sloan): ΔCurrent assets − ΔCash&STI − (ΔCurrent liabilities − ΔDebt in current liabilities − ΔIncome taxes payable) [− depreciation in the Sloan/HXZ version], ÷ average total assets; exclude price < $5
  - PctAcc: (Income before extraordinary items − operating cash flow) ÷ |IB| (if IB = 0 divide by 0.01); balance-sheet fallback if OANCF missing; price ≥ $5
  - PctTotAcc: NI − (repurchases − issuance + dividends + CFO + CFF + CFI), ÷ |NI|
  - NOA: [(Total assets − cash&STI) − (Total assets − LTD − minority interest − deferred charges − book equity)] ÷ lagged total assets
  - AssetGrowth: annual growth of total assets (AT)
  - InvestPPEInv: (ΔGross PPE + ΔInventory) ÷ lagged assets
  - ShareIss1Y: growth in split-adjusted shares between t−18 and t−6 months (CRSP shrout/cfacshr); ShareIss5Y: 5-year split-adjusted share growth
  - CompEquIss (Daniel-Titman): 5-year growth of market cap minus 5-year stock return
  - NetEquityFinance: (sale of common stock − purchase of common stock) ÷ average assets; XFIN adds −dividends + LT debt issuance − LT debt reduction, ÷ assets
  - ShareRepurchase: prstkc > 0 flag
- HXZ operating accruals: balance-sheet method before 1988, and (implicitly) the cash-flow-statement method from 1988 once the statement of cash flows exists — [HXZ appendix A.3.16](https://www.nber.org/papers/w23394.pdf)
- SEC staff reminder: dei:EntityCommonStockSharesOutstanding (cover page, as of a recent date) and the balance-sheet share count should not differ significantly unless later events explain it; the SEC has flagged tagging errors — [SEC OSD announcement](https://www.sec.gov/structureddata/announcement/osd-announcement-2210-dqreminder-entitycommonstocksharesoutstanding)
- SEC 2020 announcement on **scaling errors** in share and value facts — [SEC OSD scaling errors](https://www.sec.gov/newsroom/whats-new/osd-announcement-110520-scaling-errors)
- XBRL US rule DQC.US.0095 flags cover vs balance-sheet share counts differing in scale; example: 9,076,832 vs 9,076 (> 100x) — [XBRL US DQC_0095](https://xbrl.us/dqc_0095)
- Split handling in practice: one tool (edgartools) detects splits via StockSplitConversionRatio facts and multiplies pre-split share counts by the ratio; this is a design choice, not a standard — [edgartools split guide](https://edgartools.readthedocs.io/en/latest/guides/stock-splits-eps-normalization/)

### Inferences (researcher's own assessment, not from a cited source)
- **Asset growth**: `us-gaap:Assets` at consecutive fiscal year-ends (10-K, fp=FY); among the most reliable tags. Pitfalls: mergers and spin-offs (huge jumps), restated prior-year values (use the value as originally filed for point-in-time, the latest for clean data), fiscal-year-end changes.
- **Percent / operating accruals (cash-flow method)**: `NetIncomeLoss` (or `IncomeLossFromContinuingOperations...`) minus `NetCashProvidedByUsedInOperatingActivities`. Annual values are reliable; quarterly CFO in XBRL is year-to-date (the Q2 value covers 6 months), so quarterly values have to be differenced, and Q4 is implied (FY − 9M). Divide by |NI| for PctAcc with a floor (CZ uses 0.01 in $ millions-scale units; pick an equivalent dollar floor) and handle NI ≈ 0 explicitly. Some filers use `NetCashProvidedByUsedInOperatingActivitiesContinuingOperations` or `ProfitLoss` instead; a tag fallback chain is needed, and **missing must stay missing** (no zero-fill).
- **NOA**: needs `CashAndCashEquivalentsAtCarryingValue` (+ `ShortTermInvestments`/`MarketableSecuritiesCurrent`), `LongTermDebtNoncurrent`, `MinorityInterest`, `StockholdersEquity`; tag use varies widely across filers (debt has many variants such as `LongTermDebtNoncurrent`, `LongTermDebtAndCapitalLeaseObligations`, operating-lease liabilities after ASC 842 in 2019), so NOA is the noisiest accrual measure from XBRL.
- **Net equity financing / buybacks**: `ProceedsFromIssuanceOfCommonStock` and `PaymentsForRepurchaseOfCommonStock` are flows from the cash-flow statement and do not need split adjustment. Pitfalls: many filers do not report one or the other (absent ≠ 0 in principle, although economically often 0); employee-option proceeds sometimes go to `ProceedsFromStockOptionsExercised`; tax-withholding share buybacks (`PaymentsRelatedToTaxWithholdingForShareBasedCompensation`) are separate. This is a cleaner, split-free substitute for Pontiff-Woodgate share growth, and closely matches the CZ NetEquityFinance/XFIN definitions, which held up best post-2015.
- **Share issuance (Pontiff-Woodgate)** from XBRL: `dei:EntityCommonStockSharesOutstanding` is dated at the cover date (weeks after fiscal year-end) and covers all classes or one class depending on the filer; `us-gaap:CommonStockSharesOutstanding` is at the balance-sheet date; `WeightedAverageNumberOfDilutedSharesOutstanding` is a period average that includes dilution and is **retroactively restated for splits** in later filings while older filings are not. Mixing these tags or periods produces false "issuance" or "buyback" signals from splits, multi-class structures (e.g., Class A + B summed or not) and scale errors (thousands vs units, DQC_0095). Practical guard: compute share growth only from the **same tag, same filer, consistent class**, check for split events (StockSplitConversionRatio or a price-ratio check versus a market-data source), and reject |growth| beyond plausible bounds unless an explanation exists. Alternative: get split-adjusted shares from market data (e.g., the provider's split-adjusted shares) rather than XBRL.
- **Composite issuance (Daniel-Titman)** needs 5 years of market cap and total return, so it needs a price source, not XBRL. XBRL coverage starts around 2009-2011 (phased in by filer size), so 5-year signals are only available from about 2014-2016 onward from companyfacts alone.
- **Point-in-time**: use the `filed` date in companyfacts as the availability date (the classic papers assume a 4-6 month lag after fiscal year-end, e.g., CZ "Start Month 6"), so that look-ahead bias is avoided.

### Gaps
- No academic study was found that measures how closely XBRL-built versions of these signals match Compustat-built versions (correlation, coverage).
- The SEC documents were not read in full; the details above on tag variants (ASC 842 lease tags, tax-withholding repurchases, YTD quarterly cash flow) are from the researcher's own knowledge of US-GAAP taxonomy practice and should be checked against actual companyfacts samples before implementation.
