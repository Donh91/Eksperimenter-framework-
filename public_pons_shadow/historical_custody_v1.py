#!/usr/bin/env python3
"""Audit independent public historical gap receipts, never canonical source."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
try:
    from public_pons_shadow import collector as cold, historical_recovery_v1 as hist
    from public_pons_shadow.coverage_custody_v1 import load_json, MAX_RECEIPTS
except ImportError:
    import collector as cold
    import historical_recovery_v1 as hist
    from coverage_custody_v1 import load_json, MAX_RECEIPTS

RECEIPT=re.compile(r"public_pons_shadow/historical/runs/\d{4}/\d{2}/\d{2}/"
                   r"\d{8}T\d{6}Z_\d{1,20}_receipt\.json\Z")
STATE_FIELDS={"contract","authority","chain_id","factory","topic0","last_scanned_block",
              "bootstrap_from_block","last_run_id","last_receipt_sha256","sha256"}
EVENT_FIELDS={"block","log_index","tx_hash","block_hash","topics","data"}


def audit(root, cold_state):
    root=Path(root)
    statepath=root/hist.STATE
    base=root/hist.RUNS
    receipts=sorted(base.rglob("*_receipt.json")) if base.exists() else []
    if not statepath.exists() and not receipts:
        return {
          "historical_status":"NO_HISTORICAL_SOURCE_YET",
          "historical_verified_receipt_count":0,
          "historical_verified_start_block":None,
          "historical_verified_end_block":None,
          "historical_verified_event_count":0,
          "historical_remaining_to_cold_bootstrap":None,
          "historical_bridge_self_consistent":False,
          "historical_state_sha256":None,
          "historical_canonical_source_admitted":False,
          "historical_first_known_verified":False,
        }
    if not 1<=len(receipts)<=MAX_RECEIPTS:
        raise ValueError("HISTORICAL_RECEIPT_SET_INVALID")
    if (type(cold_state.get("bootstrap_from_block")) is not int
        or cold_state["bootstrap_from_block"]<=hist.START_BLOCK):
        raise ValueError("HISTORICAL_BOOTSTRAP_BOUNDARY_INVALID")
    current=load_json(statepath)
    if (set(current)!=STATE_FIELDS
        or current.get("sha256")!=cold.hash_obj({k:v for k,v in current.items() if k!="sha256"})
        or current.get("contract")!=cold.CONTRACT
        or current.get("authority")!="PUBLIC_SHADOW_ONLY"
        or current.get("chain_id")!=cold.CHAIN_ID
        or current.get("factory")!=cold.FACTORY
        or current.get("topic0")!=cold.TOPIC0
        or current.get("bootstrap_from_block")!=hist.START_BLOCK):
        raise ValueError("HISTORICAL_STATE_UNTRUSTED")
    last=hist.START_BLOCK-1
    reconstructed=None
    total=0
    prev_clock=None
    for i,rp in enumerate(receipts):
        if not RECEIPT.fullmatch(rp.relative_to(root).as_posix()):
            raise ValueError("HISTORICAL_PATH_INVALID")
        receipt=load_json(rp)
        events=load_json(rp.with_name(rp.name.replace("_receipt.json","_events.json")))
        if (not isinstance(receipt,dict)
            or receipt.get("sha256")!=cold.hash_obj({k:v for k,v in receipt.items() if k!="sha256"})
            or receipt.get("contract")!=cold.CONTRACT
            or receipt.get("authority")!="PUBLIC_SOURCE_SHADOW_ONLY"
            or receipt.get("source")!="PUBLIC_ROBINHOOD_RPC"
            or receipt.get("rpc_url") not in cold.RPCS
            or receipt.get("chain_id")!=cold.CHAIN_ID
            or receipt.get("factory")!=cold.FACTORY
            or receipt.get("topic0")!=cold.TOPIC0
            or receipt.get("complete_bounded_range") is not True
            or receipt.get("bootstrap_not_full_history") is not (i==0)
            or any(receipt.get(k) is not False for k in
                ("private_source_admitted","provider_first_known_verified",
                 "github_first_publication_verified","portfolio_action","alert_authority"))
            or not isinstance(events,list) or len(events)>cold.MAX_EVENTS
            or receipt.get("event_count")!=len(events)
            or receipt.get("events_sha256")!=cold.hash_obj(events)):
            raise ValueError("HISTORICAL_RECEIPT_INVALID")
        lo,hi=receipt.get("from_block"),receipt.get("to_block")
        if (type(lo)is not int or type(hi)is not int
            or lo!=last+1 or hi<lo
            or hi-lo+1>hist.MAX_BLOCKS_PER_RUN
            or hi>=cold_state["bootstrap_from_block"]
            or receipt.get("previous_cursor")!=(None if i==0 else last)
            or receipt.get("previous_cursor_sha")!=(None if i==0 else reconstructed["sha256"])
            or type(receipt.get("rpc_log_calls"))is not int
            or not 1<=receipt["rpc_log_calls"]<=1000):
            raise ValueError("HISTORICAL_CONTIGUITY_OR_LIMIT_INVALID")
        order=(-1,-1,"")
        positions=set()
        block_hashes={}
        for event in events:
            if not isinstance(event,dict) or set(event)!=EVENT_FIELDS:
                raise ValueError("HISTORICAL_EVENT_SCHEMA_INVALID")
            n,idx,tx=event.get("block"),event.get("log_index"),event.get("tx_hash")
            if (type(n)is not int or not lo<=n<=hi or type(idx)is not int or idx<0
                or not isinstance(tx,str) or not cold.HEX32.fullmatch(tx)
                or not isinstance(event.get("block_hash"),str)
                or not cold.HEX32.fullmatch(event["block_hash"])
                or not isinstance(event.get("data"),str) or not cold.HEXDATA.fullmatch(event["data"])
                or not isinstance(event.get("topics"),list) or len(event["topics"])!=4
                or event["topics"][0]!=cold.TOPIC0
                or any(not isinstance(t,str) or not cold.HEX32.fullmatch(t) for t in event["topics"])):
                raise ValueError("HISTORICAL_CHAIN_EVENT_INVALID")
            pos=(n,idx,tx)
            if pos<=order or (tx,idx) in positions:
                raise ValueError("HISTORICAL_DUPLICATE_OR_ORDER_INVALID")
            positions.add((tx,idx));order=pos
            if n in block_hashes and block_hashes[n]!=event["block_hash"]:
                raise ValueError("HISTORICAL_MIXED_REORG_BLOCK")
            block_hashes[n]=event["block_hash"]
        try:
            first=datetime.fromisoformat(receipt["scan_started_at_utc"].replace("Z","+00:00"))
            finished=datetime.fromisoformat(receipt["scan_finished_at_utc"].replace("Z","+00:00"))
            if (not receipt["scan_started_at_utc"].endswith("Z")
                or not receipt["scan_finished_at_utc"].endswith("Z")
                or first.utcoffset()!=timedelta(0) or finished.utcoffset()!=timedelta(0)
                or first>finished or finished-first>timedelta(minutes=15)
                or (prev_clock is not None and finished<prev_clock)):
                raise ValueError("HISTORICAL_CLOCK_ORDER")
        except (ValueError,TypeError,AttributeError) as ex:
            raise ValueError("HISTORICAL_CLOCK_INVALID") from ex
        prev_clock=finished
        reconstructed=cold.signed({
            "contract":cold.CONTRACT,"authority":"PUBLIC_SHADOW_ONLY",
            "chain_id":cold.CHAIN_ID,"factory":cold.FACTORY,"topic0":cold.TOPIC0,
            "last_scanned_block":hi,"bootstrap_from_block":hist.START_BLOCK,
            "last_run_id":receipt["github_run_id"],"last_receipt_sha256":receipt["sha256"]})
        last=hi
        total+=len(events)
    if reconstructed!=current:
        raise ValueError("HISTORICAL_RECONSTRUCTED_STATE_MISMATCH")
    remaining=cold_state["bootstrap_from_block"]-1-last
    if remaining<0:raise ValueError("HISTORICAL_OVERLAP_COLD")
    return {
        "historical_status":"SELF_CONSISTENT_PUBLIC_SHADOW_NOT_AUTHENTICATED",
        "historical_verified_receipt_count":len(receipts),
        "historical_verified_start_block":hist.START_BLOCK,
        "historical_verified_end_block":last,
        "historical_verified_event_count":total,
        "historical_remaining_to_cold_bootstrap":remaining,
        "historical_bridge_self_consistent":remaining==0,
        "historical_state_sha256":current["sha256"],
        "historical_canonical_source_admitted":False,
        "historical_first_known_verified":False,
    }
