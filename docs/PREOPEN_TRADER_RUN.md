# Pre-open trader run

Owner's request (2026-10-12): keep the nightly research run as it is (fundamentals, research only), and add a
second, short run before the US open with only the technical side and the traders, so that positions are opened
from the nightly fundamental analysis plus what changed overnight.

## What changes between the nightly run and the open

The nightly run finishes around 07:30 Athens, after the previous US close, so the daily bars it scores are the
same ones the traders would see at 09:00 New York. Re-running the daily indicators at that time would repeat the
nightly result. Two things are new before the open and are not in those bars:

1. **The pre-market price.** A stock can open far from its last close (earnings, news, index moves).
2. **SEC filings accepted after the close.** An 8-K filed in the evening or the morning is newer than the
   fundamental review.

`preopen_check.py` reads exactly these two for the traders' candidates and writes a checked copy of each trader's
output to `output/preopen/` (`yankos_strategy_preopen_latest.json`, `periklis_strategy_preopen_latest.json`), with
a report (`preopen_check_latest.json` / `.md`) and a dated copy under `output/preopen/history/` for later review.

## Rules

| Situation | Effect | Why |
|---|---|---|
| Pre-market gap against the trade larger than 1x the 14-day ATR (long: price below the last close; short: above) | candidate to watch | The nightly setup assumed a price that is no longer there. One ATR is the stock's own typical daily range, so the threshold scales with each company instead of one fixed percentage. |
| New content-bearing 8-K item accepted after the last close (taxonomy `sec_item_rules`, supporting-only items such as 9.01 and plain forms such as Form 4 ignored) | candidate to watch | The business news is newer than the fundamental review; its effect is unknown until the next research run reads it. |
| Gap in favour of the trade | no change | Entry timing after the open is Miltos' job (15-minute confirmation). |
| Pre-market price, last close or ATR unknown; filings could not be read | no change, stated in the report | Unknown stays unknown: no gap is invented, and a missing read does not block. |

The check only takes a candidate down to watch. It never raises a row, never changes a score, the fundamental gate,
risk limits, sizing or the run id (Koulis still matches the rows to the nightly run). The input files are not
modified.

Sources: pre-market prices first from Alpaca's multi-symbol snapshots (read-only market data, IEX feed, one
request per 100 symbols; a batch refused for one bad symbol is split in halves), then yfinance 1-minute bars only
for the symbols still missing (batches of 25, one retry for the symbols a batch missed: Yahoo answers "possibly
delisted" when it limits an IP); only a trade from today before 09:30 New York counts. Each source has a 5-minute
budget, and what was left unknown (price or filings) is looked at first in the next check (`output/preopen/
pending_unknowns.json`); the last close from the price store (`data/price_history`, raw close, no older than 5 days); filings from
SEC submissions (`data.sec.gov/submissions`) through the shared SEC rate limiter.

## Daily timing

| New York | Athens (usual) | Step |
|---|---|---|
| 09:00 | 16:00 | `preopen_check.py` |
| 09:35 | 16:35 | paper workflow on the checked files: Koulis, Thymios, trader, then Miltos and the position monitors |

When Europe and the US change clocks on different weeks, the Athens times move by one hour; the scheduled script
works in New York time.

## Status (2026-10-13)

- `start_trader_afternoon_session.ps1` runs the whole afternoon from the Mon-Fri 16:00 task (owner registered it):
  the check, then at 09:35 New York the paper launcher on the checked files (`-YankosStrategy` / `-PeriklisStrategy`),
  only if today's nightly run completed, its traders' files match and no session started today; after the close
  `paper_session_report.py` writes `output/paper_session/session_report_latest.md` and a notification is shown.
- It keeps the computer awake only while there is work; with nothing to trade, after the day's report or after an error (always notified) `hibernate_if_idle.ps1` hibernates it when idle (switch `power.hibernate_after_afternoon`).
- Limits are the launcher's defaults (6 orders x $250, at most 2 shorts, stop -3%, take profit +6%, same-day flat).
