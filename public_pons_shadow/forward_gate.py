#!/usr/bin/env python3
"""Strict offline stage admission for public Pons forward spot research only."""
from __future__ import annotations
import json
import re
from datetime import timedelta
from pathlib import Path

try:
    from public_pons_shadow import collector as cold
    from public_pons_shadow import forward_outcome as fw
except ImportError:
    import collector as cold
    import forward_outcome as fw

HEX = re.compile(r"[a-f0-9]{24}\Z")
RUNPATH = re.compile(r"public_pons_shadow/forward/runs/\d{4}/\d{2}/\d{2}/"
                     r"\d{8}T\d{6}Z_\d{1,20}_receipt\.json\Z")
SOURCEPATH = re.compile(r"public_pons_shadow/hot/runs/\d{4}/\d{2}/\d{2}/"
                        r"\d{8}T\d{6}Z_\d{1,20}_receipt\.json\Z")
STATUS = {"PUBLIC_SPOT_OBSERVED_NON_EXECUTABLE","UNKNOWN_PROVIDER_ERROR",
          "UNKNOWN_EXACT_PUBLIC_PAIR","UNKNOWN_NO_BASELINE_PAIR","WINDOW_MISSED"}


def read(path):
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 2_000_000:
        raise ValueError("FORWARD_STAGE_FILE_INVALID")
    def pairs(items):
        d = {}
        for k,v in items:
            if k in d:
                raise ValueError("FORWARD_DUPLICATE_JSON_KEY")
            d[k] = v
        return d
    return json.loads(path.read_text(), object_pairs_hook=pairs,
                      parse_constant=lambda v: (_ for _ in ()).throw(ValueError("FORWARD_NONFINITE")))


def verify_source(root,c):
    rel = c.get("source_receipt")
    if not isinstance(rel,str) or not SOURCEPATH.fullmatch(rel):
        raise ValueError("FORWARD_SOURCE_PATH_INVALID")
    receipt = read(root/rel)
    if (receipt.get("sha256")!=c.get("source_receipt_sha256")
        or receipt.get("sha256")!=cold.hash_obj({k:v for k,v in receipt.items() if k!="sha256"})
        or receipt.get("contract")!="PUBLIC_PONS_V2_HOT_SHADOW_V1"
        or receipt.get("authority")!="PUBLIC_HOT_SHADOW_ONLY"
        or receipt.get("chain_id")!=4663
        or receipt.get("factory")!=cold.FACTORY
        or receipt.get("topic0")!=cold.TOPIC0
        or receipt.get("real_time_window_candidate") is not True
        or any(receipt.get(k) is not False for k in ("private_source_admitted",
             "canonical_census_complete","alert_authority","portfolio_action"))):
        raise ValueError("FORWARD_SOURCE_RECEIPT_INVALID")
    t0=fw.parse_utc(c["t0_utc"])
    source_seen=fw.parse_utc(receipt["scan_finished_at_utc"])
    if source_seen != fw.parse_utc(c["source_machine_observed_utc"]) or not timedelta(0)<=t0-source_seen<=fw.MAX_SOURCE_AGE:
        raise ValueError("FORWARD_SOURCE_CLOCK_UNBOUND")
    events=read((root/rel).with_name(Path(rel).name.replace("_receipt.json","_events.json")))
    if not isinstance(events,list) or receipt.get("events_sha256")!=cold.hash_obj(events):
        raise ValueError("FORWARD_SOURCE_EVENTS_UNBOUND")
    for e in events:
        if not isinstance(e,dict):continue
        topics=e.get("topics")
        if not isinstance(topics,list) or len(topics)!=4 or topics[0]!=cold.TOPIC0:continue
        token="0x"+str(topics[1])[-40:]
        pool="0x"+str(topics[2])[-40:]
        if not fw.TOKEN.fullmatch(token) or not fw.TOKEN.fullmatch(pool):
            continue
        identity=cold.hash_obj({"chain_id":4663,"token":token,
                                "source_tx":e.get("tx_hash"),"block":e.get("block"),
                                "log_index":e.get("log_index")})[:24]
        if identity==c["id"] and token==c["token"] and pool==c["source_pool"]:
            return
    raise ValueError("FORWARD_TOKEN_NOT_IN_ORIGINAL_PUBLIC_SOURCE")


