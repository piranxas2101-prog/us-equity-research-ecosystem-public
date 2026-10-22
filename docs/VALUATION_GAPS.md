# Valuation gaps: a value without meaning fails and is chased until found (owner's decision 2026-10-22)

**Rationale.** The LAES report found that the stage-1 rule "forward P/E <= 35" passed for a negative forward P/E
(-6.1): a company expected to lose money earned the rule's weight (1 of 10.5) and a higher composite score. On the
2026-10-22 shortlist 24 companies had a negative forward P/E; with the change their stage-1 score falls by 9.52
points (15 of them below the 70 entry line; members stay until the 55 exit line).

**Rule.** A rule may declare `invalid_at_or_below` (scanner_config.json: forward_pe, 0). An observed value at or
below it is not a valuation: the rule is **failed**, never passed, in `evaluate_rules` and in the SEC-first path
(`sec_fundamental_ratios.verdict`). Missing values stay `missing_data` as before.

**The gap is a task** (`valuation_gaps.py`, state `data/valuation_gaps.json`): the company's gap opens; every night
the stage-1 info cache asks Yahoo afresh for it instead of reusing a stored copy; a missing forward P/E is also
built from price / forward EPS when the forward EPS is positive; the night a meaningful value is found the rule is
judged on it and the gap is closed with the date and the value. Tests: `tests/test_valuation_gaps.py`.
