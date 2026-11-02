# PONS #120, public HOT/COLD source recovery audit
Date: 2026-11-02. Status: RESEARCH CANARY, Draft PR #14, not deployed. Public-only source, no private Alpha data.

## Actual immutable public COLD receipts
Source: public_pons_shadow/runs in Donh91/Eksperimenter-framework- (10 captured runs between Oct 31 15:08Z and Nov 2 07:57Z).

| GitHub run | Scan time UTC | COLD cursor end | Safe chain head | Backlog blocks | Events |
|---|---|---:|---:|---:|---:|
| 41213409418 | Oct 31 15:08 | 102644722 | 102644722 | 0 | 55 |
| 41216687687 | Oct 31 15:49 | 102668537 | 102668537 | 0 | 45 |
| 41234217435 | Oct 31 19:26 | 102728537 | 102794606 | 66069 | 100 |
| 41249588279 | Oct 31 22:45 | 102788537 | 102910342 | 121805 | 79 |
| 41265191513 | Nov 1 02:26 | 102848537 | 103037399 | 188862 | 52 |
| 41293912854 | Nov 1 09:37 | 102908537 | 103285130 | 376593 | 38 |
| 41328579407 | Nov 1 16:53 | 102968537 | 103537733 | 569196 | 42 |
| 41351455437 | Nov 1 21:20 | 103028537 | 103691739 | 663202 | 27 |
| 41369921584 | Nov 2 00:57 | 103088537 | 103816928 | 728391 | 23 |
| 41409568028 | Nov 2 07:57 | 103148537 | 104059063 | **910526** | 31 |

No unscanned COLD block interval occurs between these recorded receipts. Last eight COLD runs advanced 60k blocks each with 30 RPC log calls. Actual interrun delays reached 7h16m. Last terminal block timestamp at 05:40:54Z on Nov 1 is over 26 hours before latest Nov 2 scan.

## Root cause and modeled capacity
Safe head grew 1,414,341 blocks across about 40.81 hours, or ~34,650 blocks/hour. Existing COLD cap 60k/run needs one success at least every ~104 minutes just to keep backlog flat. Observed intervals of 3.3h to 7.3h explain observed backlog growth. This is a scheduling/capacity deficit, not evidence that COLD skipped any of its recorded ranges.

| Successful schedule interval | Max COLD captured blocks/hour | Net backlog drift vs observed chain |
|---|---:|---:|
| Every 7h | 8,571 | +26,079 blocks/h |
| Every 4h | 15,000 | +19,650 blocks/h |
| Every 2h | 30,000 | +4,650 blocks/h |
| Every 60m | 60,000 | -25,350 blocks/h |

At reliable hourly success, the current ~910k backlog would require ~36 hours to converge if provider, chain growth and all receipts behave as modeled. This is a scenario, NOT a verified ETA or guaranteed GitHub cron service level.

## Implemented bounded Draft PR #14
- One existing public workflow, no new scheduler, repo, secrets, private runners or BUY authority.
- HOT independently scans newest up to 60,000 safe blocks and maintains its own cursor, immutable receipts, source clock, safe-head hash, reorg prior-anchor check and explicit ledger of late/unobserved intervals. If GitHub misses 7h, HOT gains fresh data but records all skipped early-observation windows as UNKNOWN.
- COLD retains original exhaustive contiguous 60k source cursor and existing data contract. HOT/COLD can fail independently while a successful lane still publishes.
- Source/RPC script uses only contents:read; credentials are not persisted at checkout. Separate restricted contents:write job accepts only 3 or 6 validated JSON artifacts, rejects path/symlink/shape/hash/run-ID/source-code SHA/cursor anomalies and verifies remote main blob readback.
- Existing PR gate and new deterministic/security tests are executed on hosted GitHub runners; no production HOT output has been verified or promoted yet.
- Self-computed SHA256 is only integrity, NOT a signature or independent GitHub principal binding. Public main had zero active rulesets. Private Alpha #203 remains offline, no import/admission.

## Required release evidence
1. Exact current PR HEAD, substantive hosted CI and independent adversarial Grok review.
2. Main write-authority and recovery safepoint, including enforced repository settings. CODEOWNERS text alone does not enforce a ruleset.
3. Controlled main merge and first actual HOT/COLD collection plus write-job remote readback, negative/reorg validation and no leaked private data.
4. Two consecutive timely natural HOT observations with honest gap ledger.
5. Subsequent 48-72h real schedule, block-age, observed coverage and COLD convergence trend, with visible RPC failures and no manufactured LIVE completeness.
6. Separate private Alpha #120/#203 source authentication and scientific admission. No retrospective first-known or trading/alert authority.

Private Actions billed-minute savings, future provider availability and production end-to-end performance remain UNKNOWN.

## Actual read-only, non-persistent source canary proof (2026-11-02)

These two genuine GitHub pull_request-triggered jobs used the original PR #14
with contents:read / persist-credentials:false and executed real public RPC
capture from Robinhood Chain. No output committed, no private scientific
admission. The temporary live-source-canary job was removed from the final
production candidate after collecting reproducible read-only evidence.

| UTC observed | Safe source range | Events | RPC log calls | Terminal block age | Status |
|---|---|---:|---:|---:|---|
| 11:37:15 | 104125512..104185511 | 113 | 30 | 1 second | PASS |
| 11:39:24 | 104126718..104186717 | 112 | 30 | 1 second | PASS |

Both used https://rpc.mainnet.chain.robinhood.com and left original public
main COLD cursor untouched. Adjacent ephemeral HOT windows intentionally
have overlapping source blocks; **not two independent canonical first-known
runs and not 48h natural source stability**.

Final PR requires new exact-head substantive hosted CI and separate publisher
release proof. The public main writing workflow has not been exercised in
this draft and still has no enforced rulesets.

## PONS #120 bounded COLD capacity canary, 2 November 2026

Source: GitHub Actions public PR #15, genuine read-only RPC job
[run 41453736034](https://github.com/Donh91/Eksperimenter-framework-/actions/runs/41453736034),
conclusion SUCCESS. Tested 300,000 contiguous historical Robinhood Chain blocks,
150 calls to eth_getLogs, 407 factory events, **12.1 seconds wall clock**,
final receipt remaining blocks 744,026 **inside temporary runner directory only**.
No remote main cursor was modified by this benchmark. Provider used:
https://rpc.mainnet.chain.robinhood.com. A single successful canary is
not a 48-72h provider reliability guarantee and does not authenticate first-known.

The old 60,000-block cap required an average successful run interval below
approximately 104 minutes just to avoid more backlog (historical source growth
approximately 34,650 blocks/hour). 300,000 blocks/run supports up to about
8.7 hours between successful runs **at the measured chain growth rate**, but
only if the provider and publisher actually complete each run. This is a
performance sensitivity calculation, not a future cron delivery promise.

The candidate implementation preserves original HOT max window 60,000,
separate COLD cursor and public-shadow authority. Source RPC failures after a
strictly complete contiguous COLD prefix can publish only that prefix and
resume from its next block; failure before the first fully processed chunk
must not advance state. Invalid logs/chain IDs must still fail closed.

Release requires exact-head substantive hosted tests, cautious first real
300k production capture, on-main immutable receipts with remote readback,
then continuing monitoring for scheduling gaps, actual provider 429/errors,
source time, and COLD backlog slope. Private canonical collector remains
independent and unchanged. No private Alpha trades or source authority.
