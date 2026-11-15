# Event and valuation signals: earnings surprise and PEAD, insider trading (Form 4), value (E/P, B/M, CF/P, EBIT/EV, intangibles-adjusted)

Scope: US-listed stocks, 1-12 month horizons (shorter for PEAD), with weight on 2010-2026 and on out-of-sample or post-publication evidence. Search budget was about 18 tool calls. Several primary PDFs could not be machine-read: the Martineau CFR PDF downloaded as compressed binary and no PDF library was available. Some numbers therefore come from summaries of the papers, and each such case is flagged.

## Q1. Has PEAD decayed? Does it survive in small and micro caps, and after costs? How fast does the drift play out now?

### Takeaway
The best post-2000 evidence says PEAD in non-microcap US stocks has largely or entirely disappeared: prices now adjust on the announcement day. What drift remains sits mostly in microcaps and illiquid stocks, where trading costs historically absorbed 70-100% of paper profits. 2025 papers claiming "PEAD is alive" rely on designs that include microcaps or that use announcement-return factors. A rebuild over 2001-2024 shows the drift factor is insignificant once microcaps are excluded (t = 1.43).

### Cited Findings
- **Martineau (2022), "Rest in Peace Post-Earnings Announcement Drift", Critical Finance Review 11(3-4), 613-646.** Large stocks have shown no PEAD since 2006; in microcaps it disappeared only recently. — [Search summary of CFR / RePEc listing](https://nowpublishers.com/article/Details/CFR-0122); [IDEAS](https://ideas.repec.org/a/now/jnlcfr/104.00000122.html)
- In non-microcaps the drift began fading in 2001 and was fully gone by 2006. Prices now fully adjust on the announcement day. The brief attributes this to decimalization and to the 2005 rule changes that sped up high-frequency trading. — [UCLA Anderson Review research brief](https://anderson-review.ucla.edu/is-post-earnings-announcement-drift-a-thing-again)
- In recent years Martineau finds "no evidence of slow price formation following earnings announcements". — [SSRN working version via Scribd](https://www.scribd.com/document/626905438/SSRN-id3111607) (working-paper copy; check against the published CFR version)
- Clement, Lee and Ow Yong report that drift has declined for both random-walk-based SUE (no analysts) and analyst-forecast-based SUE. Their drift-ratio measure of delayed reaction has fallen significantly in recent years. — [A new perspective on PEAD: relative drift measure (vLex abstract)](https://law-journals-books.vlex.com/vid/new-perspective-on-postearningsannouncementdrift-855667163)
- **Counter-evidence from 2025:**
  - Dickerson, Julliard and Mueller, "The co-pricing factor zoo" (JFE, forthcoming), search about 18 quadrillion factor-model combinations for stocks and corporate bonds. They conclude that earnings drift remains a major priced factor. Two caveats: they do not filter out microcaps, and their earnings factor is built from abnormal returns around announcements, not from accounting surprises. — [UCLA Anderson Review](https://anderson-review.ucla.edu/is-post-earnings-announcement-drift-a-thing-again)
  - Hirshleifer, Peng and Wang (RFS 38(3), 2025) report a drift t-statistic of about 14 that is weaker when social connectedness is higher. Microcaps are not filtered. — [UCLA Anderson Review](https://anderson-review.ucla.edu/is-post-earnings-announcement-drift-a-thing-again)
- **Subrahmanyam (2025), "Keeping it Simple: How Can Post-Earnings Return Drift Exist and Not Exist Simultaneously?" (SSRN 5930255).** Sample: US stocks, February 2001 to December 2024. Microcaps are defined as stocks below the 20th NYSE size percentile, about 3% of total market cap. The drift factor has t = 2.18 on all stocks and t = 1.43 without microcaps. He concludes microcaps drive the apparent PEAD, and that the Hirshleifer et al. social-network effect looks like a size effect once size is controlled. — [UCLA Anderson Review](https://anderson-review.ucla.edu/is-post-earnings-announcement-drift-a-thing-again)
- **Chordia, Goyal, Sadka, Sadka and Shivakumar (2009), FAJ 65(4).** PEAD is concentrated in illiquid stocks: the value-weighted long-short return is 0.04% per month in the most liquid stocks and 2.43% per month in the most illiquid. Transaction costs absorb 70-100% of paper profits. — [CFA Institute summary](https://rpc.cfainstitute.org/research/financial-analysts-journal/2009/liquidity-and-the-post-earnings-announcement-drift); [IDEAS](https://ideas.repec.org/a/taf/ufajxx/v65y2009i4p18-32.html)
- **Jegadeesh and Livnat (2006), FAJ 62(2).** Drift is larger when revenue and earnings surprises point the same way. Revenue surprises flag earnings surprises that persist. Firms with extreme surprises of the same sign earned significantly higher abnormal returns in the following quarter. — [CFA Institute](https://rpc.cfainstitute.org/en/research/financial-analysts-journal/2006/post-earnings-announcement-drift-the-role-of-revenue-surprises); [IDEAS](https://ideas.repec.org/a/taf/ufajxx/v62y2006i2p22-34.html)
- **Regime dependence.** Da, Wang and Zeng ("Presidential PEAD", 2025-2026 working paper) report much stronger drift under Republican presidencies. — [Wharton PDF, Mar 2026](https://accounting.wharton.upenn.edu/wp-content/uploads/2026/04/Presidential_PEAD-Mar-2026.pdf); [Notre Dame slides](https://academicweb.nd.edu/~zda/PresiPEAD.pdf)
- **General post-publication decay of anomalies:**
  - Chen and Velikov, using 204 predictors from the open-source dataset, replicate a post-publication decay of about 50%. The decay rises to about 72% when the post-publication sample is restricted to after 2005. — [Search summary of Chen-Velikov / JFQA "Zeroing in on the expected returns of anomalies"](https://core-prod.cambridgecore.org/core/services/aop-cambridge-core/content/view/945133D5A3ECEEAF466AEE91551FD225/S0022109022000874a.pdf/zeroing-in-on-the-expected-returns-of-anomalies.pdf)
  - Chen, Lopez-Lira and Zimmermann find that, split at 2004, the mean long-short return of published predictors falls from 71 to 31 bps per month (gross), and 88% of predictors decay. — [arXiv 2212.10317](https://arxiv.org/html/2212.10317v6)

### Inferences
- **Long and short sides.** None of the sources found split post-2006 PEAD into long and short legs. Since the residual drift sits in illiquid microcaps, the short leg (low-SUE microcaps) is probably hard and costly to borrow. Any residual edge is more plausibly harvested on the long side, as a filter or tilt rather than a standalone long-short strategy. This is an inference, not a measured result.
- **Speed.** If prices "fully adjust" on day 0 for non-microcaps, the useful horizon in liquid names is the announcement window itself (days 0 to +1 or +2). The classic 60-day drift is not supported for large and mid caps after about 2006.
- **SUE without analysts** (seasonal random walk: (EPS_q − EPS_{q−4}) / σ of past changes) is the variant Clement et al. show has also declined. It remains a reasonable descriptive "surprise" feature from SEC data alone, but it should not be relied on as a 1-12 month alpha source in liquid stocks.
- **Revenue surprise** (Jegadeesh-Livnat) is best used as a confirmation of earnings-surprise direction, a measure of persistence, and not as an independent signal. No post-2010 standalone replication was found.
- **Paper bias.** The "alive" papers use EAR-style factors, which are price-based and include microcaps. The "dead" papers use accounting surprises and exclude microcaps. An implementation should test both definitions, split by NYSE size breakpoints, and measure net of a cost model.

### Gaps
- Martineau's exact CAR magnitudes by period and size could not be extracted because the PDF was unreadable. The published paper (CFR 11:3-4) has them; the filenames suggest a 1984-2019 sample with a month-1-to-60 PEAD figure.
- No post-2010 out-of-sample number was found for the earnings-announcement return (EAR) signal (Brandt et al. 2008) on its own.
- No post-2010 replication of revenue-surprise (SURGE) drift was found.
- No recent study was found that reports PEAD net of costs for 2010-2024 specifically. Chordia et al. (2009) covers the earlier period.
- No source split long versus short legs post-2006.

## Q2. How profitable are insider-purchase signals after publication and after costs? Filing-lag rules?

### Takeaway
The original evidence (1986-2007) is strong: opportunistic insider trades earn about 82 bps per month value-weighted abnormal return, while routine trades earn about zero. After Sarbanes-Oxley (two-business-day Form 4 deadline from August 2002), short-window returns to purchases after filing became larger: 1.13-1.89% over 2-3 days versus 0.32-0.59% before. Purchases are much more informative than sales. Post-2018 microcap evidence shows meaningful 30-day CARs (2-6%), but realistic costs remove roughly half of them. Rigorous post-publication net-of-cost studies of the Cohen-Malloy-Pomorski signal were not found.

### Cited Findings
- **Cohen, Malloy and Pomorski (2012), "Decoding Inside Information", Journal of Finance 67(3), 1009-1043** (NBER WP 16454). Data: Thomson Reuters insider filings, January 1986 to December 2007.
  - "Routine" insiders are those who traded in the same calendar month for several consecutive years; they make up about 55% of insider trades. Opportunistic insiders show no discernible timing pattern.
  - An opportunistic-only strategy earns value-weighted abnormal returns of 82 bps per month. Routine trades earn essentially zero.
  - Opportunistic trades predict firm news: analyst forecasts, management forecasts and earnings announcements.
  - The most informed are local, non-executive insiders in geographically concentrated, poorly governed firms.
  - Opportunistic traders are more likely to face SEC enforcement, and they cut trading after enforcement waves.
  - Sources: [NBER Digest](https://www.nber.org/digest/apr11/decoding-inside-information); [HBS Working Knowledge](https://hbswk.hbs.edu/item/decoding-inside-information); [Harvard Law Forum](https://corpgov.law.harvard.edu/2012/02/03/decoding-inside-information); [EconPapers](https://econpapers.repec.org/RePEc:bla:jfinan:v:67:y:2012:i:3:p:1009-1043)
- **Filing-lag rule.** SOX Section 403 requires Form 4 within two business days of the trade, effective August 2002. Before that, the deadline was ten days after the end of the month in which the trade occurred. — [Brochet (Accounting Review) as summarized in search; CXO Advisory](https://www.cxoadvisory.com/?p=11135); [Harvard Law Forum, "SOX and Insider Trades"](https://corpgov.law.harvard.edu/2009/10/30/sox-and-insider-trades/)
- **Brochet.** Three-day abnormal return (size and B/M adjusted) from SEC receipt of the Form 4, for purchases: 0.59% before SOX versus 1.89% after. — [Harvard Law Forum](https://corpgov.law.harvard.edu/2009/10/30/sox-and-insider-trades/)
- **Hossain (2010).**
  - Sample: 42,794 Form 4 filings, 1996 to May 2009.
  - Two-day CAR from the filing date, purchases: +0.32% pre-SOX versus +1.13% post-SOX. Sales: −0.19% versus −0.54%.
  - Post-credit-crunch: purchases +1.84%, sales −0.97%.
  - Insider rank was the most important factor: higher-ranked insiders earned larger CARs.
  - Size and trade-size effects were not statistically significant.
  - Caveats: returns are gross; part of the return from the filing date may not be capturable; the result is short-term only.
  - Source: [CXO Advisory summary](https://cxoadvisory.com/investing-expertise/evolving-informativeness-of-insider-trading)
- **Wintoki et al. (2016 working paper).** Both insider purchases and sales predict abnormal returns, before and after SOX. — [Wichita State PDF](https://www.wichita.edu/academics/business/accountancy/documents/InsiderTradingPatternsWintokiNov2016.pdf)
- **Insider purchases in microcaps, 2018-2024 (arXiv 2602.06198, 2026).**
  - Sample: EDGAR Form 4 open-market purchases (code "P") in firms of $30M-$500M market cap; 17,237 transactions; filters for reporting lag ≤90 days, trade ≥$5k and ADV ≥$200k.
  - 30-day Fama-French three-factor CAR by distance above the pre-disclosure price: +2.3% (≤0%), +4.7% (0-3%), +4.4% (3-5%), +4.8% (5-10%), +6.3% (>10%). The difference between extremes has t = −5.13.
  - Distance from the 52-week high is the strongest feature. Insider title and trade size rank low.
  - With 2% spread and 1% impact on $50k, the 6.3% CAR falls to about 3.3%.
  - Out-of-sample 2024 test: XGBoost AUC 0.70.
  - The paper does not test cluster buying or opportunistic-versus-routine. Horizons of 60 days or more are "weaker".
  - Sources: [arXiv](https://arxiv.org/html/2602.06198v1) (single-author preprint, not peer-reviewed; internal inconsistencies noted, e.g., bucket counts sum to 17,060 rather than 17,237)

### Inferences
- **Long side.** Opportunistic, non-routine open-market purchases (code P) remain the strongest documented insider signal, especially by officers or directors and in small firms. Post-SOX, much of the short-run reaction occurs in the first 1-3 days after filing. A system that reads Form 4 the next day captures only what remains, though 30-day CARs in microcaps still appear positive after a rough cost haircut.
- **Short side.** Sales are weaker and noisier: post-SOX two-day CAR is −0.54% versus +1.13% for purchases. Most sales are routine, diversification or 10b5-1 sales. The new AFF10B5ONE flag in the SEC datasets (from 2023) helps exclude pre-planned trades. Insider selling is at best a weak, short-horizon negative tilt, not a standalone short signal.
- **Cluster buying** (several insiders buying within a window) is widely used by practitioners. No peer-reviewed post-2010 quantification was found in this search.
- **Routine classification.** The Cohen-Malloy-Pomorski method needs at least 3 years of insider trade history per person. It is computable from SEC Form 4 data (2003 onward from the SEC structured sets).

### Gaps
- No peer-reviewed post-2012 out-of-sample replication of the 82 bps/month opportunistic result, with or without costs, was found. The Open Source Asset Pricing project may include an insider predictor, but its post-publication statistics were not retrieved.
- No quantified cluster-buying evidence was found for 2010-2026.
- No 3-12 month horizon numbers were found for the post-2010 period.

## Q3. What is value's record after 2007, and does it work better combined with quality or profitability?

### Takeaway
Traditional B/M value (HML) had its longest and deepest drawdown from the end of 2006 through 2020. It rebounded sharply in 2021-2022, with HML up about 18.8% year-to-date by August 2022, then lagged again in 2023-2024 as growth led, concentrated in the Magnificent Seven. Intangibles-adjusted value would have reduced the "value winter" substantially: about +2.1 to 2.4% per year over HML in 1975-2018, and about +2.2% per year over the 13.5 years to 2020, with drawdown duration cut by about three-quarters. Profitability explains about half of value's post-2007 underperformance, so value works better when combined with profitability. Most of this evidence is in-sample or written by factor originators.

### Cited Findings
- **AQR on the drawdown.** Value's drawdown from end-2006 to 2019, plus further losses in 2020, was the longest and deepest in HML history. AQR's HML Devil (timely-price B/M) data is updated through December 31, 2025. HML Devil portfolios have statistically significant alphas of 305-378 bps per year against a 5-factor model (original sample). — [AQR Datasets, HML Devil](https://www.aqr.com/Insights/Datasets/The-Devil-in-HMLs-Details-Factors-Monthly); [AQR "Is Systematic Value Investing Dead"](https://www.aqr.com/Insights/Perspectives/Is-Systematic-Value-Investing-Dead)
- **2022 rebound.** Fama-French HML was up 18.8% in 2022, which appears to be year-to-date through August. Acadian argues this mostly reversed an unprecedented 2017-2021 run of speculation in growth stocks. — [Acadian, Growth vs Value: End of an Era?](https://www.acadian-asset.com/investment-insights/equities/growth-versus-value-end-of-an-era)
- **2023-2024.** The growth index beat value by more than 30% in 2023 and by about 16% year-to-date by late June 2024. The Magnificent Seven made up 51.9% of the Russell 1000 Growth. — [Newedge Wealth](https://www.newedgewealth.com/?p=46705)
- **Conflicting views:**
  - Robeco describes value's comeback as keeping traction through 2021-2023, using data through December 2023. — [Robeco](https://robeco.com/en-uk/insights/2024/01/value-investing-the-reports-of-my-death-have-been-greatly-exaggerated)
  - AllianceBernstein saw early signs of value recovery in Q3 2024. — [AllianceBernstein](https://www.alliancebernstein.com/apac/en/institutions/insights/investment-insights/the-forgotten-style-what-ever-happened-to-value.html)
- **Year-by-year data.** Ken French's library has annual US HML returns through 2025, but the figures were not retrieved here. — [Ken French Data Library](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/f-f_factors.html)
- **Eisfeldt, Kim and Papanikolaou, "Intangible Value", Critical Finance Review (2022), NBER WP 28056.**
  - Book equity is augmented with intangible capital, built by perpetual-inventory accumulation of SG&A, to form HML^INT.
  - In 1975-2018, intangible value significantly outperforms traditional value: the alpha of HML^INT on HML is 3.32%. The HML^INT − HML long-short averaged about 2.1% per year (SD 6.4%); CXO reports about 2.4% per year annualized.
  - The outperformance was more pronounced after the financial crisis.
  - Returns are gross.
  - Sources: [CFR](https://nowpublishers.com/article/Details/CFR-0113); [NBER WP](https://www.nber.org/system/files/working_papers/w28056/revisions/w28056.rev1.pdf); [CXO Advisory](https://www.cxoadvisory.com/fundamental-valuation/intangible-value-factor/); [Advisor Perspectives](https://www.advisorperspectives.com/articles/2022/01/24/do-intangible-assets-explain-the-failure-of-the-value-factor). Exact figures are from secondary summaries.
- **Arnott, Harvey, Kalesnik and Linnainmaa (2021), "Reports of Value's Death May Be Greatly Exaggerated", FAJ 77(1), 44-67.**
  - Capitalizing intangibles would have raised the value factor's return by about 2.2% per year over the prior 13.5 years and cut the drawdown duration by nearly three-quarters.
  - The main driver of post-2007 underperformance was growth stocks becoming more expensive relative to value stocks (a widening valuation spread), not fundamental weakness.
  - Sources: [CFA Institute](https://rpc.cfainstitute.org/research/financial-analysts-journal/2020/0015198x-2020-1842704); [Advisor Perspectives commentary](https://www.advisorperspectives.com/commentaries/2020/01/30/reports-of-values-death-may-be-greatly-exaggerated); [IDEAS](https://ideas.repec.org/a/taf/ufajxx/v77y2021i1p44-67.html). Figures are from secondary summaries.
- **Medhat and Novy-Marx (2025), "Profitability Retrospective".**
  - Profitability explains about half of value's underperformance since 2007.
  - Profitability subsumes the "quality" factors: none earn significant alpha relative to profitability, the other FF factors and momentum.
  - Disclosure: Novy-Marx consults for DFA.
  - Sources: [AFA paper page](https://afajof.org/management/viewp.php?n=155428); [Alpha Architect](https://alphaarchitect.com/profitability/)
- **Novy-Marx (2013), combined profitability and value.** About 9% per year with a realized Sharpe ratio of about 0.9 over 1963-2009 (in-sample). CXO warns about data-snooping inflation in quality-value tests. Wahal and Repetto (2020) found that tilting long-only portfolios toward both factors improved value. Verdad (2025) finds quality and value negatively correlated in large caps over 1990-2024: profitable firms tend to be expensive. — [Alpha Architect](https://alphaarchitect.com/profitability/); [CXO Advisory](https://www.cxoadvisory.com/?p=23218); [WealthManagement](https://www.wealthmanagement.com/equities/combining-value-and-profitability-in-small-caps)

### Inferences
- **Choice of measures.** For a 1-12 month horizon in 2026, earnings- and cash-flow-based yields (E/P, CF/P, EBIT/EV) and intangibles-adjusted B/M are more defensible than raw B/M. The documented B/M failures are tied to unrecorded R&D and SG&A investment. Treat this as an inference from the intangibles papers; no source compared E/P or EBIT/EV versus B/M after 2007 directly.
- **Combine with profitability.** Value combined with profitability or quality (e.g., gross profits/assets) is the strongest supported form. Pure cheapness picks up distressed or low-profitability names, which drove about half of the post-2007 shortfall.
- **Long versus short.** Classic value long-short returns come substantially from the short leg (expensive growth). In 2023-2024 that leg was dominated by mega-cap tech momentum, which was very painful. A long-only value-plus-profitability tilt avoids that concentration risk. Long/short split numbers for 2010-2025 were not found.
- **Valuation-spread regime.** Arnott et al.'s point that the spread widened suggests that the starting valuation spread drives expected value returns at multi-year horizons. Its timing value at 1-12 months is weak and not established here.

### Gaps
- Exact annual HML, HML Devil and HML^INT returns for 2021-2025 were not retrieved; they are available in the Ken French and AQR data files.
- No post-2018 out-of-sample test of HML^INT was found.
- No head-to-head comparison of E/P, CF/P, EBIT/EV and B/M for 2010-2025 was found.
- No net-of-cost figures were found for any value variant in this period.

## Q4. Which inputs are free from the SEC, and what are the pitfalls?

### Takeaway
All three signal families can be built from free SEC data:
- XBRL companyfacts/frames give quarterly and annual EPS, net income and revenue, with filing dates, from about 2009.
- The Financial Statement Data Sets give quarterly as-filed bulk data from 2009 Q1.
- The Insider Transactions Data Sets give flattened Forms 3/4/5 XML quarterly, and the EDGAR submissions/filings feed gives the timing of 8-K and Form 4 filings.

The main pitfalls are period alignment, Q4 values that must be derived from the 10-K, restated values replacing originals, and using the right point-in-time date: the filing or acceptance date, not the period end.

### Cited Findings
- **data.sec.gov APIs (no API key needed):**
  - companyfacts returns all XBRL concept data for a CIK. companyconcept groups facts by unit.
  - frames returns one fact per entity, from the latest filing that best fits a calendar period (e.g., CY2019Q1I). Annual periods are 365 ±30 days and quarterly 91 ±30 days. The SEC advises checking each fact's start and end dates because reporting calendars differ.
  - Only standard-taxonomy, entity-wide facts are included; custom tags are excluded.
  - Bulk companyfacts.zip and submissions.zip are rebuilt nightly at about 03:00 ET. The APIs update in real time: submissions in under a second, XBRL within about a minute, longer at peak.
  - Source: [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- **Financial Statement Data Sets.**
  - Contents: face-financial XBRL numbers, flattened, as filed; updated quarterly; coverage from 2009 Q1; no accuracy guarantee.
  - The December 2024 reprocessing limited the data to primary statement items and added a "segments" field to NUM.
  - Field definitions (filed/accepted, fp, qtrs) are in a separate PDF.
  - Source: [SEC Financial Statement Data Sets](https://www.sec.gov/dera/data/financial-statement-data-sets)
- **Insider Transactions Data Sets.**
  - Built from the XML of Forms 3/4/5, flattened and unchanged from the as-filed submissions, updated quarterly. Filings after 5:30 pm ET on the last business day of a quarter roll into the next posting.
  - In July 2025 the SEC added the AFF10B5ONE (Rule 10b5-1 plan) element to the SUBMISSION file for the 2023-2025 sets.
  - No accuracy guarantee.
  - Sources: [SEC Insider Transactions Data Sets](https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets); [Form 345 announcement](https://www.sec.gov/structureddata/announcement/osd-announcement-081222-form-345-data-sets)
- **Form 4 deadline.** Two business days since August 2002 (SOX §403). — [Harvard Law Forum](https://corpgov.law.harvard.edu/2009/10/30/sox-and-insider-trades/)

### Inferences
- **Pitfalls with SEC data:**
  - **Q4 derivation.** Most firms tag no standalone Q4 duration. Q4 must be derived as FY minus 9M YTD, or FY minus (Q1+Q2+Q3), using values from the same filing vintage. EPS cannot be safely derived this way because share counts change; recompute from net income and weighted shares, or treat Q4 EPS as unknown.
  - **Restatements.** companyfacts keeps multiple facts for the same period from later filings, for example prior-year comparatives in the next 10-Q. Point-in-time SUE must use the first-filed value as of the decision date, keyed by `filed`. Otherwise look-ahead bias enters.
  - **Fiscal calendars.** frames snap to calendar quarters within ±30 days. For seasonal random walk SUE, match fiscal quarter q to q−4 by the firm's own fiscal period (fy/fp) and period end, not the calendar frame.
  - **Announcement date versus 10-Q date.** Earnings are usually first released in the 8-K Item 2.02 press release, which typically has no full XBRL. The 10-Q/10-K XBRL arrives days or weeks later. SUE from XBRL is known only at the 10-Q filing date, after most of the price reaction. Use the 8-K Item 2.02 acceptance timestamp (from EDGAR submissions) as the event date for EAR, and treat XBRL-based SUE as lagged.
  - **Insider data timing.** The quarterly Insider Transactions sets are too slow for event trading. Near-real-time use requires parsing Form 4 XML from EDGAR daily. Use the filing acceptance time, not the trade date, as the signal date.
  - **Unit and scale issues.** Values can be in thousands versus units, and share-class EPS differs across classes. Custom tags are excluded from the APIs, so some firms' revenue or EPS is missing. Treat these as explicit unknowns, consistent with the repo invariant.

### Gaps
- No SEC documentation was retrieved that explicitly describes duplicate or restated fact handling, or defines the `fp`/`fy`/`form`/`filed` fields in companyfacts. The page fetched does not cover them; the SEC's FSDS PDF and XBRL API notes would.
- SEC fair-access limits (about 10 requests per second, declared User-Agent) were not confirmed from a fetched source in this session.
