# Eksperimenter-framework-

Public, non-canonical execution plane for prospective experiments, independent replication, one-shot data recovery and auditable research artifacts.

## Role in the wider framework

This repository is the **Experiment Execution Plane**.

The canonical repository `Donh91/Investering-Framework-Archive-v1` remains the **Control Plane** and owns:

- candidate registration;
- frozen forecasts;
- outcome maturation;
- lifecycle state;
- weekly adjudication;
- governance decisions.

This repository owns:

- independent request-hash verification;
- deterministic replication of sensor firing logic;
- execution receipts;
- experimental scripts and workflows;
- raw research artifacts;
- no canonical or portfolio authority.

## Automated experiment bridge

`Experiment Execution Plane` runs five times per day and may also be started manually.

It:

1. fetches the public dispatch manifest from the canonical repository;
2. verifies every request hash;
3. preserves accepted requests immutably;
4. recomputes the supplied sensor conjunction from frozen component values;
5. writes an independent receipt;
6. publishes `experiment_bridge/LATEST_EXECUTION_RECEIPT_MANIFEST.json` for synchronization back to the Control Plane.

Supported replication states:

- `REPLICATED_FIRED`
- `REPLICATED_NOT_FIRED`
- `REPLICATED_WAITING_FOR_DATA`
- `REPLICATION_MISMATCH`

Novel or initially strange hypotheses are allowed. They must still be measurable and falsifiable. They are not deleted merely because value has not appeared yet.

## Swing Signal Ledger v0.1

The existing Swing Signal Ledger remains shadow-only. Its prospective, anti-hindsight and control-comparison rules remain valid. The new bridge provides the missing automated candidate and receipt path, but it does not grant live trading authority.

## Installed manual workflow

`Fetch DeFiLlama stablecoin and DEX history`

Purpose:

- fetch 12 public DeFiLlama JSON responses;
- preserve raw bytes and SHA-256 checksums;
- normalize stablecoin supply and DEX daily volume from 2024-01-01;
- calculate exact 3D/7D changes without interpolation;
- build `STABLECOIN_DEPLOYMENT_PROXY_HISTORY.csv`;
- upload one auditable artifact;
- never commit, push or alter canonical framework data.

Safety:

```text
workflow_dispatch only
contents: read
no schedule
no secrets
no canonical repository write
artifact retention: 7 days
```

The workflow intentionally fails its final validation step if fewer than all 12 endpoints are fetched and parsed. The artifact is uploaded first so failures remain auditable.

## BTC.D validation tool

`scripts/normalize_validate_btc_d.py` validates an unedited TradingView BTC.D export with continuous daily coverage, no duplicates, no gaps, dispersed anchors and no interpolation or backdating.

## Public Pons 24h/72h forward source-outcome observer

The EXISTING `Public Pons V2 Factory Shadow` schedule now runs an independently
bounded **due-only spot-observation lane** in addition to HOT and COLD public
factory-log collection. It has no separate schedule or private Actions runner.

- **Source:** only original, already committed public HOT launch receipts and
  their verified event-file digest. The event is observed, not an exhaustive
  launch census. Indexed source identity is chain 4663 + exact token and curve,
  not ticker. No private Alpha Lab watchlists, wallet positions or secrets.
- **Freeze:** at most two new distinct public HOT tokens per UTC day and 240
  lifetime. Research T0 starts when the public observer first creates the
  frozen record. The earlier source log/block is never upgraded to first-known
  T0, and source-chain launch time is not a provider price timestamp.
- **Outcome:** fixed initial public DexScreener token-pair observation and
  due-only +24h and +72h snapshots, maximum four short-timeout requests per
  run. Pair selection occurs only at baseline and cannot chase whichever later
  pool rises. A not-yet-indexed baseline generates immutable `BASELINE_ATTEMPT_N`
  receipts, at most eight attempts during the original four-hour T0 window;
  it does not instantly become a terminal loss or success. A later recovered
  baseline retains its **actual** observation timestamp, never pretends to be
  a price known at the original T0. Past frozen baseline rows remain unchanged.
  Captures more than four hours late become `WINDOW_MISSED`, and
  source errors or missing baseline remain explicitly `UNKNOWN`.
- **Integrity:** hashed, append-only frozen T0 and outcomes; a dedicated stage
  validator independently binds the original HOT receipt and validates all
  receipt hashes, immutable T0 and candidate identity, expected event deltas,
  public-only allowlisted fields, and separate forward-state cursor before the
  existing restricted publisher can push.
- **Scientific limit:** `PUBLIC_RESEARCH_ONLY`, `first_known_verified=false`,
  `execution_verified=false`, `realizable_net_return=null`, no alert,
  signing or trade. Even a positive spot move does not prove an achievable
  entry, fee-adjusted exit or economic edge. Public source gaps and GitHub
  schedule delays remain visible and do not become source-complete receipts.

