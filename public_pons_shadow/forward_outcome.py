#!/usr/bin/env python3
"""Public Pons HOT -> due-only forward spot observations (never executable alpha).

Uses ONLY already published public on-chain HOT receipts, never private Alpha
cohorts. T0 is the time this script freezes a new candidate, NOT launch time.
This is a limited source-delivery experiment, not a census, trading or alert feed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from public_pons_shadow import collector as cold
except ImportError:
    import collector as cold

CONTRACT = "PUBLIC_PONS_FORWARD_SPOT_RESEARCH_V1"
STATE = Path("public_pons_shadow/forward_state.json")
HISTORY = Path("public_pons_shadow/forward/runs")
MAX_NEW_DAILY = 2
MAX_CALLS_PER_RUN = 4
MAX_BASELINE_ATTEMPTS = 8
MAX_TRACKED = 240
MAX_SOURCE_AGE = timedelta(hours=2)
MAX_LATE = timedelta(hours=4)
HORIZONS = ((24, "24H"), (72, "72H"))
TOKEN = re.compile(r"0x[a-f0-9]{40}\Z")
RUN_ID = re.compile(r"[0-9]{1,20}\Z")
SHA = re.compile(r"[a-f0-9]{40}\Z")


def now_utc():
    return datetime.now(timezone.utc)


def stamp(value):
    return cold.iso(value)


def parse_utc(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("FORWARD_CLOCK_INVALID")
    dt = datetime.fromisoformat(value[:-1] + "+00:00")
    if dt.utcoffset() != timedelta(0):
        raise ValueError("FORWARD_CLOCK_INVALID")
    return dt


def load_public_hot(root, now, *, exclude_run_id=None):
    """Admit at most one previously published HOT batch, never this run's staged HOT."""
    paths = sorted((root / "public_pons_shadow/hot/runs").glob("**/*_receipt.json"), reverse=True)
    for rp in paths[:30]:
        if rp.is_symlink() or rp.stat().st_size > 2_000_000:
            continue
        try:
            r = json.loads(rp.read_text())
            if r.get("github_run_id") == exclude_run_id:
                continue  # Newly captured in working tree, not yet published.
            if (r.get("sha256") != cold.hash_obj({k: v for k, v in r.items() if k != "sha256"})
                or r.get("authority") != "PUBLIC_HOT_SHADOW_ONLY"
                or r.get("contract") != "PUBLIC_PONS_V2_HOT_SHADOW_V1"
                or r.get("chain_id") != cold.CHAIN_ID or r.get("factory") != cold.FACTORY
                or r.get("topic0") != cold.TOPIC0
                or any(r.get(k) is not False for k in ("private_source_admitted",
                      "canonical_census_complete", "portfolio_action", "alert_authority"))
                or r.get("real_time_window_candidate") is not True):
                continue
            seen = parse_utc(r["scan_finished_at_utc"])
            if not timedelta(0) <= now - seen <= MAX_SOURCE_AGE:
                continue
            ep = rp.with_name(rp.name.replace("_receipt.json", "_events.json"))
            if ep.is_symlink() or ep.stat().st_size > 2_000_000:
                continue
            events = json.loads(ep.read_text())
            if not isinstance(events, list) or len(events) != r.get("event_count"):
                continue
            if cold.hash_obj(events) != r.get("events_sha256"):
                continue
            valid = []
            for e in events:
                ts = e.get("topics") if isinstance(e, dict) else None
                if (not isinstance(ts, list) or len(ts) != 4 or ts[0] != cold.TOPIC0
                    or not all(re.fullmatch(r"0x[a-f0-9]{64}", x or "") for x in ts)
                    or not r["from_block"] <= e.get("block", -1) <= r["to_block"]):
                    continue
                token = "0x" + ts[1][-40:]
                pool = "0x" + ts[2][-40:]
                if TOKEN.fullmatch(token) and int(token[2:], 16) and int(pool[2:], 16):
                    valid.append((e["block"], e["log_index"], token, pool, e["tx_hash"]))
            # Original deterministic log order, never return/price-sorted.
            valid.sort()
            return rp.relative_to(root).as_posix(), r["sha256"], seen, valid
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    return None


