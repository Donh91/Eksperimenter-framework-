# Public Pons v2 exhaustive factory shadow, W1

**Scope:** public-only observation. Not a private Alpha Lab source producer, first-known attestor, token selection signal or trading/alert authority.

## Temporary public location

The canonical desired destination under Archive #1865 and Grok Bridge #118 is a new isolated public compute repository with a ruleset and CODEOWNERS. That repo does not yet exist, and this GitHub connector cannot create repositories. Existing PUBLIC Experiment Execution Plane is used for an immediate temporary W1 public-only shadow, with no private credentials or inputs. This is not a hardened dedicated compute enclave. Never grant this workflow access to Alpha, secrets, private watchlists, private cohorts, provider tokens or trading algorithms.

## Capture contract

- Robinhood Chain ID 4663; public Pons V2 factory 0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e and TokenLaunched topic0 0x8d4aad4953d0ca700d468f3753aa14432d1b35b43ec6409f051fb6aa43a89607. Both are externally documented public chain identifiers.
- Public standard GitHub Actions source job hourly at minute 27 UTC, not a reusable workflow invoked from private Alpha. Shares existing experiment-execution-plane concurrency group for known same-repo main writers, not a global lock against unrelated actors.
- First run scans only the last 40,000 safe blocks and labels bootstrap_not_full_history=true. Later runs continue exactly from prior public last_scanned_block + 1, at most 60,000 blocks/run and 2,000 blocks/RPC call. Remaining backlog stays explicit; never infer full historical coverage.
- Immutable public runs/YYYY/MM/DD/*_events.json and *_receipt.json plus a mutable state.json with hash-bound payload. All Pons v2 factory events in each bounded range, no selected token universe or private/watchlist feed.
- Fail closed on RPC error, wrong chain/factory/topic, malformed log, duplicate, cap reached, corrupt cursor, unverifiable end block or remote Git push/readback. Event block clock is NOT GitHub publication clock or an independent provider-first-known clock.
- provider_first_known_verified=false, github_first_publication_verified=false, private_source_admitted=false, portfolio_action=false, alert_authority=false. Research only; no market BUY/SELL.

## Release tests and scientific hold

Python unittest discover -s tests -v and positive/negative deterministic unit/authority cases in tests/test_public_pons_shadow.py. Existing Experiment Execution Plane PR gate already watches tests/** and runs the full offline test collection. New workflow is scheduled/dispatch-only on its own public main.

A real hosted W1 source run, original GitHub publication and exact main readback are mandatory before any assertion of operational capture. Then obtain 48-72h public source coverage and a governed source-hash-pinned private importer with independent source clock, no dual cursor writer and tested rollback, before original private Alpha #120 may ratify cutover. Preserve the original 2026-10-21 Pons cursor and classify historical backfill separately from new live-tail. Private zero-step CI and missing early outcomes remain UNKNOWN. No prospective trading or economic edge implied.

## Stop and rollback

Disable the public workflow or revert its installation commit. Never force-push historic events. No private Alpha producer, workflow or source data changed by this public-only pilot. If private content appears in public, halt and follow the established incident process. Measure actual billing rather than extrapolating savings.
