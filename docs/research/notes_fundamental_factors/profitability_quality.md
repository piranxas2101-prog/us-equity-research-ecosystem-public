# Profitability and quality factors for predicting US stock returns (1-12 month horizon)

Scope: gross profitability (Novy-Marx 2013), operating / cash-based operating profitability (Ball, Gerakos, Linnainmaa, Nikolaev 2016), Piotroski F-score, Quality-Minus-Junk (Asness, Frazzini, Pedersen), margin/earnings-change signals. In-sample (IS) and out-of-sample (OOS) numbers are kept apart. Research budget was limited (about 18 tool calls). Several primary PDFs could not be read (Novy-Marx-Velikov 2016, Ball et al. 2016, Alpha Architect returned 403), so some requested figures are listed as gaps instead of being filled from memory.

## 1. Original in-sample evidence vs. post-publication / replication evidence

### Takeaway
Of this group, cash-based operating profitability is the one that survives a strict replication best: Hou-Xue-Zhang, using value weights and NYSE breakpoints, get about 0.5-0.6%/month long-short with t of about 3-3.5 at 1, 6 and 12 months, and the q-factor model cannot explain it. Gross profitability scaled by current assets survives only weakly (0.38%/month, t=2.62), and it disappears when scaled by lagged assets. Fama-French operating profitability (Ope) and the classic distress/O-score/Z-score signals do not replicate. The F-score earns about 0.4-0.6%/month, but that is almost entirely ROE exposure. QMJ was strong in sample (1957-2016) but has had a roughly zero raw premium since 2017.

