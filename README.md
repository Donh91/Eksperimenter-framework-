# Eksperimenter-framework-

Private execution sandbox for non-canonical research and one-shot data recovery.

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
no repository write
artifact retention: 7 days
```

The workflow intentionally fails its final validation step if fewer than all 12 endpoints are fetched and parsed. The artifact is still uploaded first so failures remain auditable.

## Manual run

Open **Actions** → **Fetch DeFiLlama stablecoin and DEX history** → **Run workflow**.

After completion, download:

`DEFILLAMA_STABLECOIN_AND_DEX_HISTORY_6_ENTITIES`

Upload that artifact to the Investering analysis thread for canonical validation and ingestion.

## Authority boundary

No market call. No portfolio action. No scoring. No rule ratification.
