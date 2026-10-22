# Parallel stage-1 fetch (off by default)

Decision date: 2026-10-01. Owner direction: agents should work concurrently, and the 3.5 hour Babis scan (price history
68 min, yfinance info and news 1.5 h, SEC recent filings 31 min, one symbol at a time) is the longest step.

## What it is

`pipeline.stage1_workers` in `scanner_config.json` (default **1**: today's sequential behaviour, nothing changes).
With N > 1, `scan_market_core` requests snapshots ahead with N threads, but consumes every result **in symbol order**, so
the rules, filters, membership, checkpoints and the output are exactly those of the sequential loop. A worker error is
re-raised at the symbol's own turn and handled by the same code (`tests/test_shortlist_membership.py` compares a
4-worker run with scrambled latency to the sequential run: identical results, errors and order).

Rate limits: with N > 1 every SEC request passes one in-process gate spaced 0.15 s apart (about 6.7 requests a second,
under the SEC limit of 10); the shared provider limiter for market data (`shared_rate_limit_enabled`) is already
safe across threads (a SQLite budget). yfinance has no such budget.

## Why it is off

It has not been run against the real providers. yfinance rate-limits by IP and its behaviour under threads is untested
here; a burst of 429s could hurt more than the time saved. Trial it before relying on it:

1. Set `stage1_workers` to 3 and run a small, bounded scan by hand (a limited universe), watching `[ERROR]` lines and
   the runtime summary (`market_data_seconds`, `fundamentals_and_news_seconds`, `sec_seconds`).
2. Compare the shortlist with a sequential run of the same symbols (it must be identical).
3. Raise the workers gradually; set it back to 1 at the first wave of provider errors.

Revert: set `stage1_workers` to 1.

## First live trial (2026-10-06, approved by the owner)

`tools/babis_parallel_trial.py --symbols 60 --workers 3`, 60 shortlist symbols, nightly stage-1 settings, every
yfinance info call live (no info cache, the heaviest case), run as a separate process during the day:

| | wall time | per symbol | errors | provider timeouts |
|---|---|---|---|---|
| sequential | 197.7 s | 3.3 s | 0 | 0 |
| 3 workers | 93.4 s | 1.56 s | 0 | 0 |

Speed-up ×2.1; identical results (same 60 tickers and composite scores, same order). The per-phase seconds in the
runtime summary add up thread time, so with workers they exceed the wall time; compare wall time only.
Caveat: 60 symbols is not 5,300. yfinance may throttle a 3-thread night-long scan; the time limits and the shared
provider limiter catch hangs, but a wave of 429s would show up as errors. Proposal: switch `stage1_workers` to 3 when the
branch is merged (Sunday 2026-10-11), watch the first night's errors and `provider_timeouts`, and set it back to 1 at
the first wave of provider errors. Expected Babis scan: ~3 h -> ~1.5 h.

## First night with 3 workers (2026-10-07): reverted to 1

The full nightly scan got slower, not faster: 4.8 h instead of 3.0 h (elapsed 17,142 s vs 10,516 s). The time went
into price history: thread-summed market-data seconds 26,044 vs 5,352, i.e. each history request took about five
times longer. In the nightly run the yfinance info comes from the store (reuse), so price history dominates, and its
providers are paced by the shared rate limiter: three threads only queue on the same budget and add contention.
The data got slightly worse too (history exclusions 226 -> 246, no provider coverage 4 -> 16). The 60-symbol trial
had live info calls, which do run in parallel, so it overstated the gain. `stage1_workers` is back to 1. A real
speed-up of the price step needs bulk history downloads (many symbols per request), not more threads.