This separate public Pons experiment does **not** replace private Alpha source
#120 or outcome #77, nor backfill historical missed windows. Admitting public
spot records to private research would require an independent source-clock,
chain/CA, privacy, denominator and execution quote gate. Followup cohorts
remain fixed and public-only.

## Authority boundary

No market order or wallet transaction. No portfolio action. No automatic rule ratification. No model-weight change. No canonical promotion. No self-merge of experimental findings.

## Public Pons 5-minute HOT cadence, bounded owner pilot (2026-11-07)

The existing `Public Pons V2 Factory Shadow` public-repository workflow
has been configured for 12 UTC HOT observation opportunities per hour:
`:02, :07, :12, :17, :22, :27, :32, :37, :42, :47, :52, :57`.
This is **not** a promise that GitHub delivers a schedule on time.
It uses the same public read-only source and the existing verified
HOT collector, source validator, artifact handoff and single guarded publisher.
No new scanner, workflow, identity, secret, paid API or private runner.

- **Every scheduled opportunity:** bounded HOT recent-tail source observation, exact
  chain 4663/factory/topic and anchor check, explicit unknown historical gaps,
  immutable source receipt and independent publish gate. No canonical live/
  first-known promotion or BUY authority.
- **Only :27:** original COLD/exhaustive and original historical recovery.
- **Only :27 and :57:** original due-only forward outcomes and native-curve
  research, to avoid sixfold growth in external quote/API requests.
- **Pull request:** original full offline and workflow-security CI only, no
  network source scans or write token in PR jobs.
- **Push and manual dispatch:** original bounded behaviors retained.

The public standard GitHub-hosted runner does not consume private Actions
minutes, but free public compute does **not** remove provider limits, Git
history/artifact storage growth, or resource quotas. Public receipt data may
be copied to a private domain only through its original safe import/contract
and remains `PUBLIC_RESEARCH_ONLY`; none of this yields canonical
prospective first-known or public website permission.

**Acceptance is natural-run evidence, not PR CI alone:** check an actual
postmerge `event=schedule` with the new cron, original source-scoped HOT
receipt, monotonic correct cursor/continuity or explicit UNKNOWN gaps, independent
published-main readback, and no extra scheduled private job. Check several
successive natural runs and 24h actual API/log sizes/HTTP failures before
classifying cadence as *operationally effective*. If delays, provider throttling,
gaps or repo growth worsen, **do not** claim a 5-minute data SLA. Revert or
reduce frequency with an exact-head reviewed owner PR while preserving existing
source receipts. The 90-minute source/first-known qualification remains
independently binding in the Alpha Lab original owner #120/#70.

## Existing public Pons source: Gap Intelligence read-only log (2026-11-07)

The original `public-pons-shadow.yml` collect job now runs
`public_pons_shadow/gap_observer_v1.py` **after** HOT source capture and **before** the
original publisher. This is an offline metadata validator, not an additional
scanner/provider/scheduler or writer. The only output is one compact
`PUBLIC_PONS_GAP_INTELLIGENCE {json}` line in existing GitHub job logs.
No extra token/market-address export, API call, file artifact or commit.

The diagnostic verifies the exact public source checkout/run ID, current
authenticated-by-hash HOT state, the original immutable receipt file and
its original events digest, explicit unknown blocks in the original HOT gap
ledger, and the source scan completion clock. Original event bytes remain
inside the existing source producer and are never copied into the diagnostic.
It reports a new gap as an observed unscanned block interval, **not** as
recovered original launches, an independent provider attestation or prospective
first-known. It cannot prove the intended exact cron time because
`github.event.schedule` gives the matching *expression*, not which delayed
5-minute occurrence was originally due. GitHub run `created_at` and past
expected delivery require an independent external check; S5 already owns
the deduplicated hourly read-only observation and original issue #120 receipts.

Source capture failure produces `UNKNOWN_CAPTURE_NOT_PRESENT`, not zero
launches or a fake negative outcome. A self-hash mismatch, injected JSON,
wrong SHA/run, an unsafe path, malicious clock or unexpected source authority
causes the observer to reject input. The diagnostic is NON-BINDING to the
original source publisher: failure emits a visible job warning but cannot
create, overwrite or promote any source data. The original
`publish_gate.py` remains the sole write admission boundary. The public
source may be older, partial or delayed even if this diagnostic passes.

For future affordable gap challenges, first compare the original immutable
public HOT/COLD receipts against an independently admitted same-chain public
RPC or explorer within its provider quotas. Such a cross-check remains
`RESEARCH_ONLY`, with PASS/FAIL/UNKNOWN and no hindsight credit. Run it
only on material distinct gap fingerprints, not on every 5-minute slot.
