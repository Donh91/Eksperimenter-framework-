#!/usr/bin/env python3
"""Offline issuer-independent gate for public native-curve due observations."""
from __future__ import annotations
import json
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

try:
    from public_pons_shadow import collector as cold
    from public_pons_shadow import forward_gate, forward_outcome as fw
    from public_pons_shadow import curve_forward_v1 as curve
    from public_pons_shadow import native_curve_diagnostic_v1 as native
except ImportError:
    import collector as cold
    import forward_gate
    import forward_outcome as fw
    import curve_forward_v1 as curve
    import native_curve_diagnostic_v1 as native

STATE_KEYS={"contract","authority","run_id","candidates","last_receipt_sha256",
            "alert_authority","portfolio_action","sha256"}
CASE_KEYS={"id","token","curve","source_receipt","source_receipt_sha256",
           "original_forward_t0_utc","native_t0_utc","baseline","observations",
           "quote_asset","first_known_verified","execution_verified"}
OBS_KEYS=curve.OBS_KEYS
RECEIPT_KEYS={"contract","authority","run_id","checkout_sha","started_at_utc",
              "finished_at_utc","previous_state_sha256","event_count",
              "events_sha256","source_samples_attempted","source_block_number",
              "source_block_hash","first_known_verified","economic_edge_verified",
              "execution_verified","alert_authority","portfolio_action","sha256"}
RPATH=re.compile(r"public_pons_shadow/curve/runs/\d{4}/\d{2}/\d{2}/"
                 r"\d{8}T\d{6}Z_\d{1,20}_receipt\.json\Z")
IDS=re.compile(r"[a-f0-9]{24}\Z")
VALID={"NATIVE_CURVE_PROXY_CAPTURED","WINDOW_MISSED",
       "UNKNOWN_NATIVE_CURVE_RPC","UNKNOWN_SOURCE_IDENTITY",
       "UNKNOWN_EMPTY_CURVE_RESERVES"}


def verify_row(o,label,t0,start,end):
    if not isinstance(o,dict) or set(o)!=OBS_KEYS:
        raise ValueError("CURVE_OBSERVATION_EXTRA_FIELDS")
    if o.get("horizon")!=label or o.get("provider_timestamp_verified") is not False or o.get("execution_verified") is not False:
        raise ValueError("CURVE_FALSE_EXECUTION_OR_SOURCE_AUTHORITY")
    observed=fw.parse_utc(o["observed_at_utc"])
    target=fw.parse_utc(o["target_utc"])
    if not start<=observed<=end or target!=t0+timedelta(hours=curve.HORIZONS.get(label,0)):
        raise ValueError("CURVE_OBSERVATION_CLOCK_INVALID")
    status=o.get("status")
    if status not in VALID:
        raise ValueError("CURVE_OBSERVATION_STATUS_INVALID")
    if label=="NATIVE_BASELINE" and status!="NATIVE_CURVE_PROXY_CAPTURED":
        raise ValueError("CURVE_BASELINE_NOT_AN_ACTUAL_ONCHAIN_READ")
    if label!="NATIVE_BASELINE":
        if observed<target:
            raise ValueError("CURVE_EARLY_HORIZON")
        if observed>target+curve.MAX_LATE and status!="WINDOW_MISSED":
            raise ValueError("CURVE_TOO_LATE_FOR_CREDIT")
        if status=="WINDOW_MISSED" and observed<=target+curve.MAX_LATE:
            raise ValueError("CURVE_FALSE_WINDOW_MISS")
    if status=="NATIVE_CURVE_PROXY_CAPTURED":
        try:
            n=Decimal(o["ratio_eth_per_token"])
            q=Decimal(o["quote_reserve_wei"])
            t=Decimal(o["token_reserve_raw"])
            d=o["token_decimals"]
            if type(d)is not int or not 0<=d<=30 or q<=0 or t<=0 or not n.is_finite():
                raise ValueError("CURVE_BAD_RESERVES")
            with localcontext() as ctx:
                ctx.prec=55
                expected=(q/Decimal(10**18))/(t/Decimal(10**d))
            if n!=expected:
                raise ValueError("CURVE_RATIO_MATH_MISMATCH")
        except (TypeError,InvalidOperation,ValueError,OverflowError) as e:
            raise ValueError("CURVE_MATH_INVALID") from e
    elif any(o.get(k) is not None for k in ("ratio_eth_per_token",)):
        raise ValueError("CURVE_UNKNOWN_WITH_PRICE")
    if status=="WINDOW_MISSED":
        if any(o.get(k) is not None for k in ("block_number","block_hash",
                                              "quote_reserve_wei","token_reserve_raw",
                                              "token_decimals")):
            raise ValueError("CURVE_MISSED_WITH_FAKE_SOURCE")
    if status!="WINDOW_MISSED":
        if (type(o.get("block_number")) is not int or o["block_number"]<0
            or not native.HEX_HASH.fullmatch(str(o.get("block_hash") or ""))):
            raise ValueError("CURVE_BLOCK_EVIDENCE_MISSING")
    return True


