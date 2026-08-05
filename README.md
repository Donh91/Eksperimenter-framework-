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

## Authority boundary

No market call. No portfolio action. No automatic rule ratification. No model-weight change. No canonical promotion. No self-merge of experimental findings.
