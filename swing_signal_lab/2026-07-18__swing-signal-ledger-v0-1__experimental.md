# Swing Signal Ledger v0.1

**Date:** 2026-07-18  
**Status:** EXPERIMENTAL_SHADOW_ONLY  
**Repository:** `Donh91/Eksperimenter-framework-`  
**Authority:** zero market, portfolio, canonical or execution authority

## Purpose

Build a prospective, auditable record of short-horizon swing signals and segment rotation calls so the framework can later test whether its DATA PING interpretation adds value beyond simple controls.

This is not a trading engine. It may not create live orders, change framework state, alter gates, promote rules or recommend position size.

## Core outputs

Each eligible source snapshot may produce:

1. one 5–7 day segment radar covering BTC/ETH, large caps, mid caps, small caps, microcaps and memes;
2. zero or more theoretical swing rows for approved liquid assets;
3. controls for HOLD, WAIT, simple momentum and deterministic placebo;
4. outcome rows after 1, 3 and 7 days.

## Source hierarchy

Use only eligible source-backed DATA PING observations resolved in this order:

1. direct eligible project-thread packet;
2. validated accepted-log receipt;
3. validated thread-derived handoff;
4. source unavailable.

Packets marked `FORMAT_TEST`, `eligible_for_canonical_acceptance=false`, `canonical_acceptance=NO` or equivalent non-binding test classification may support schema and source-QA only. They cannot create swing rows or outcomes.

## Eligible universe

Start with a small frozen liquid universe. Default candidates:

- BTC
- ETH
- SOL
- BNB
- LINK
- AAVE
- ONDO
- TAO
- AIOZ
- KARRAT

A token is eligible only when current price, source timestamp, liquidity status and invalidation can be frozen without interpolation. No token may enter merely because it is currently pumping.

## Segment radar

Required segments:

- BTC_ETH
- LARGE_CAP_ALTS
- MID_CAPS
- SMALL_CAPS
- MICROCAPS
- MEMES

Allowed movement labels:

- UP
- FLAT
- DOWN
- UNKNOWN

Allowed positioning labels:

- HOLD
- WAIT
- BUY_DIP_SMALL
- ACCUMULATE_SELECTIVELY
- ACCUMULATE_BROADLY
- REDUCE_RISK
- AVOID_CHASING

Every segment row must preserve the causal source ID and state whether the signal is based on direct data, a proxy, or unavailable evidence.

## Swing row creation gate

Create a theoretical swing row only when all conditions hold:

- source is eligible and no more than 36 hours old;
- exact source ID, timestamp and hash are frozen;
- asset is in the approved universe;
- entry zone, invalidation and horizon are explicit;
- at least two independent evidence families support the direction;
- no critical required field is reconstructed;
- no duplicate open row exists for the same asset and direction from the same causal event window;
- maximum one new row per asset per UTC day.

If any condition fails, log `NO_SIGNAL` or reject the row with an exact reason.

## Directions

Allowed signal directions:

- LONG
- WAIT
- AVOID

`SHORT` is excluded from v0.1 to keep the test aligned with pre-bull-run accumulation and risk timing.

## Required frozen fields

- signal_id
- source_snapshot_id
- source_timestamp_utc
- source_hash
- created_at_utc
- asset
- segment
- direction
- entry_low
- entry_high
- invalidation_price
- target_low
- target_high
- horizon_days
- evidence_codes
- data_quality
- regime_context
- fixed_before_outcome
- duplicate_event_window_id

No frozen field may be overwritten after creation.

## Outcome fields

Evaluate at 1, 3 and 7 days using later eligible source-backed observations:

- outcome_timestamp_utc
- mark_price
- maximum_favorable_excursion_pct
- maximum_adverse_excursion_pct
- target_hit
- invalidation_hit
- timeout_status
- return_from_entry_mid_pct
- return_after_costs_pct
- direction_result
- timing_result
- severe_failure
- outcome_source_id
- outcome_source_hash

Use a fixed round-trip cost assumption declared in `latest_state.json`. Do not change it retroactively.

## Controls

Every eligible LONG row must be compared with:

- HOLD_FROM_SIGNAL
- WAIT_CASH
- SIMPLE_24H_MOMENTUM
- DETERMINISTIC_PLACEBO

Segment radar must also be compared with the prior radar state and later realized segment behavior when data exist.

## Evaluation standards

Keep these separate:

- raw trade count;
- matured trade count;
- independent event windows;
- win rate;
- median return after costs;
- maximum adverse excursion;
- severe-failure rate;
- performance versus each control;
- performance by segment;
- performance by evidence code;
- performance by regime context.

Do not create one composite score.

Evidence labels:

- 0–9 matured rows: `INSUFFICIENT_SAMPLE`
- 10–24: `EARLY_SIGNAL_ONLY`
- 25–49 plus at least 3 independent windows: `FORWARD_CANDIDATE`
- 50+ plus at least 5 independent windows, controls beaten and no concentrated severe-failure mode: `GOVERNANCE_REVIEW_PERMITTED`

These labels authorize review only, never live promotion.

## Anti-hindsight rules

- no retrospective row creation;
- no current constituents used to backfill old segment calls;
- no outcome information in frozen fields;
- no replacing original observations with revised values;
- source revisions must preserve both original and revised prints;
- one signal cannot count as multiple independent event successes;
- overlapping signals from the same causal event window must be grouped.

## User-facing output contract

After each DATA PING, Main Framework may show:

1. one short conclusion;
2. a 5–7 day segment radar;
3. one swing-trade sentence.

The user-facing output is interpretation only. Repository rows remain shadow evidence and have zero execution authority.

## Stop conditions

Stop row creation and report `BLOCKED_SAFETY` on:

- source lineage conflict;
- missing source hash;
- retrospective signal request;
- duplicate event-window ambiguity;
- invalid entry/invalidation ordering;
- unsupported asset identity;
- attempt to promote or trade automatically;
- schema drift that changes frozen field meaning.