def read_spot(token, pair=None):
    """A single bounded public provider request. No quote, fill or source timestamp."""
    url = "https://api.dexscreener.com/tokens/v1/robinhood/" + token
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                 "User-Agent": "PublicPonsForwardShadow/1.0"})
    with urllib.request.urlopen(req, timeout=4) as rsp:
        if rsp.status != 200:
            raise ValueError("PROVIDER_HTTP_NON_200")
        raw = rsp.read(200_001)
        if len(raw) > 200_000:
            raise ValueError("PROVIDER_RESPONSE_TOO_LARGE")
    body = json.loads(raw)
    if not isinstance(body, list):
        raise ValueError("PROVIDER_SCHEMA_UNKNOWN")
    eligible = []
    for x in body[:80]:
        if not isinstance(x, dict) or x.get("chainId") != "robinhood":
            continue
        base = x.get("baseToken") or {}
        address = str(base.get("address") or "") if isinstance(base, dict) else ""
        p = str(x.get("pairAddress") or "").lower()
        if address.lower() != token or not TOKEN.fullmatch(p):
            continue
        liquidity = x.get("liquidity") or {}
        try:
            liq = float(liquidity.get("usd")) if isinstance(liquidity, dict) else 0.0
            price = float(x.get("priceUsd"))
        except (ValueError, TypeError):
            continue
        if not 0 < liq < 1e15 or not 0 < price < 1e15:
            continue
        if pair is not None and p != pair:
            continue
        eligible.append((liq, p, str(x["priceUsd"])))
    if not eligible:
        return {"state": "UNKNOWN_EXACT_PUBLIC_PAIR", "price_usd": None, "pair": None}
    eligible.sort(key=lambda z: (-z[0], z[1]))
    # Pair fixed at baseline: never migrate to whichever pool later outperforms.
    _, chosen, px = eligible[0]
    return {"state": "PUBLIC_SPOT_OBSERVED_NON_EXECUTABLE",
            "price_usd": px, "pair": chosen}