def validate(stage,root,run_id,checkout_sha):
    if not fw.RUN_ID.fullmatch(run_id or "") or not fw.SHA.fullmatch(checkout_sha or ""):
        raise ValueError("CURVE_GATE_RUN_ID_INVALID")
    sp=stage/curve.STATE
    if not sp.exists():return []
    state=forward_gate.read(sp)
    old=forward_gate.read(root/curve.STATE) if (root/curve.STATE).exists() else None
    if (not isinstance(state,dict) or set(state)!=STATE_KEYS
        or state.get("contract")!=curve.CONTRACT
        or state.get("authority")!="PUBLIC_RESEARCH_ONLY"
        or state.get("run_id")!=run_id
        or state.get("alert_authority") is not False
        or state.get("portfolio_action") is not False
        or state.get("sha256")!=cold.hash_obj({k:v for k,v in state.items() if k!="sha256"})
        or not isinstance(state.get("candidates"),list)
        or len(state["candidates"])>curve.MAX_TRACKED):
        raise ValueError("CURVE_GATE_STATE_INVALID")
    if old and (set(old)!=STATE_KEYS or old.get("sha256")!=cold.hash_obj({k:v for k,v in old.items() if k!="sha256"})):
        raise ValueError("CURVE_GATE_PREVIOUS_STATE_INVALID")
    recs=list((stage/curve.HISTORY).glob("**/*_receipt.json"))
    if len(recs)!=1:raise ValueError("CURVE_GATE_RECEIPT_COUNT")
    rp=recs[0]
    rel=rp.relative_to(stage).as_posix()
    if not RPATH.fullmatch(rel) or not rel.endswith("_"+run_id+"_receipt.json"):
        raise ValueError("CURVE_GATE_RECEIPT_PATH")
    ep=rp.with_name(rp.name.replace("_receipt.json","_events.json"))
    receipt=forward_gate.read(rp)
    events=forward_gate.read(ep)
    if (not isinstance(receipt,dict) or set(receipt)!=RECEIPT_KEYS
        or receipt.get("sha256")!=cold.hash_obj({k:v for k,v in receipt.items() if k!="sha256"})
        or receipt.get("contract")!=curve.CONTRACT
        or receipt.get("authority")!="PUBLIC_RESEARCH_ONLY"
        or receipt.get("run_id")!=run_id
        or receipt.get("checkout_sha")!=checkout_sha
        or receipt.get("previous_state_sha256")!=(old["sha256"] if old else None)
        or receipt.get("event_count")!=len(events)
        or not isinstance(events,list) or not 1<=len(events)<=curve.MAX_SAMPLES_PER_TICK
        or receipt.get("events_sha256")!=cold.hash_obj(events)
        or state.get("last_receipt_sha256")!=receipt.get("sha256")
        or type(receipt.get("source_samples_attempted")) is not int
        or not 0<=receipt["source_samples_attempted"]<=curve.MAX_SAMPLES_PER_TICK
        or any(receipt.get(k) is not False for k in (
            "first_known_verified","economic_edge_verified","execution_verified",
            "alert_authority","portfolio_action"))):
        raise ValueError("CURVE_GATE_RECEIPT_INVALID")
    start=fw.parse_utc(receipt["started_at_utc"])
    end=fw.parse_utc(receipt["finished_at_utc"])
    if not timedelta(0)<=end-start<=timedelta(minutes=5):
        raise ValueError("CURVE_GATE_RUN_CLOCK")
    prev_cases=(old or {}).get("candidates",[])
    if len(state["candidates"])<len(prev_cases):
        raise ValueError("CURVE_GATE_HISTORY_TRUNCATED")
    expected=[]
    seen=set()
    new_daily={}
    source=curve.published_forward(root)
    public={c["id"]:c for c in source["candidates"]} if source else {}
    for i,c in enumerate(state["candidates"]):
        if not isinstance(c,dict) or set(c)!=CASE_KEYS or not IDS.fullmatch(str(c.get("id") or "")):
            raise ValueError("CURVE_GATE_CASE_FIELDS")
        if (c["id"] in seen or c.get("first_known_verified") is not False
            or c.get("execution_verified") is not False
            or c.get("quote_asset")!="NATIVE_ETH"
            or not fw.TOKEN.fullmatch(str(c.get("token") or ""))
            or not fw.TOKEN.fullmatch(str(c.get("curve") or ""))):
            raise ValueError("CURVE_GATE_CASE_AUTHORITY")
        seen.add(c["id"])
        t0=fw.parse_utc(c["native_t0_utc"])
        obs=c.get("observations")
        if not isinstance(obs,dict) or set(obs)!={"24H","72H"}:
            raise ValueError("CURVE_GATE_OUTCOME_STRUCTURE")
        if i>=len(prev_cases):
            orig=public.get(c["id"])
            if orig is None:raise ValueError("CURVE_GATE_MISSING_ORIGINAL_PUBLIC_CASE")
            token,pool,pair=native.source_case(root,orig)
            if (token!=c["token"] or pool!=c["curve"] or pair!=native.ZERO
                or orig["t0_utc"]!=c["original_forward_t0_utc"]
                or orig["source_receipt"]!=c["source_receipt"]
                or orig["source_receipt_sha256"]!=c["source_receipt_sha256"]):
                raise ValueError("CURVE_GATE_ORIGINAL_SOURCE_MISMATCH")
            if not start<=t0<=end:
                raise ValueError("CURVE_GATE_NATIVE_T0_BACKDATED")
            new_daily[c["native_t0_utc"][:10]]=new_daily.get(c["native_t0_utc"][:10],0)+1
            if new_daily[c["native_t0_utc"][:10]]>curve.MAX_NEW_DAILY:
                raise ValueError("CURVE_GATE_NEW_DAILY_CAP")
            verify_row(c["baseline"],"NATIVE_BASELINE",t0,start,end)
            expected.append({"id":c["id"],**c["baseline"]})
            oldobservations={"24H":[],"72H":[]}
        else:
            before=prev_cases[i]
            if {k:v for k,v in before.items() if k!="observations"} != {k:v for k,v in c.items() if k!="observations"}:
                raise ValueError("CURVE_GATE_FROZEN_ENTRY_REWRITTEN")
            oldobservations=before["observations"]
        for label in ("24H","72H"):
            values=obs[label]
            older=oldobservations[label]
            if (not isinstance(values,list) or len(values)>curve.MAX_ATTEMPTS_PER_HORIZON+1
                or values[:len(older)]!=older or len(values)-len(older)>1):
                raise ValueError("CURVE_GATE_HISTORY_REWRITTEN")
            if values and values[-1]["status"]=="NATIVE_CURVE_PROXY_CAPTURED" and len(values)>1:
                if any(v["status"]=="NATIVE_CURVE_PROXY_CAPTURED" for v in values[:-1]):
                    raise ValueError("CURVE_GATE_DUPLICATED_SUCCESS")
            for o in values[len(older):]:
                verify_row(o,label,t0,start,end)
                if older and older[-1]["status"] in ("NATIVE_CURVE_PROXY_CAPTURED","WINDOW_MISSED"):
                    raise ValueError("CURVE_GATE_COMPLETED_OUTCOME_OVERWRITTEN")
                expected.append({"id":c["id"],**o})
    if sorted(json.dumps(x,sort_keys=True) for x in events)!=sorted(json.dumps(x,sort_keys=True) for x in expected):
        raise ValueError("CURVE_GATE_EVENT_DELTA_MISMATCH")
    return [curve.STATE,rp.relative_to(stage),ep.relative_to(stage)]