def validate(stage,root,run_id,checkout_sha):
    if not fw.RUN_ID.fullmatch(run_id or "") or not fw.SHA.fullmatch(checkout_sha or ""):
        raise ValueError("FORWARD_RUN_OR_SHA_INVALID")
    state_rel=fw.STATE
    staged=stage/state_rel
    if not staged.exists():
        return []
    state=read(staged)
    prior=read(root/state_rel) if (root/state_rel).exists() else None
    if (state.get("contract")!=fw.CONTRACT
        or state.get("authority")!="PUBLIC_RESEARCH_ONLY"
        or state.get("run_id")!=run_id
        or state.get("alert_authority") is not False
        or state.get("portfolio_action") is not False
        or state.get("sha256")!=cold.hash_obj({k:v for k,v in state.items() if k!="sha256"})
        or not isinstance(state.get("candidates"),list)
        or len(state["candidates"])>fw.MAX_TRACKED):
        raise ValueError("FORWARD_STAGE_STATE_UNTRUSTED")
    if prior and (prior.get("sha256")!=cold.hash_obj({k:v for k,v in prior.items() if k!="sha256"})
        or prior.get("contract")!=fw.CONTRACT):
        raise ValueError("FORWARD_PREVIOUS_STATE_UNTRUSTED")
    receipts=list((stage/fw.HISTORY).glob("**/*_receipt.json"))
    if len(receipts)!=1:
        raise ValueError("FORWARD_STAGE_RECEIPT_MISSING")
    rp=receipts[0]
    rel=rp.relative_to(stage).as_posix()
    if not RUNPATH.fullmatch(rel) or not rel.endswith("_"+run_id+"_receipt.json"):
        raise ValueError("FORWARD_RECEIPT_PATH_MISMATCH")
    ep=rp.with_name(rp.name.replace("_receipt.json","_events.json"))
    receipt=read(rp)
    events=read(ep)
    if (not isinstance(events,list) or not events
        or receipt.get("sha256")!=cold.hash_obj({k:v for k,v in receipt.items() if k!="sha256"})
        or receipt.get("contract")!=fw.CONTRACT
        or receipt.get("authority")!="PUBLIC_RESEARCH_ONLY"
        or receipt.get("run_id")!=run_id
        or receipt.get("checkout_sha")!=checkout_sha
        or receipt.get("previous_state_sha256")!=(prior.get("sha256") if prior else None)
        or receipt.get("event_count")!=len(events)
        or receipt.get("events_sha256")!=cold.hash_obj(events)
        or receipt.get("source_calls",5)>fw.MAX_CALLS_PER_RUN
        or state.get("last_receipt_sha256")!=receipt.get("sha256")
        or any(receipt.get(k) is not False for k in ("canonical_source_admitted",
                "historical_replay","first_known_verified","portfolio_action",
                "paper_execution","alert_authority"))):
        raise ValueError("FORWARD_STAGE_RECEIPT_UNTRUSTED")
    start=fw.parse_utc(receipt["started_at_utc"])
    end=fw.parse_utc(receipt["finished_at_utc"])
    if not timedelta(0)<=end-start<=timedelta(minutes=5):
        raise ValueError("FORWARD_RUN_CLOCK_INVALID")
    if len(state["candidates"]) < len(prior["candidates"] if prior else []):
        raise ValueError("FORWARD_CANDIDATE_HISTORY_TRUNCATED")
    originals=(prior or {}).get("candidates",[])
    expected=[]
    ids=set()
    tokens=set()
    today_new={}
    for i,c in enumerate(state["candidates"]):
        if not isinstance(c,dict) or not HEX.fullmatch(str(c.get("id") or "")):
            raise ValueError("FORWARD_CANDIDATE_ID_INVALID")
        if (c["id"] in ids or c.get("token") in tokens
            or c.get("chain_id")!=4663
            or not fw.TOKEN.fullmatch(str(c.get("token") or ""))
            or not fw.TOKEN.fullmatch(str(c.get("source_pool") or ""))
            or c.get("source_coverage")!="PARTIAL_PUBLIC_HOT"
            or c.get("first_known_verified") is not False
            or c.get("paper_entry_authority") is not False
            or c.get("selection")!="FIRST_VALID_PUBLIC_HOT_LOGS_MAX2_DAILY"):
            raise ValueError("FORWARD_CANDIDATE_AUTHORITY_OR_DUPLICATE")
        ids.add(c["id"]);tokens.add(c["token"])
        t0=fw.parse_utc(c["t0_utc"])
        if i >= len(originals):
            verify_source(root,c)
            if not start <= t0 <= end:
                raise ValueError("FORWARD_NEW_FREEZE_CLOCK_INVALID")
            day=c["t0_utc"][:10]
            today_new[day]=today_new.get(day,0)+1
            if today_new[day]>fw.MAX_NEW_DAILY:
                raise ValueError("FORWARD_DAILY_CAP_BREACHED")
            expected.append({"id":c["id"],"horizon":"FREEZE","captured_at_utc":c["t0_utc"],
                             "state":"SHADOW_CANDIDATE_ONLY",
                             "source_receipt_sha256":c["source_receipt_sha256"]})
            oldobs={}
        else:
            original=originals[i]
            before={k:v for k,v in original.items() if k!="observations"}
            after={k:v for k,v in c.items() if k!="observations"}
            if before!=after:
                raise ValueError("FORWARD_FREEZE_REWRITTEN")
            oldobs=original.get("observations",{})
        observations=c.get("observations")
        if not isinstance(observations,dict) or not set(observations)<= {"BASELINE","24H","72H"}:
            raise ValueError("FORWARD_OBSERVATIONS_INVALID")
        if any(observations.get(k)!=v for k,v in oldobs.items()):
            raise ValueError("FORWARD_OUTCOME_REWRITTEN")
        for label in ("BASELINE","24H","72H"):
            if label not in observations or label in oldobs:
                continue
            o=observations[label]
            hrs={"BASELINE":0,"24H":24,"72H":72}[label]
            due=t0+timedelta(hours=hrs)
            seen=fw.parse_utc(o.get("captured_at_utc"))
            if (o.get("horizon")!=label or o.get("target_utc")!=fw.stamp(due)
                or not due<=seen<=end
                or o.get("state") not in STATUS
                or o.get("provider_timestamp_verified") is not False
                or o.get("execution_verified") is not False
                or o.get("realizable_net_return") is not None):
                raise ValueError("FORWARD_OUTCOME_AUTHORITY_OR_CLOCK_INVALID")
            px=o.get("price_usd");pair=o.get("pair")
            if o["state"]=="PUBLIC_SPOT_OBSERVED_NON_EXECUTABLE":
                if not isinstance(px,str) or not fw.TOKEN.fullmatch(str(pair or "")):
                    raise ValueError("FORWARD_SPOT_PRICE_OR_PAIR_INVALID")
                try:
                    n=float(px)
                except ValueError:
                    raise ValueError("FORWARD_SPOT_PRICE_INVALID") from None
                if not 0<n<1e15:
                    raise ValueError("FORWARD_SPOT_PRICE_OUT_OF_BOUNDS")
                if label!="BASELINE" and pair!=observations["BASELINE"].get("pair"):
                    raise ValueError("FORWARD_PAIR_CHANGED_AFTER_FREEZE")
            elif px is not None or pair is not None:
                raise ValueError("FORWARD_UNKNOWN_WITH_FAKE_PRICE")
            expected.append({"id":c["id"], **o})
    if sorted(json.dumps(x,sort_keys=True) for x in expected)!=sorted(json.dumps(x,sort_keys=True) for x in events):
        raise ValueError("FORWARD_STAGE_EVENT_LEDGER_MISMATCH")
    return [state_rel,rp.relative_to(stage),ep.relative_to(stage)]