def run(root, run_id, checkout_sha, *, clock=now_utc, reader=read_spot):
    if not RUN_ID.fullmatch(run_id or "") or not SHA.fullmatch(checkout_sha or ""):
        raise ValueError("FORWARD_RUN_IDENTITY_INVALID")
    start = clock()
    if start.tzinfo is None or start.utcoffset() != timedelta(0):
        raise ValueError("FORWARD_NOW_MUST_BE_UTC")
    statepath = root / STATE
    previous = json.loads(statepath.read_text()) if statepath.exists() else None
    if previous is not None and (not isinstance(previous, dict)
        or previous.get("contract") != CONTRACT
        or previous.get("authority") != "PUBLIC_RESEARCH_ONLY"
        or previous.get("sha256") != cold.hash_obj({k: v for k, v in previous.items() if k != "sha256"})
        or not isinstance(previous.get("candidates"), list)
        or len(previous["candidates"]) > MAX_TRACKED):
        raise ValueError("FORWARD_PREVIOUS_STATE_INVALID")
    candidates = json.loads(json.dumps(previous["candidates"])) if previous else []
    events = []
    calls = 0
    seen_ids = {c["id"] for c in candidates}
    today = stamp(start)[:10]
    daily = sum(c["t0_utc"].startswith(today) for c in candidates)
    # This source may be missing: that is UNKNOWN, not evidence of zero launches.
    source = load_public_hot(root, start, exclude_run_id=run_id) if daily < MAX_NEW_DAILY else None
    if source and len(candidates) < MAX_TRACKED:
        path, sha, observed, raw = source
        for block, index, token, pool, tx in raw:
            if daily >= MAX_NEW_DAILY or len(candidates) >= MAX_TRACKED:
                break
            identity = cold.hash_obj({"chain_id": cold.CHAIN_ID, "token": token, "source_tx": tx,
                                      "block": block, "log_index": index})[:24]
            if identity in seen_ids or any(c["token"] == token for c in candidates):
                continue
            t0 = clock()   # T0 only now, never time of block or prior source receipt.
            candidate = {"id": identity, "chain_id": cold.CHAIN_ID, "token": token,
                         "source_pool": pool, "source_receipt": path, "source_receipt_sha256": sha,
                         "source_machine_observed_utc": stamp(observed),
                         "t0_utc": stamp(t0), "selection": "FIRST_VALID_PUBLIC_HOT_LOGS_MAX2_DAILY",
                         "source_coverage": "PARTIAL_PUBLIC_HOT", "first_known_verified": False,
                         "paper_entry_authority": False, "observations": {}}
            candidates.append(candidate)
            seen_ids.add(identity)
            daily += 1
            events.append({"id": identity, "horizon": "FREEZE", "captured_at_utc": stamp(t0),
                           "state": "SHADOW_CANDIDATE_ONLY", "source_receipt_sha256": sha})
    for c in candidates:
        for hours, label in ((0, "BASELINE"), *HORIZONS):
            if label in c["observations"]:
                continue
            due = parse_utc(c["t0_utc"]) + timedelta(hours=hours)
            at = clock()
            if at < due:
                continue
            if (label == "BASELINE" and at <= due + MAX_LATE
                and sum(k.startswith("BASELINE_ATTEMPT_") for k in c["observations"]) >= MAX_BASELINE_ATTEMPTS):
                continue
            if at > due + MAX_LATE:
                result = {"state": "WINDOW_MISSED", "price_usd": None, "pair": None}
            elif calls >= MAX_CALLS_PER_RUN:
                continue
            else:
                calls += 1
                try:
                    binding = c["observations"].get("BASELINE", {}).get("pair")
                    if label != "BASELINE" and not binding:
                        result = {"state": "UNKNOWN_NO_BASELINE_PAIR", "price_usd": None, "pair": None}
                        calls -= 1  # No provider request was made.
                    else:
                        result = reader(c["token"], binding if label != "BASELINE" else None)
                except (OSError, ValueError, TimeoutError, urllib.error.URLError,
                        json.JSONDecodeError, RuntimeError):
                    result = {"state": "UNKNOWN_PROVIDER_ERROR", "price_usd": None, "pair": None}
            captured = clock()
            # Eligibility is determined at the response completion clock, not
            # merely before the source request. Never credit a late response.
            if captured > due + MAX_LATE and result["state"] != "WINDOW_MISSED":
                result = {"state": "WINDOW_MISSED", "price_usd": None, "pair": None}
            # Missing public prices during the first four hours are retryable,
            # not a successful/failed/terminal BASELINE. Never rewrite an
            # existing frozen observation, including old terminal UNKNOWN rows.
            attempt = (label == "BASELINE" and captured <= due + MAX_LATE
                       and result["state"] in ("UNKNOWN_EXACT_PUBLIC_PAIR", "UNKNOWN_PROVIDER_ERROR"))
            key = ("BASELINE_ATTEMPT_" + str(1 + sum(
                   k.startswith("BASELINE_ATTEMPT_") for k in c["observations"]))) if attempt else label
            observation = {"horizon": "BASELINE_ATTEMPT" if attempt else label,
                           "target_utc": stamp(due), "captured_at_utc": stamp(captured),
                           "state": result["state"], "price_usd": result.get("price_usd"),
                           "pair": result.get("pair"), "provider_timestamp_verified": False,
                           "execution_verified": False, "realizable_net_return": None}
            c["observations"][key] = observation
            events.append({"id": c["id"], **observation})
    if not events:
        return {"status": "NO_DUE_OR_ELIGIBLE_PUBLIC_SAMPLE", "source_calls": calls}
    finished = clock()
    receipt = cold.signed({"contract": CONTRACT, "authority": "PUBLIC_RESEARCH_ONLY",
        "run_id": run_id, "checkout_sha": checkout_sha,
        "started_at_utc": stamp(start), "finished_at_utc": stamp(finished),
        "source_calls": calls, "event_count": len(events),
        "events_sha256": cold.hash_obj(events),
        "previous_state_sha256": previous.get("sha256") if previous else None,
        "canonical_source_admitted": False, "historical_replay": False,
        "first_known_verified": False, "portfolio_action": False,
        "paper_execution": False, "alert_authority": False})
    state = cold.signed({"contract": CONTRACT, "authority": "PUBLIC_RESEARCH_ONLY",
        "run_id": run_id, "last_receipt_sha256": receipt["sha256"],
        "candidates": candidates, "alert_authority": False, "portfolio_action": False})
    stem = start.strftime("%Y%m%dT%H%M%SZ") + "_" + run_id
    folder = root / HISTORY / start.strftime("%Y/%m/%d")
    rp = folder / (stem + "_receipt.json")
    ep = folder / (stem + "_events.json")
    if rp.exists() or ep.exists():
        raise ValueError("FORWARD_IMMUTABLE_RUN_COLLISION")
    folder.mkdir(parents=True, exist_ok=True)
    rp.write_bytes(cold.encode(receipt))
    ep.write_bytes(cold.encode(events))
    statepath.parent.mkdir(parents=True, exist_ok=True)
    statepath.write_bytes(cold.encode(state))
    return {"status": "PUBLIC_FORWARD_CAPTURED_UNPUBLISHED",
            "receipt": rp.relative_to(root).as_posix(),
            "source_calls": calls, "event_count": len(events)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", type=Path, default=Path("."))
    p.add_argument("--validate-only", action="store_true")
    args = p.parse_args()
    if args.validate_only:
        assert MAX_CALLS_PER_RUN <= 4 and MAX_NEW_DAILY <= 2
        print("PUBLIC_FORWARD_RESEARCH_ONLY_POLICY_PASS")
        return
    outcome = run(args.repo_root.resolve(), os.environ.get("GITHUB_RUN_ID", ""),
                  os.environ.get("GITHUB_CHECKOUT_SHA", ""))
    print(json.dumps(outcome, sort_keys=True))


if __name__ == "__main__":
    main()