### Cited Findings
**General post-publication decay (all anomalies)**
- McLean-Pontiff (2016), 97 predictors: the average long-short return is 26% lower out of sample (the statistical-bias part) and 58% lower after publication, so about 32 percentage points of the drop is attributed to post-publication trading. Declines are larger for predictors with higher in-sample returns and for those cheaper to arbitrage. — [McLean & Pontiff 2016 (PDF)](https://Www.Gwern.net/doc/economics/2016-mclean.pdf); [ETF.com Swedroe summary](https://www.etf.com/node/94121.md)
- A follow-up on the Chen-Zimmermann replicated set (about 205 predictors) reports that gross anomaly returns decay about 50% after publication, and about 72% if post-publication data are limited to post-2005. — [search summary citing Chen-Zimmermann-based study](https://acfr.aut.ac.nz/__data/assets/pdf_file/0012/578748/Guy-Kaplanski-RACE9.pdf) (secondary, not verified line by line)
- Chen-Zimmermann's Bayesian bias correction is reported as implying only about a 12% drop attributable to publication bias in US data. — [arXiv 2105.01380 "Why and how systematic strategies decay"](https://arxiv.org/pdf/2105.01380) (secondary citation; a second search could not confirm the 12% figure from a primary source, see Gaps)
- Jensen-Kelly-Pedersen (JF 2023): baseline US replication rate about 56.9%; global replication rates are higher. They argue most factors replicate once a Bayesian framework and factor clustering are used. — [NBER w28432](https://www.nber.org/system/files/working_papers/w28432/w28432.pdf); [JF 2023](https://onlinelibrary.wiley.com/doi/10.1111/jofi.13249)
- Hou-Xue-Zhang (RFS 2020, 452 anomalies): 65% fail |t|>=1.96 with NYSE breakpoints and value-weighted returns (microcaps controlled); 82% fail at t>=2.78. — [NBER w23394](https://www.nber.org/papers/w23394.pdf); [RePEc RFS 33(5)](https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2019-2133..html)

**Gross profitability (GP/AT, Novy-Marx 2013)**
- IS claim: gross profits-to-assets "has roughly the same power as book-to-market" in predicting returns; profitable firms earn more despite higher valuation ratios. — [RePEc JFE 108(1)](https://econpapers.repec.org/RePEc:eee:jfinec:v:108:y:2013:i:1:p:1-28); [NBER w15940](https://nber.org/papers/w15940)
- International test in the original paper (19 developed markets, Jul 1990-Oct 2009): reported premium of 3.6%/yr in Europe and 4.4%/yr in the US. — [Alpha Architect summary](https://alphaarchitect.com/the-profitability-factor-international-evidence/)
- HXZ replication (VW, NYSE breakpoints, annual sort): high-minus-low GP/current assets (Gpa) decile 0.38%/month (t=2.62). Scaled by lagged assets (Gla), it falls to 0.16%/month (t=1.04). HXZ argue Gpa = Gla / asset growth, so the Gpa effect is confounded with the investment effect, and purging it leaves an "economically small and statistically insignificant" effect. — [HXZ NBER w23394, sec. 3.2.4](https://www.nber.org/papers/w23394.pdf)
- Branch-Goldberg-Leshem: over Jul 1995-Dec 2012, FF-Carhart does not replicate the GP strategy; a model with quality and momentum factors captures much of it (short history noted). — [SSRN 2369491](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2369491)

**Operating profitability / cash-based operating profitability (Ball et al. 2016)**
- Definition (as replicated by HXZ): Cop = REVT - COGS - XSGA + XRD (0 if missing) - ΔReceivables - ΔInventory - ΔPrepaid + ΔDeferred revenue + ΔAccounts payable + ΔAccrued expenses, all over total assets. Missing changes are set to 0 in HXZ. — [HXZ appendix A.4.18](https://www.nber.org/papers/w23394.pdf)
- IS result: removing accruals from operating profitability gives a significantly stronger predictor that subsumes the accrual anomaly. Adding only a cash-based OP factor raises the Sharpe ratio more than adding both accruals and accrual-inclusive profitability factors; predictability extends up to 10 years. — [Ball et al. 2016 JFE 121(1) via RePEc](https://econpapers.repec.org/RePEc:eee:jfinec:v:121:y:2016:i:1:p:28-45); [Alpha Architect summary](https://alphaarchitect.com/2016/01/value-investing-accruals-cash-flows-and-operating-profitability/)
- HXZ replication (VW, NYSE breakpoints): Cop high-minus-low 0.63%/month (t=3.44), q-factor alpha 0.69% (t=4.77). Cla (lagged assets) 0.53% (t=3.02), q-alpha 0.74% (t=4.89). Monthly sorts on Cla: 0.49% / 0.48% / 0.47% per month at 1 / 6 / 12-month holding (t=3.02 / 3.45 / 3.57); q-alphas 0.43 / 0.40 / 0.46% (t=2.69 / 2.82 / 3.56). This is among the few profitability anomalies the q-model cannot explain. — [HXZ sec. 4.3.1](https://www.nber.org/papers/w23394.pdf)
- HXZ: Ball et al.'s accrual-inclusive operating profits-to-assets (Opa) 0.37%/month (t=1.87), which drops to 0.20% (t=1.07) with lagged assets (Ola). Fama-French's RMW sorting variable (operating profits / book equity, Ope) is insignificant at 0.25%/month (t=1.2); Ole 0.07% (t=0.37). — [HXZ sec. 3.2.4](https://www.nber.org/papers/w23394.pdf)
- An independent replication confirms that in the US, cash-based OP subsumes accruals and operating profitability; in China the ranking reverses. — [Pacific-Basin Finance Journal replication (PolyU)](https://ira.lib.polyu.edu.hk/bitstream/10397/90052/1/PBFJ_Accrual_PolyU.pdf)
- Huber-Hanauer (2017 WP): internationally, cash-based gross profitability beats other profitability measures. — [EFMA 2017 WP](https://www.efmaefm.org/0efmameetings/efma%20annual%20meetings/2017-athens/phd/Huber%20D%20and%20Hanauer%20M%20-%20Dissecting%20Profitability%20(Working%20paper).pdf)

**ROE and changes in profitability (closest to "margin trends / earnings growth persistence")**
- HXZ: Roe (quarterly, monthly sort) high-minus-low 0.69%/month (t=3.07) at 1 month; at 6 months 0.42% (t=1.95); at 12 months 0.24% (t=1.19). The signal decays fast, so it is mainly a 1-6 month effect. — [HXZ sec. 3.2.4 and 4.3.1](https://www.nber.org/papers/w23394.pdf)
- HXZ: four-quarter change in ROE (dRoe) 0.76%/month (t=5.43) at 1 month, q-alpha 0.34% (t=2.29). HXZ read it as earnings seasonality; dRoe "likely better captures the underlying economic profitability than Roe itself". — [HXZ sec. 4.3.1](https://www.nber.org/papers/w23394.pdf)

**Piotroski F-score**
- IS: long high-F / short low-F among value stocks earned about 23%/yr over 1976-1996; Piotroski himself warned of data-snooping risk. — [Wikipedia: Piotroski F-score](https://en.wikipedia.org/wiki/Piotroski_F-score)
- HXZ (quarterly F-score Fq, VW, NYSE breakpoints, all stocks, not only value): 0.58% / 0.53% / 0.42% per month at 1 / 6 / 12 months (t=2.47 / 2.52 / 2.22). q-factor alphas are only 0.13 / 0.15 / 0.07% (t=0.58 / 0.86 / 0.49), and the ROE-factor loading is about 0.65-0.73 (t about 7). The F-score premium is real in raw terms but is essentially ROE/profitability exposure. — [HXZ sec. 4.2](https://www.nber.org/papers/w23394.pdf)
- HXZ definitions of the nine signals: ROA>0, CFO>0, ΔROA>0, CFO>ROA (accrual), Δleverage<0 (DLTT/avg assets), Δcurrent ratio>0, no common equity issuance, Δgross margin>0, Δasset turnover>0. — [HXZ appendix](https://www.nber.org/papers/w23394.pdf)
- Unverified secondary claim: the original long-high/short-low rules lost -9.53%/yr over the "last ten years" and -11.75%/yr over 20 years ("Why Piotroski's F-Score No Longer Works"). — reported in [quant-investing.com backtest PDF / search summary](https://www.quant-investing.com/media/tztosoes/backtest_utility_of_piotroski_f-score_for_predicting_growth-stock_returns_14.pdf). Treat as low reliability; it conflicts in sign with HXZ's positive full-sample Fq returns (different universe, value-only, different period).

**Distress-type "junk" signals**
- HXZ: Campbell-Hilscher-Szilagyi failure probability (Fp) high-minus-low -0.38%/month (t=-1.28), Jul 1976-Dec 2014, against 9.66%/yr originally (1981-2003). It replicates in the original sample (-0.82%, t=-2.1) but is 0.09%/month from 2003 onward. The O-score and Z-score are insignificant at every horizon (O: -0.06% to -0.36%, max |t|=1.57). — [HXZ sec. 3.2.4](https://www.nber.org/papers/w23394.pdf)

**Quality-Minus-Junk (QMJ)**
- IS (AFP, US Jul 1957-Dec 2016): raw 0.29%/month; CAPM alpha 0.39% (t=5.43); FF3 0.51% (t=8.90); FF3+UMD 0.60% (t=9.95); six-factor (incl. RMW, CMA) 0.33% (t=6.81). Global 1989-2016: raw 0.38%; four-factor 0.61% (t=8.07). Positive in 23 of 24 countries. RMW loading 0.55. — [Summitward analysis of AFP and AQR data](https://summitward.com/learn/quality-minus-junk); [AQR paper page](https://www.aqr.com/Insights/Research/Working-Paper/Quality-Minus-Junk)
- AQR current US file, raw long-short: Jul 1957-Dec 2016 mean 0.36%/month, vol 7.4%/yr, Sharpe 0.58, t=4.46, max DD -28%. Jan 2017-Jun 2026: mean 0.02%/month, vol 10.4%, Sharpe 0.02, t=0.05, max DD -36%. Including Jul 2026 (+14.3%): 0.14%/month, Sharpe 0.15, t=0.45. — [Summitward](https://summitward.com/learn/quality-minus-junk)
- Post-2016 US FF5+UMD regression: alpha 0.20%/month (t=1.35, not significant); RMW loading 0.86. Negative market beta cost about 0.30%/month. With a true Sharpe of 0.47-0.58, a 9.6-year stretch at Sharpe <=0.15 has about 9-16% probability. — [Summitward](https://summitward.com/learn/quality-minus-junk)
- Global QMJ: 0.47%/month (1989-2016) vs 0.23%/month (Jan 2017-Jul 2026), Sharpe 0.33 post-2016. — [Summitward](https://summitward.com/learn/quality-minus-junk)
- AQR datasets (daily and monthly QMJ factors, by country) are the primary source for any recomputation. — [AQR QMJ monthly](https://www.aqr.com/Insights/Datasets/Quality-Minus-Junk-Factors-Monthly)

### Inferences
- Ranking by robustness of OOS/replication evidence: (1) cash-based operating profitability, scaled by assets; (2) ROE and dRoe at short (1-6 month) horizons; (3) F-score, which works only as a profitability proxy; (4) GP/assets, whose significance depends on current-assets scaling, i.e., an investment confound; (5) Ope/RMW-type sort variable; (6) distress, O-score and Z-score, which are dead in VW tests.
- Applying the McLean-Pontiff haircut (-58% after publication) to IS spreads of about 0.4-0.6%/month gives a planning expectation of roughly 0.2-0.3%/month (about 2.5-3.5%/yr) gross long-short for the best profitability signals. This is an extrapolation, not a measured number.
- Composite quality (QMJ) did not protect against the 2017-2026 "junk rally" regime, so a single profitability signal should not be expected to deliver steady returns year to year.

### Gaps
- Exact Novy-Marx (2013) IS long-short numbers (VW monthly spread, t-stat, 1963-2010) were not retrieved from a primary source in this session.
- Ball et al. (2016) exact decile returns, alphas and Sharpe comparisons: the PDF fetch failed (ECONNRESET).
- Factor-level post-publication decay for GP, cash-OP, F-score and QMJ specifically (Chen-Zimmermann signal-level IS vs post-pub means) was not retrieved. openassetpricing.com has these per signal (GP, OperProf, CBOperProf, PS, etc.) and should be checked directly.
- JKP (jkpfactors.com) per-factor US OOS alphas for "profitability" and "quality" clusters were not retrieved.
- The "12% publication-bias" figure attributed to Chen-Zimmermann could not be confirmed from a primary source.

## 2. Small/micro caps vs large caps, and transaction costs

### Takeaway
Profitability signals are low-turnover (annual rebalance), which is why they survive trading costs better than most anomalies. Novy-Marx-Velikov name profitability, size and value as the highest-capacity strategies. Strict VW/NYSE-breakpoint replications (which down-weight microcaps) still find cash-OP significant, so the effect is not purely a microcap phenomenon. Distress and F-score style signals rely much more on small, illiquid shorts.

### Cited Findings
- Novy-Marx-Velikov (RFS 2016, 29(1):104-147): most anomalies with one-sided monthly turnover below 50% keep significant net spreads when designed to reduce costs; few higher-turnover strategies do. Size, value and profitability have the greatest capacity. Gross profitability (GP/AT, financials excluded, annual rebalance) is in the low-turnover group. — [NBER w20721](https://www.nber.org/papers/w20721.pdf); [RePEc](https://econpapers.repec.org/RePEc:nbr:nberwo:20721); [ETF.com Swedroe summary](https://www.etf.com/node/94299.md)
- Per one secondary summary of Novy-Marx-Velikov, adding profitability to a value+momentum combination raised the net excess return from 0.51 to 0.99 (%/month). — [search summary of NMV / Alpha Architect](https://alphaarchitect.com/trading-costs-destroy-factor-investing/) (page returned 403; figure is secondary)
- HXZ's replication framework: microcaps are below the NYSE 20th percentile of market cap, and NYSE breakpoints with value weights limit their influence. Cop/Cla stays significant under this design (see section 1). — [HXZ](https://www.nber.org/papers/w23394.pdf)
- F-score: Australian evidence finds the short-side alpha much larger than the long side and concentrated in small caps. A US growth-stock study says shorting low-F stocks is hard to implement and suggests buying high-F is more practical. — [Australian F-score study (ResearchGate)](https://www.researchgate.net/publication/301600517_The_Piotroski_F_-score_evidence_from_Australia); [quant-investing.com PDF](https://www.quant-investing.com/media/tztosoes/backtest_utility_of_piotroski_f-score_for_predicting_growth-stock_returns_14.pdf)
- Large-cap F-score test on the S&P 500 (long-only versus index), from a lower-authority source. — [Quanterlab](https://quanterlab.com/research/does-the-piotroski-f-score-have-anything-to-find-among-large-caps)
- QMJ is built within both small and big size groups. Small-value ETFs load positively on QMJ, e.g., AVUV 0.13 (t 2.1), DFSV 0.17 (t 2.7) over Nov 2019-Jul 2026, while IWN loads -0.03. — [Summitward](https://summitward.com/learn/quality-minus-junk)

### Inferences
- For a system that holds positions for 1-12 months, annual-refresh profitability signals add almost no turnover cost. The cost risk sits in the short leg (borrow, small illiquid junk names).
- Original F-score and distress results came from equal-weighted or small-value universes. Once microcaps are down-weighted, the extra return beyond ROE disappears (HXZ q-alpha about 0).

### Gaps
- Exact NMV net spreads for GP (gross vs net %/month, turnover %) were not retrieved; PDF text extraction failed in this session.
- No source found with a clean size-split (micro / small / big) table for GP or cash-OP post-2010.

## 3. Long side vs short side

### Takeaway
The evidence I found is thin and mostly indirect. For the F-score and distress-type signals, most of the alpha is on the short side (low-quality small firms). For broad profitability factors there is no clean split in what I retrieved. QMJ's recent losses were driven by the junk leg rallying.

### Cited Findings
- F-score: the short-side alpha is much higher than the long side, and small-cap alpha relies heavily on the short leg (Australia). — [ResearchGate](https://www.researchgate.net/publication/301600517_The_Piotroski_F_-score_evidence_from_Australia)
- QMJ 2023-2026: the losses coincided with "junk" rallies. The Goldman Sachs unprofitable-tech basket rose 57% in Jan-May 2026 vs 11% for the S&P 500. After June 2026 that basket fell 11% in Q3 while staples rose (P&G +12.9%, KO +17.3%). Only combined long-short figures are given. — [Toobit news, 2026-10-29, citing AQR and Bloomberg](https://www.toobit.com/en-US/news/quality-stocks-rebound-after-historic-junk-run)
- QMJ loads negatively on market beta, size and value. After 2016, negative beta cost about 0.30%/month. — [Summitward](https://summitward.com/learn/quality-minus-junk)

### Inferences
- In a research-and-trade system, a low-profitability, low-F, unprofitable-growth flag is most informative as a short or avoid signal. It also carries the highest squeeze and drawdown risk in speculative rallies (2020-21, 2023-H1 2026). A high-profitability flag is a weaker but steadier long tilt.

### Gaps
- No primary decomposition of long-leg vs short-leg returns (relative to market) found for GP, cash-OP or QMJ in the US after 2010. Chen-Zimmermann portfolio files (decile returns per signal) would allow computing this directly.

## 4. Performance 2015-2026 (recent drawdowns, quality 2022-2025)

### Takeaway
From 2017 to mid-2026 US QMJ was essentially flat with a -36% max drawdown (Nov 2023-Jun 2026), described as the deepest and longest in its 69-year history. It then had a record +14.3% month in July 2026. FF RMW held up better than QMJ in 2022-2024 but lost about 10% in 2025.

### Cited Findings
- QMJ (US): lost money in 5 of 9 calendar years 2017-2025; 2020 was the worst year and 2022 the best. — [Summitward](https://summitward.com/learn/quality-minus-junk)
- QMJ: losses in 2023, 2024 and 2025; -36% from Nov 2023 to Jun 2026; the 12 months to May 2026 were the weakest in the dataset; -3.8% in Jan-Aug 2026 despite July's +14.3% (the largest monthly gain since 1957; prior records were Nov 2000 and Oct 2008). Long run (Jul 1957-Aug 2026): quality beat junk by 3.6%/yr. Eight drawdowns over 10% since 1957, three tied to tech enthusiasm. In the two years after those three, quality beat junk by 28%, 68% and 41%. — [Toobit news 2026-10-29 citing AQR data](https://www.toobit.com/en-US/news/quality-stocks-rebound-after-historic-junk-run) (news-style site, not verified against the AQR file)
- FF RMW (2x3), uncompounded sums of monthly returns: about +8.6% (2022), +4.3% (2023), +4.1% (2024), -10.3% (2025); worst month Oct 2025 at -5.24%. — [YCharts RMW series](https://ycharts.com/indicators/fama_french_monthly_5_factor_rmw_benchmark_return); primary: [Ken French data library](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/f-f_5_factors_2x3.html)
- RMW Nov 1990-Aug 2025: about 3.37%/yr annualized, 4.75% vol (secondary table). — [daytrading.com factor statistics](https://www.daytrading.com/factor-statistics)
- Early 2022 assessment: RMW the single factor that "consistently delivered" excess returns (pre-2025). — [CFA Institute Enterprising Investor](https://blogs.cfainstitute.org/investor/2022/01/10/fama-and-french-the-five-factor-model-revisited)

### Inferences
- The two sources disagree on when quality pays: Summitward says 2022 was QMJ's best year, while RMW sums are positive 2022-2024 and QMJ is negative in 2023-2024. The cause is that QMJ includes safety/low-beta and growth components that suffered in the 2023-2025 rally, while RMW is a pure operating-profitability sort.
- The base rate after past junk rallies (strong 2-year quality recovery) is suggestive but rests on only 3 episodes.

### Gaps
- Exact calendar-year QMJ returns 2015-2025, and RMW compounded annual returns and drawdown, were not computed. Download the AQR QMJ monthly file and the Ken French 5-factor file to compute them.
- No 2015-2026 performance found specifically for GP/AT or cash-OP long-short portfolios (Chen-Zimmermann data are updated annually and could give this).

## 5. Required inputs and availability in SEC XBRL companyfacts

### Takeaway
All five factor families can be built from 10-K/10-Q data, but the fragile inputs are cost of revenue (needed for GP, gross margin and the F-score margin signal) and the working-capital change items for cash-OP. Banks, insurers and many service firms do not report a COGS line. Tags vary across filers, and some filers use custom extension tags.

### Cited Findings
- Compustat inputs used in replications: GP/AT = (REVT - COGS)/AT. Cash-OP = REVT - COGS - XSGA + XRD - ΔRECT - ΔINVT - ΔXPP + Δ(DRC+DRLT) + ΔAP + ΔXACC, over AT. F-score uses IB, OANCF, AT, DLTT, ACT, LCT, SSTK/PSTK, gross margin and asset turnover. — [HXZ appendix A.4](https://www.nber.org/papers/w23394.pdf)
- Novy-Marx-Velikov's GP signal excludes financial firms. — [NMV signals detail](https://mysimon.rochester.edu/novy-marx/data_lib/ToAatTC/signals_details.pdf)
- XBRL derived gross profit: some data providers compute gross profit when it is not reported and leave it null when no cost-of-revenue fact exists. — [stocks.cafe gross profit metric](https://www.stocks.cafe/metric/3007/gross+profit)
- Tag imputation from calculation linkbases varies by linkbase, and filers need not use tags from the same calculation linkbase. — [AsFiled methodology](https://sites.google.com/view/asfiled/methodology/additional-details-about-the-taxonomy-and-our-algorithm)
- SEC staff: some banks tag noninterest-income line items with custom (extension) concepts instead of us-gaap elements. — [SEC: Common noninterest income tagging errors](https://www.sec.gov/about/common-noninterest-income-tagging-errors)
- Some banks' XBRL reports contain contradictory facts. — [XBRL conflicting facts analysis (2016)](https://xbrl.squarespace.com/journal/2016/4/10/identifying-conflicting-facts-in-xbrl-based-public-company-f.html)
- The SEC Financial Statement Data Sets show which tag each filer actually used. — [SEC FSDS documentation](https://www.sec.gov/files/financial-statement-data-sets.pdf)

### Inferences
These are mappings from my own knowledge of the us-gaap taxonomy. They were not verified against companyfacts in this session; checking them against the project's companyfacts cache is advised.
- Revenue: `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax` (post-ASC 606, 2018+), `RevenueFromContractWithCustomerIncludingAssessedTax`, `SalesRevenueNet` (pre-2018). A fallback chain is needed.
- COGS: `CostOfRevenue`, `CostOfGoodsAndServicesSold`, `CostOfGoodsSold` (older), `CostOfServices`. Direct gross profit: `GrossProfit`. Banks and insurers usually have none of these, so GP should be "unknown/not applicable", not 0, consistent with the repo's unknown-stays-unknown rule.
- Total assets: `Assets`. SG&A: `SellingGeneralAndAdministrativeExpense` (sometimes split into `GeneralAndAdministrativeExpense` + `SellingAndMarketingExpense`). R&D: `ResearchAndDevelopmentExpense`.
- CFO: `NetCashProvidedByUsedInOperatingActivities` (some filers use `...ContinuingOperations`). Net income: `NetIncomeLoss` / `IncomeLossFromContinuingOperations...`.
- Cash-OP working capital: `IncreaseDecreaseInAccountsReceivable`, `IncreaseDecreaseInInventories`, `IncreaseDecreaseInPrepaidExpense...`, `IncreaseDecreaseInAccountsPayable`, `IncreaseDecreaseInAccruedLiabilities`, `IncreaseDecreaseInContractWithCustomerLiability` (deferred revenue). Using the cash-flow-statement change lines avoids balance-sheet differencing but has sign conventions and frequent omissions. HXZ set missing changes to 0, which conflicts with this repo's invariant; an explicit unknown or partial flag is better.
- F-score: `LongTermDebtNoncurrent`, `AssetsCurrent`, `LiabilitiesCurrent`, `ProceedsFromIssuanceOfCommonStock`, plus the COGS and revenue tags above.
- companyfacts pitfalls: 10-Q cash-flow values are year-to-date, so quarterly figures must be derived by differencing. Restated values appear under the same period with different `filed`/`fy`; use the first-filed value for point-in-time backtests. Duplicate facts across frames.

### Gaps
- No authoritative source retrieved giving the share of US filers missing `CostOfRevenue`/`GrossProfit` in companyfacts by sector. This is measurable locally from the repo's companyfacts store.
