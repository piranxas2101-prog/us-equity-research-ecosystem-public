# Distress signals, combining fundamental signals, replication/decay, and implementation (US equities, 1-12 month horizon)

Scope note: in-sample (IS) = the original paper's sample; out-of-sample (OOS) = post-sample and/or post-publication. Primary sources were read as full text where marked "(full text read)"; the rest come from abstracts or search summaries and are flagged.

## Q1. Do distressed stocks still underperform out of sample? Is it mainly a short-side / small-cap effect?

### Takeaway
Under a microcap-controlled, value-weighted design (NYSE breakpoints), the distress anomaly mostly disappears. The CHS failure-probability spread is significant only in the original 1981-2003 window and at a 6-month horizon, and it is about zero after 2003. O-score and Z-score spreads are insignificant at every horizon. Whatever distress premium exists sits in small/microcap, hard-to-short stocks, so it is mainly a short-leg, small-cap effect, and it is hard to harvest net of costs.

### Cited Findings
- Hou-Xue-Zhang replication (NBER w23394 / RFS 2020), NYSE breakpoints, value-weighted deciles: "The distress anomaly is virtually nonexistent in our replication." CHS failure probability, O-score, Z-score (Dichev 1998) and credit ratings give mostly insignificant spreads. — [Hou, Xue, Zhang, NBER w23394 (full text read)](https://www.nber.org/papers/w23394.pdf)
- CHS failure probability (Fp), annual sorts, July 1976-Dec 2014: high-minus-low = -0.38%/month (t = -1.28), against 9.66%/yr reported by CHS for 1981-2003. In the CHS window the replication gives -0.82%/month (t = -2.1). Outside it the spread is +0.69%/month (1976-1980) and +0.09%/month from 2003 on. — [HXZ w23394 (full text read)](https://www.nber.org/papers/w23394.pdf)
- Fp in monthly sorts: -0.48%/month at the 1-month horizon and -0.36%/month at 12 months, both within 1.5 SE of zero. At 6 months it is -0.63%/month (t = -2.03), the only significant horizon. — [HXZ w23394 (full text read)](https://www.nber.org/papers/w23394.pdf)
- Ohlson O-score high-minus-low ranges from -0.06% (t = -0.3) to -0.36%/month (t = -1.57). Altman Z ranges from +0.01% (t = 0.06) to -0.09% (t = -0.46). None is significant in annual or monthly sorts at any horizon. Dichev (1998) had reported 1.17%/month (t = 3.36) for O-score using NYSE-Amex-NASDAQ breakpoints and equal weights. — [HXZ w23394 (full text read)](https://www.nber.org/papers/w23394.pdf)
- CHS (2008) say the distress effect is particularly strong among smaller stocks, and HXZ attribute the replication gap mainly to microcaps. Microcaps are 61% of stocks (about 2,406 of 3,938) but 3.28% of market cap. They have the largest cross-sectional dispersion in returns (19.1% monthly SD, against 8.9% for big stocks). With NYSE-Amex-NASDAQ breakpoints they make up more than 60% of extreme deciles. — [HXZ w23394 (full text read)](https://www.nber.org/papers/w23394.pdf); [CHS NBER w12362](https://www.nber.org/papers/w12362)
- Across 38 countries the distress anomaly is concentrated in developed markets, and in some cases small distressed stocks outperform. — [Gao, Parsons, Shen (2017)](https://www3.nd.edu/%7Epgao/papers/GPS_20160927.pdf) (search summary only)
- The 2015-2026 short-sale cost evidence covers anomalies in general: across 162 anomalies, average long-short returns of about 0.14-0.15%/month come from the short leg and fall to about -0.02%/month net of borrow fees. Excluding high-fee stock-dates (12% of observations) makes anomalies unprofitable even before fees. — [Muravyev, Pearson, Pollet, "Anomalies and Their Short-Sale Costs" (J. Finance, DOI 10.1111/jofi.13501)](https://experts.illinois.edu/en/publications/anomalies-and-their-short-sale-costs/); [summary](https://www.cxoadvisory.com/short-selling/shorting-costs-kill-stock-return-anomalies)

### Inferences
- The surviving evidence is the IS 1981-2003 period plus a weak 6-month-horizon effect. Treat distress (Fp, O, Z) as a risk filter or veto, for example "do not hold long" or "size the position down", rather than a standalone alpha source. That fits an evidence-first system: high distress means an elevated chance of extreme left-tail outcomes, even though the average return spread is unreliable.
- Shorting the most distressed names is the textbook expression of the anomaly. It is also where borrow fees, recalls and squeeze risk concentrate, so a modest-capital short book should not lean on distress shorts.
- Fp builds in market data (price, volatility, past returns, market cap). Part of its signal therefore overlaps with momentum and low-volatility effects rather than being purely fundamental.

### Gaps
- I found no 2020-2026 paper that re-tests CHS/O/Z specifically on post-2014 US data with borrow fees. HXZ's sample ends in 2014.
- No direct per-anomaly borrow-fee-adjusted figure for distress was found in the Muravyev-Pearson-Pollet summaries.
- Open Source Asset Pricing (Chen-Zimmermann) post-publication returns for its distress predictors were not extracted.

## Q2. Do composite scores beat single factors out of sample? Equal-weight ranks or fitted weights?

### Takeaway
Combining signals is the main way to get net-of-cost value from fundamentals. Composites diversify noise and cut turnover and costs, and ML combinations show strong OOS Sharpe ratios gross of costs. Simple equal-weight rank averaging is hard to beat once estimation error and data mining are accounted for. Individual composites like the F-score are often explained by known factors (profitability/ROE).

### Cited Findings
- Stambaugh-Yuan "Mispricing Factors" (RFS 2017) average a stock's rankings across 11 prominent anomalies into two clusters, MGMT and PERF. A three-factor model with one composite mispricing factor, averaging rankings across all 11, also does well, especially in Bayesian model comparisons. — [Stambaugh & Yuan, NBER w21533](https://nber.org/system/files/working_papers/w21533/w21533.pdf); [econpapers](https://econpapers.repec.org/RePEc:oup:rfinst:v:30:y:2017:i:4:p:1270-1315.)
- Investor sentiment predicts the mispricing factors mainly through their short legs. — [Stambaugh & Yuan w21533](https://nber.org/system/files/working_papers/w21533/w21533.pdf)
- Piotroski F-score in HXZ replication (quarterly Fq, NYSE breakpoints, value-weighted): high-minus-low = 0.58, 0.53 and 0.42%/month at 1, 6 and 12 months (t = 2.47, 2.52, 2.22). It survives as a raw spread, but q-factor alphas are only 0.13, 0.15 and 0.07% (t < 1), explained mostly by the ROE factor. — [HXZ w23394 (full text read)](https://www.nber.org/papers/w23394.pdf)
- Gu-Kelly-Xiu (RFS 2020): a value-weighted long-short decile spread on neural-network forecasts reaches an OOS annualized Sharpe of 1.35. A secondary summary reports an equal-weighted 2.45, which tilts heavily to microcaps, and a linear-model value-weighted Sharpe of about 0.61. The same summary gives OOS monthly R² of about 0.26% for penalized linear models and 0.33-0.40% for trees and neural networks. — [GKX NBER w25398](https://www.nber.org/papers/w25398.pdf); [Mirova summary (secondary, unverified against tables)](https://www.research-center.mirova.com/en/research-library/Empirical-Asset-Pricing-via-Machine-Learning)
- Chen-Velikov: "Combining anomalies can improve portfolio performance, particularly when accounting for trading costs." They cite DeMiguel et al., who find powerful cost effects from combining 50 anomalies in the long sample. Chen-Velikov themselves do not test combinations. — [Chen & Velikov, FEDS 2020-039 (full text read)](https://www.federalreserve.gov/econres/feds/files/2020039pap.pdf)
- Selecting anomalies OOS by their IS net Sharpe ratio works best: the top quartile earns 21 bps/month net in post-publication, post-2005 data, but only with equal-weighted implementations. — [Chen & Velikov (full text read)](https://www.federalreserve.gov/econres/feds/files/2020039pap.pdf)
- Equal-weight combinations of anomaly portfolios slightly beat mean-variance-optimized combinations on expected Sharpe in one study. This is ex-ante evidence, not a realized OOS test. — [CXO summary](https://www.cxoadvisory.com/size-effect/bottom-up-anomalies-and-top-down-portfolio-efficiency) (secondary)

### Inferences
- For a small system with on the order of 10 signals and a short history: rank-normalize each signal cross-sectionally, sign it by prior evidence, and take an equal-weighted average. This is the Stambaugh-Yuan construction. Fitting weights on your own short history mostly fits noise, because the GKX OOS R² is below 0.5% per month even with huge data.
- Include explicit unknown handling: average only over available signals and report coverage, rather than imputing zero. That fits the repo's "unknown stays unknown" rule.
- ML gains are largest in equal-weighted, microcap-heavy portfolios (2.45 against 1.35 Sharpe), the segment where costs are highest.

### Gaps
- I did not retrieve QMJ (Asness-Frazzini-Pedersen) OOS numbers or a 2020-2026 head-to-head test of equal-weight against fitted-weight composites net of costs. The literature exists (DeMiguel et al., Jensen-Kelly-Malamud-Pedersen "implementable efficient frontier"), but I did not verify figures.
- Stambaugh-Yu-Yuan (2012/2015) long-leg against short-leg return numbers for the 11-anomaly combination were not extracted. Only the qualitative finding (short legs drive it) is sourced.

## Q3. Factor zoo, replication, and post-publication decay: how much survives?

### Takeaway
Most published anomalies reproduce on their own original methods (Chen-Zimmermann, about 100%), and about 82% replicate in a Bayesian global test (JKP). Under value-weighted, microcap-controlled designs only about 36% are significant (HXZ). Returns decay by roughly 35-50% after publication, and by more after 2005. Net of effective spreads, the average anomaly's expected return post-publication and post-2005 is about 8 bps/month (4 bps value-weighted), close to zero.

### Cited Findings
- Harvey-Liu-Zhu (RFS 2016) catalog about 316 factors and argue a new factor needs t > 3.0 rather than 2.0 because of multiple testing. — [NBER w20592](https://nber.org/papers/w20592); [CXO summary](https://www.cxoadvisory.com/big-ideas/taming-the-factor-zoo/)
- HXZ: of 447 anomaly variables, 286 (64%) are insignificant at 5% with NYSE breakpoints and value weights, and 380 (85%) fail a t ≥ 3 hurdle. Within trading frictions/liquidity, 95 of 102 (93%) are insignificant. Of the 161 significant anomalies, the q-factor model leaves 115 alphas insignificant. — [HXZ w23394 (full text read)](https://www.nber.org/papers/w23394.pdf)
- Jensen-Kelly-Pedersen (J. Finance 2023): a Bayesian test on 153 factors across 93 countries finds about 82% replicable. The US frequentist baseline is about 56%, and most factors hold up out of sample and internationally. Critics (Lopez de Prado-Fabozzi) argue this depends on modeling the search process. — [NBER w28432](https://nber.org/system/files/working_papers/w28432/w28432.pdf); [Alpha Architect](https://alphaarchitect.com/is-there-a-replication-crisis-in-finance/); [Substack critique](https://systematicallybiased.substack.com/p/pockets-of-replicability-post5)
- Chen-Zimmermann open source asset pricing: nearly 100% of predictability results reproduce. Replicated predictors average 0.72%/month IS (average t = 4.3). — [Chen & Zimmermann, CFR](https://cfr.ivo-welch.info/published/papers/chen2021open.pdf); [Chen & Velikov (full text read)](https://www.federalreserve.gov/econres/feds/files/2020039pap.pdf)
- Chen-Zimmermann publication-bias estimate: bias-adjusted returns are only 12.3% below IS, much smaller than post-publication decay, which points to mispricing that gets arbitraged. — [Chen & Zimmermann, FEDS 2018-033](https://www.federalreserve.gov/econres/feds/files/2018033pap.pdf) (search summary)
- McLean-Pontiff working paper (82 characteristics, 72 replicable): out-of-sample, pre-publication decay is about 10% and not significant. Post-publication decay is about 35% and significant, so an IS alpha of 5% becomes about 3.25%. In raw extreme-quintile terms, the IS long-short return is 42.8 bps/month and falls 17.3 bps after publication. Decay is largest for characteristics cheaper to arbitrage (big, liquid, low-idiosyncratic-risk, dividend-paying stocks). Relative shorting (short-leg minus long-leg short interest) rises about nine-fold after publication. — [McLean & Pontiff working paper (full text read)](https://ivey.uwo.ca/media/3775549/pontiff.pdf). The published J. Finance 2016 version covers 97 predictors. — [vLex listing](https://law-journals-books.vlex.com/vid/does-academic-research-destroy-855658380)
- Chen-Velikov (120 anomalies, JFQA 2023): IS gross 66 bps/month falls to 38 bps net of effective spreads, 13 bps net post-publication and 8 bps net post-publication and post-2005 (cost-optimized, equal-weighted). Value-weighted, the figure is 4 bps (SE 3 bps). The naive academic implementation gives about -3 bps net going forward. Only 9% of post-2005 net t-stats exceed |2|, close to the 5% expected under the null. After a data-mining adjustment, the 90th-percentile anomaly is about 20 bps/month, or 6 bps if value-weighted. — [Chen & Velikov (full text read)](https://www.federalreserve.gov/econres/feds/files/2020039pap.pdf); [JFQA version](https://core-prod.cambridgecore.org/core/services/aop-cambridge-core/content/view/945133D5A3ECEEAF466AEE91551FD225/S0022109022000874a.pdf/zeroing-in-on-the-expected-returns-of-anomalies.pdf)
- Gross decay is about 50% post-publication, or 72% if post-publication data is limited to post-2005. — [search summary of Chen-Velikov](https://www.federalreserve.gov/econres/feds/files/2020039pap.pdf)

### Inferences
- Planning number for a single published fundamental anomaly applied today in liquid stocks: assume an expected net long-short return near 0-10 bps/month. Edge has to come from combining signals, controlling costs and timing at the 1-12 month horizon, not from any one factor.
- Use t ≥ 3 (HLZ) as the bar for promoting any locally discovered signal. That fits the repo's candidate, shadow and manual-champion flow.

### Gaps
- I could not verify the commonly cited published McLean-Pontiff figures (26% OOS, 58% post-publication). Only the working-paper figures (10% and 35%) were confirmed from full text.
- The final JKP (2023) journal numbers were not checked; 82% is from the working paper and summaries.

## Q4. How much survives realistic costs and short constraints (2015-2026)?

### Takeaway
After decimalization, spreads are small for typical stocks, but anomaly portfolios load on the high-spread right tail: about 67 bps paid on average in 2014, against 16 bps for the average NYSE stock. Low-turnover anomalies (under about 50%/month) with buy/hold bands can survive costs. Size, value and momentum survive at institutional scale in AQR live-trade data. Borrow fees remove most of the average anomaly's short-leg alpha.

### Cited Findings
- Novy-Marx-Velikov (RFS 2016): most anomalies with under 50% monthly turnover still earn significant net spreads when designed to cut costs, and few with higher turnover do. A buy/hold spread (a stricter rule to enter than to keep) is the most effective simple cost reducer. Size, value and profitability have the most capacity. — [NBER w20721](https://www.nber.org/papers/w20721); [Rochester PDF](https://mysimon.rochester.edu/novy-marx/research/ToAatTC.pdf)
- NV's Hasbrouck low-frequency spreads overstate costs by 25-50 bps after 2003, against high-frequency TAQ effective spreads. — [Chen & Velikov (full text read)](https://www.federalreserve.gov/econres/feds/files/2020039pap.pdf)
- A typical anomaly turns over 15% of its long and 15% of its short book monthly (30% two-sided). Anomaly portfolios paid a 67 bps mean effective spread in 2014, against a 16 bps NYSE average and a 5 bps mode. About 20% of NYSE stocks have much larger spreads. Optimization (buy/hold) cuts turnover by 35% and spreads paid by 38%. — [Chen & Velikov (full text read)](https://www.federalreserve.gov/econres/feds/files/2020039pap.pdf)
- Frazzini-Israel-Moskowitz, using AQR live executions (about $1 trillion in 1998-2011 across 19 developed markets in the 2012 draft): US break-even fund sizes are about $103B for size, $83B for value and $52B for momentum. Short-term reversal does not survive costs at scale. — [AQR working paper](https://www.aqr.com/Insights/Research/Working-Paper/Trading-Costs-of-Asset-Pricing-Anomalies); [NBER conf draft](https://conference.nber.org/conf_papers/f68262/f68262.pdf). Chen-Velikov note size, value and momentum earned 30-70 bps net in FIM's 1998-2013 sample, but individual anomaly returns are sample-sensitive. — [Chen & Velikov](https://www.federalreserve.gov/econres/feds/files/2020039pap.pdf)
- Borrow fees (data from July 2006; 83% of anomalies discovered before 2006, so the test is largely OOS): average anomaly 0.14-0.15%/month gross, about -0.02% net of fees. Profits concentrate in about 12% of high-fee stock-dates. — [Muravyev-Pearson-Pollet](https://experts.illinois.edu/en/publications/anomalies-and-their-short-sale-costs/); [CXO](https://www.cxoadvisory.com/short-selling/shorting-costs-kill-stock-return-anomalies)
- Counterpoint: Da, Fu, Lin and Lu (2025) find anomalies remain exploitable net of fees among stocks with high short-selling profitability. A separate AEA paper finds short-selling returns net of borrow costs below one-third of gross. — [Da et al. SSP](https://www3.nd.edu/~zda/SSP.pdf); [AEA 2018 paper](https://swlb1.aeaweb.org/conference/2018/preliminary/paper/EKFHFi58)
- Stambaugh-Yu-Yuan (JFE 2012): across 11 anomalies, the short leg of each strategy is more profitable after high sentiment, while sentiment has no relation to long-leg returns. This is consistent with overpricing persisting because of short-sale impediments. Kim-Na (2018) find macro variables explain as much as the Baker-Wurgler sentiment index. — [AQR Insight Award summary](https://www.aqr.com/Our-Firm/AQR-Insight-Award/2012/Honorable-Mention-2); [NBER w16898](https://econpapers.repec.org/RePEc:nbr:nberwo:16898); [Kim-Na via vLex](https://law-journals-books.vlex.com/vid/investor-sentiment-anomalies-and-855592950)

### Inferences
- For a modest-capital long/short book:
  - Restrict to stocks above about the NYSE 20th size percentile, or to an explicit liquidity and spread floor.
  - Use buy/hold hysteresis bands.
  - Prefer low-turnover signals (quarterly or annual fundamentals with 3-12 month holds).
  - Make shorts conditional on borrow availability and fee: exclude hard-to-borrow names, which is where most of the gross anomaly alpha lives.
- Expect the long leg to carry most of the net alpha you can actually capture. Use the short leg more as a hedge or risk control than as an alpha engine, especially for distress shorts.
- Small capital helps a little, because market impact is negligible. It does not remove spreads or borrow fees.

### Gaps
- I found no source with 2015-2026 net-of-cost-and-borrow returns specifically for composite (multi-signal) long/short portfolios in US mid/small caps.
- Exact FIM published-version figures ($1.7T, 19 years) were not verified.

## Q5. Point-in-time data rules for SEC XBRL

### Takeaway
Key everything on the filing acceptance date (`filed`), not the fiscal period end. Keep the originally reported values; do not use restated or "latest" values for history. Handle 10-K/A and 10-Q/A amendments, tag changes and year-to-date quarterly cash flows. The SEC `frames` endpoint returns the last-filed fact per entity, so it is not point-in-time. The academic convention with Compustat is a lag of at least 4 months from fiscal quarter end for non-earnings items, and 6 months (annual sorts formed in June on prior-year fiscal data) for annual items.

### Cited Findings
- SEC: frames "aggregates one fact for each reporting entity that is last filed" that best fits the requested period. XBRL APIs update in real time (typically under a minute of processing), and bulk ZIPs are republished nightly at about 3:00 a.m. ET. — [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- HXZ convention: for monthly sorts on quarterly items other than earnings, impose a four-month lag from fiscal quarter end. Non-earnings quarterly items are often unavailable at the earnings announcement, because firms announce by press release and file several weeks later. Earnings items are used from the announcement date (Compustat RDQ). Annual items are sorted at the end of June on the fiscal year ending in calendar year t-1. — [HXZ w23394 (full text read)](https://www.nber.org/papers/w23394.pdf)
- Practitioner guidance (vendor sources with a commercial interest):
  - Filter on first-filed date; joining on period end leaks data weeks or months early. One vendor measured an average of about 43 days from period end to first filing.
  - Restatements arrive as new filings for the same period, so keep both the original and the restated values.
  - Concepts change tags over time (Apple revenue appears under 3 tags since 2014).
  - Quarterly cash flow facts are often year-to-date.

  — [Tradevo/dev.to: look-ahead in fundamentals](https://dev.to/tradevodata/your-backtest-saw-the-future-lookahead-bias-in-fundamental-data-measured-across-313562-rows-4cba); [Tradevo: point-in-time DB from EDGAR](https://dev.to/tradevodata/how-to-build-a-point-in-time-fundamentals-database-from-sec-edgar-and-when-not-to-2gn6); [Valuein guide](https://valuein.pages.dev/guides/avoiding-look-ahead-bias)

### Inferences
- Rules for the ecosystem's companyfacts cache:
  1. For each fact, select by `filed <= decision_date`, and for each (concept, period) take the earliest-filed value as "as first reported". Store later amendments separately with their own `filed` date.
  2. Never use `frames` for backtests or shadow replays.
  3. Make a value available from the next trading session after the acceptance timestamp.
  4. Derive Q4 and quarterly flows from YTD differences only using values each available at that date.
  5. Keep a tag-mapping table per concept, and mark a value unknown, not zero, when no mapped tag exists.
  6. Use market data (price, market cap for O/Z/Fp) as of the decision date, not the fiscal date.
- The 4-month rule is a conservative fallback when the `filed` date is missing. Prefer actual `filed` dates, since large accelerated filers file 10-Qs within 40 days and 10-Ks within 60 days.

### Gaps
- No academic study quantifying look-ahead bias specifically from XBRL restatements was found. The quantitative claims come from vendors and should not be relied on.
- The SEC filing deadlines (40/60 days for large accelerated filers) are from background knowledge, not from a source retrieved here; they should be verified before being cited.
