#!/usr/bin/env python3
"""Independent recent-tail Pons factory observation, never canonical LIVE credit.

HOT is deliberately a separate public cursor. Unscanned intervals are recorded as
gaps, not interpolated; earlier COLD/canonical sources retain their own cursors.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

try:
    from public_pons_shadow import collector as cold
except ImportError:
    import collector as cold

STATE = Path("public_pons_shadow/hot_state.json")
CONTRACT = "PUBLIC_PONS_V2_HOT_SHADOW_V1"
WINDOW = 60000
MAX_EVENTS = cold.MAX_EVENTS


def hot_capture(root, client, run_id, checkout_sha, clock=cold.now, window=WINDOW):
    if type(window) is not int or not 1 <= window <= WINDOW:
        raise ValueError("HOT_WINDOW_UNBOUNDED")
    if re.fullmatch(r"[0-9]{1,20}", run_id or "") is None:
        raise ValueError("HOT_RUN_ID_INVALID")
    if re.fullmatch(r"[a-f0-9]{40}", checkout_sha or "") is None:
        raise ValueError("HOT_SOURCE_SHA_INVALID")
    started = clock()
    state_path = root / STATE
    previous = None
    if state_path.exists():
        previous = json.loads(state_path.read_text())
        if (not isinstance(previous, dict)
            or previous.get("sha256") != cold.hash_obj({k:v for k,v in previous.items() if k != "sha256"})
            or previous.get("contract") != CONTRACT or previous.get("chain_id") != cold.CHAIN_ID
            or previous.get("factory") != cold.FACTORY or previous.get("authority") != "PUBLIC_HOT_SHADOW_ONLY"
            or type(previous.get("last_observed_block")) is not int
            or previous["last_observed_block"] < 0
            or cold.HEX32.fullmatch(previous.get("last_observed_block_hash","")) is None):
            raise RuntimeError("HOT_STATE_UNTRUSTED")
    client.connect()
    safe_head = cold.as_int(client.call("eth_blockNumber", [])) - 2
    if safe_head < 0:
        raise RuntimeError("HOT_SAFE_HEAD_INVALID")
    prev_end = previous["last_observed_block"] if previous else None
    if prev_end is not None and safe_head <= prev_end:
        return {"status":"NO_NEW_HOT_SAFE_BLOCK","safe_head":safe_head}
    if prev_end is not None:
        anchor=client.call("eth_getBlockByNumber",[hex(prev_end),False])
        if (not isinstance(anchor,dict)
            or cold.as_int(anchor.get("number"))!=prev_end
            or str(anchor.get("hash","")).lower()!=previous["last_observed_block_hash"]):
            raise RuntimeError("HOT_REORG_ANCHOR_MISMATCH_NO_ADVANCE")
    lo = max(0, safe_head-window+1)
    if prev_end is not None:
        # Include the previous boundary to detect unexpected reorgs. Never
        # pretend that an unobserved multi-hour interval was covered.
        lo = max(lo, prev_end)
    hi = safe_head
    gaps = list(previous.get("unobserved_gaps", [])) if previous else []
    if prev_end is not None and lo > prev_end + 1:
        gaps.append({"from_block":prev_end+1,"to_block":lo-1,
                     "reason":"HOT_MISSED_SCHEDULE_UNKNOWN_COLD_COVERAGE"})
    if len(gaps) > 1000:
        raise RuntimeError("HOT_GAP_LEDGER_CAP_REACHED")
    rows = []
    calls = 0
    for low in range(lo, hi+1, cold.MAX_CHUNK):
        high = min(hi,low+cold.MAX_CHUNK-1)
        raw = client.call("eth_getLogs", [{"fromBlock":hex(low),"toBlock":hex(high),
                                          "address":cold.FACTORY,"topics":[cold.TOPIC0]}])
        if not isinstance(raw, list):
            raise RuntimeError("HOT_LOG_RESULT_NOT_ARRAY")
        if len(rows)+len(raw) > MAX_EVENTS:
            raise RuntimeError("HOT_EVENT_CAP_NO_ADVANCE")
        rows.extend(cold.validate_log(x,low,high) for x in raw)
        calls += 1
    rows.sort(key=lambda x:(x["block"],x["log_index"],x["tx_hash"]))
    if len({(x["block"],x["log_index"]) for x in rows}) != len(rows):
        raise RuntimeError("HOT_LOG_POSITION_COLLISION")
    block_hashes = {}
    for row in rows:
        b = block_hashes.setdefault(row["block"],row["block_hash"])
        if b != row["block_hash"]:
            raise RuntimeError("HOT_BLOCK_HASH_CONFLICT")
    final = client.call("eth_getBlockByNumber", [hex(hi),False])
    if not isinstance(final,dict) or cold.as_int(final.get("number")) != hi:
        raise RuntimeError("HOT_HEAD_NOT_VERIFIED")
    final_hash=str(final.get("hash","")).lower()
    if cold.HEX32.fullmatch(final_hash) is None:
        raise RuntimeError("HOT_HEAD_HASH_INVALID")
    ts = cold.as_int(final.get("timestamp"))
    if ts <= 0 or ts > int(clock().timestamp()) + 60:
        raise RuntimeError("HOT_BLOCK_CLOCK_INVALID")
    ended = clock()
    age = int((started-datetime.fromtimestamp(ts,timezone.utc)).total_seconds())
    if age < -60:
        raise RuntimeError("HOT_FUTURE_BLOCK_CLOCK")
    receipt = cold.signed(dict(
        contract=CONTRACT,authority="PUBLIC_HOT_SHADOW_ONLY",
        chain_id=cold.CHAIN_ID,factory=cold.FACTORY,topic0=cold.TOPIC0,
        from_block=lo,to_block=hi,safe_head=safe_head,observed_head_block_hash=final_hash,
        previous_hot_block=prev_end,previous_hot_state_sha=previous.get("sha256") if previous else None,
        complete_scanned_window=True,history_complete=False,
        missed_hot_ranges=gaps,missed_hot_range_count=len(gaps),
        source="PUBLIC_ROBINHOOD_RPC",rpc_url=client.url,
        block_utc=cold.iso(datetime.fromtimestamp(ts,timezone.utc)),
        scan_started_at_utc=cold.iso(started),scan_finished_at_utc=cold.iso(ended),
        observed_end_block_age_seconds=age,real_time_window_candidate=age <= 5400,
        github_run_id=run_id,github_checkout_sha=checkout_sha,
        events_sha256=cold.hash_obj(rows),event_count=len(rows),rpc_log_calls=calls,
        github_first_publication_verified=False,provider_first_known_verified=False,
        canonical_census_complete=False,private_source_admitted=False,
        portfolio_action=False,alert_authority=False))
    state=cold.signed(dict(
        contract=CONTRACT,authority="PUBLIC_HOT_SHADOW_ONLY",
        chain_id=cold.CHAIN_ID,factory=cold.FACTORY,topic0=cold.TOPIC0,
        last_observed_block=hi,last_observed_block_hash=final_hash,
        last_receipt_sha256=receipt["sha256"],
        last_run_id=run_id,unobserved_gaps=gaps))
    stem=started.strftime("%Y%m%dT%H%M%SZ")+"_"+run_id
    folder=root/"public_pons_shadow"/"hot"/"runs"/started.strftime("%Y/%m/%d")
    event_file=folder/(stem+"_events.json")
    receipt_file=folder/(stem+"_receipt.json")
    if event_file.exists() or receipt_file.exists():
        raise RuntimeError("HOT_IMMUTABLE_RUN_EXISTS")
    folder.mkdir(parents=True,exist_ok=True)
    event_file.write_bytes(cold.encode(rows))
    receipt_file.write_bytes(cold.encode(receipt))
    state_path.write_bytes(cold.encode(state))
    return {"status":"PUBLIC_HOT_CAPTURED_UNPUBLISHED","from_block":lo,"to_block":hi,
            "events":len(rows),"missed_hot_range_count":len(gaps),
            "receipt":str(receipt_file.relative_to(root))}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--repo-root",type=Path,default=Path("."))
    ap.add_argument("--validate-only",action="store_true")
    args=ap.parse_args()
    if args.validate_only:
        assert 0<WINDOW<=cold.MAX_SCAN and cold.CHAIN_ID==4663
        print("PUBLIC_PONS_HOT_POLICY_PASS")
        return
    result=hot_capture(args.repo_root.resolve(),cold.Client(),
                       os.environ.get("GITHUB_RUN_ID",""),
                       os.environ.get("GITHUB_CHECKOUT_SHA",""))
    print(json.dumps(result,sort_keys=True))


if __name__=="__main__":
    main()
