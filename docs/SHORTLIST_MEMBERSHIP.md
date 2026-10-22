# Shortlist membership (index-style entry and exit)

Decision date: 2026-10-01. Owner direction: the shortlist should work like an index (names join and leave by rules over
time), not by one absolute score threshold re-applied to every symbol each night.

## Problem

Babis kept a company only if its stage-1 fundamental score reached `stage1_min_composite_score` (70), re-tested
nightly. The score is the share of rule weight matched (weights sum to 10.5), so one rule worth 1.5 moves a name by
about 14 points. A name near 70 flipped in and out from one night to the next, and each flip re-ran its downstream
analysis and changed its Giannis tier.

## Rule

- **Enter** when the stage-1 score reaches the entry threshold (`stage1_min_composite_score`, 70).
- **Stay** as a member until the score falls below the **exit threshold** (`pipeline.stage1_membership.exit_composite_score`,
  55), or the company stops being eligible: price above the maximum, missing price, fundamental coverage below the
  minimum, insufficient history, no provider coverage, or a route that is no longer a company.
- The 15-point band is slightly more than the weight of one 1.5 rule, so one rule flipping does not move a member out.
- A member that was not scanned tonight (provider error, deferred) stays a member, unchanged and marked
  `not_scanned_at`; a data failure never removes a name.
- Every addition and removal is recorded with date and reason in `output/shortlist_membership.json` (events keep the
  last 1000) and in `babis_shortlist_latest.json` under `membership`. The daily report shows the removal reason.

The membership file only records state; it changes no score. A missing or unreadable file means every name is new
(entry threshold for all), which is the previous behaviour. The file was seeded on 2026-10-01 from the then current
shortlist (1064 names) so the first night already has the buffer.

## Configuration and revert

`scanner_config.json` -> `pipeline.stage1_membership` = `{enabled, exit_composite_score, state_file}`.
Set `enabled` to `false` (or remove the key) to return to the single threshold. The stage-1 checkpoint signature
includes the exit threshold and a hash of the active members, so a resumed checkpoint never mixes two memberships.

## What it does not do

It does not remove the entry threshold: with no threshold all ~4400 eligible companies would go through the control-plane
batch every night (about 2.3 s each, 2.8 h). Size and eligibility stay as they are; only the flipping stops.

Tests: `tests/test_shortlist_membership.py`.
