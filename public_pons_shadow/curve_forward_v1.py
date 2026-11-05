#!/usr/bin/env python3
"""Due-only native Pons V2 reserve-ratio shadow, never executable alpha.

Frozen native baseline Tnative0 is its actual first onchain read completion,
not the original public launch or DEX T0. All outcomes are public-source
proxies, including after phase transitions; NEVER real fills or USD profits.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path

try:
    from public_pons_shadow import collector as cold, forward_outcome as fw
    from public_pons_shadow import native_curve_diagnostic_v1 as native
except ImportError:
    import collector as cold
    import forward_outcome as fw
    import native_curve_diagnostic_v1 as native

CONTRACT="PUBLIC_PONS_NATIVE_DUE_SHADOW_V1"
STATE=Path("public_pons_shadow/curve_state.json")
HISTORY=Path("public_pons_shadow/curve/runs")
MAX_TRACKED=240
MAX_NEW_DAILY=2
MAX_SAMPLES_PER_TICK=4
MAX_ATTEMPTS_PER_HORIZON=3
MAX_LATE=timedelta(hours=4)
RETRY_AFTER=timedelta(minutes=15)
HORIZONS={"24H":24,"72H":72}
OBS_KEYS={"horizon","target_utc","observed_at_utc","status","block_number",
          "block_hash","quote_reserve_wei","token_reserve_raw","token_decimals",
          "ratio_eth_per_token","provider_timestamp_verified","execution_verified"}


def published_forward(root):
    """Only committed public forward candidates, not newly staged same-run data."""
    file=root/fw.STATE
    if (root/".git").exists():
        try:
            raw=subprocess.check_output(["git","show","HEAD:"+fw.STATE.as_posix()],
                                        cwd=root,stderr=subprocess.DEVNULL,timeout=4)
        except (subprocess.CalledProcessError,subprocess.TimeoutExpired,OSError):
            return None
        if len(raw)>2_000_000:
            raise ValueError("CURVE_FORWARD_SOURCE_OVERSIZED")
        obj=json.loads(raw)
    elif file.is_file():
        obj=json.loads(file.read_text())
    else:
        return None
    if (not isinstance(obj,dict) or obj.get("contract")!=fw.CONTRACT
        or obj.get("authority")!="PUBLIC_RESEARCH_ONLY"
        or obj.get("sha256")!=cold.hash_obj({k:v for k,v in obj.items() if k!="sha256"})
        or not isinstance(obj.get("candidates"),list)
        or len(obj["candidates"])>fw.MAX_TRACKED):
        raise ValueError("CURVE_FORWARD_SOURCE_UNTRUSTED")
    return obj


def reserve_observation(client,token,curve,block,block_hash,target,clock,horizon):
    """Return one non-executable ratio at a single already pinned block."""
    status="UNKNOWN_NATIVE_CURVE_RPC"
    qr=tr=dec=ratio=None
    try:
        raw=client.call("eth_call",[{"to":curve,"data":native.RESERVES_SELECTOR},hex(block)])
        q,t=native.decode_reserves(raw)
        dr=client.call("eth_call",[{"to":token,"data":native.DECIMALS_SELECTOR},hex(block)])
        if not isinstance(dr,str) or not native.HEX_DECIMALS.fullmatch(dr):
            raise ValueError("CURVE_DECIMALS_MALFORMED")
        decimals=int(dr,16)
        if not 0<=decimals<=30:
            raise ValueError("CURVE_DECIMALS_INVALID")
        qr,tr,dec=str(q),str(t),decimals
        if q>0 and t>0:
            with localcontext() as ctx:
                ctx.prec=55
                ratio=str((Decimal(q)/Decimal(10**18))/(Decimal(t)/Decimal(10**decimals)))
            status="NATIVE_CURVE_PROXY_CAPTURED"
        else:
            status="UNKNOWN_EMPTY_CURVE_RESERVES"
    except (OSError,ValueError,RuntimeError,TypeError,KeyError):
        status="UNKNOWN_NATIVE_CURVE_RPC"
    observed=clock()
    if horizon!="NATIVE_BASELINE" and observed > target + MAX_LATE:
        return missed_snapshot(horizon,target,observed)
    if not isinstance(observed,datetime) or observed.tzinfo is None:
        raise ValueError("CURVE_CLOCK_NOT_UTC")
    return {
        "horizon":horizon,"target_utc":cold.iso(target),
        "observed_at_utc":cold.iso(observed),"status":status,
        "block_number":block,"block_hash":block_hash,
        "quote_reserve_wei":qr,"token_reserve_raw":tr,"token_decimals":dec,
        "ratio_eth_per_token":ratio,"provider_timestamp_verified":False,
        "execution_verified":False,
    }


def missed_snapshot(horizon,target,now):
    return {
        "horizon":horizon,"target_utc":cold.iso(target),
        "observed_at_utc":cold.iso(now),"status":"WINDOW_MISSED",
        "block_number":None,"block_hash":None,
        "quote_reserve_wei":None,"token_reserve_raw":None,"token_decimals":None,
        "ratio_eth_per_token":None,"provider_timestamp_verified":False,
        "execution_verified":False,
    }


def capture(root,run_id,checkout_sha,client=None,clock=None):
    if not fw.RUN_ID.fullmatch(run_id or "") or not fw.SHA.fullmatch(checkout_sha or ""):
        raise ValueError("CURVE_RUN_ID_INVALID")
    client=client or cold.Client()
    clock=clock or cold.now
    start=clock()
    if not isinstance(start,datetime) or start.tzinfo is None or start.utcoffset()!=timedelta(0):
        raise ValueError("CURVE_CAPTURE_CLOCK_NOT_UTC")
    existing=STATE
    path=root/existing
    prev=json.loads(path.read_text()) if path.is_file() else None
    if prev and (not isinstance(prev,dict) or prev.get("contract")!=CONTRACT
        or prev.get("authority")!="PUBLIC_RESEARCH_ONLY"
        or not isinstance(prev.get("candidates"),list)
        or len(prev["candidates"])>MAX_TRACKED
        or prev.get("sha256")!=cold.hash_obj({k:v for k,v in prev.items() if k!="sha256"})):
        raise ValueError("CURVE_PREVIOUS_STATE_INVALID")
    candidates=json.loads(json.dumps(prev["candidates"])) if prev else []
    source=published_forward(root)
    current={c["id"]:c for c in source["candidates"]} if source else {}
    events=[]
    plans=[]
    today=cold.iso(start)[:10]
    new_today=sum(c["native_t0_utc"][:10]==today for c in candidates)
    # Matured fixed-horizon observations get priority over adding new cases.
    for c in candidates:
        for label,hours in HORIZONS.items():
            attempts=c["observations"][label]
            if attempts and attempts[-1]["status"] in ("NATIVE_CURVE_PROXY_CAPTURED","WINDOW_MISSED"):
                continue
            target=fw.parse_utc(c["native_t0_utc"])+timedelta(hours=hours)
            if start<target:continue
            if start>target+MAX_LATE:
                plans.append(("MISSED",c,label,target))
            elif len(attempts)>=MAX_ATTEMPTS_PER_HORIZON:
                continue  # Try cap exhausted; preserve UNKNOWN until window expires.
            elif not attempts or start-fw.parse_utc(attempts[-1]["observed_at_utc"])>=RETRY_AFTER:
                plans.append(("DUE",c,label,target))
    if source and new_today<MAX_NEW_DAILY and len(candidates)<MAX_TRACKED:
        known={c["id"] for c in candidates}
        for public in source["candidates"]:
            if public["id"] in known:continue
            if len(plans)>=MAX_SAMPLES_PER_TICK:break
            plans.append(("BASELINE",public,"NATIVE_BASELINE",start))
            if len([p for p in plans if p[0]=="BASELINE"])>=MAX_NEW_DAILY-new_today:
                break
    if not plans:
        return {"status":"NO_NATIVE_OBSERVATIONS_DUE","source_calls":0}
    if len(plans)>MAX_SAMPLES_PER_TICK:
        plans=plans[:MAX_SAMPLES_PER_TICK]
    requires_rpc=any(p[0]!="MISSED" for p in plans)
    block=block_hash=None
    if requires_rpc:
        client.connect()
        block=cold.as_int(client.call("eth_blockNumber",[]))-2
        if block<0:raise ValueError("CURVE_SAFE_BLOCK_INVALID")
        hdr=client.call("eth_getBlockByNumber",[hex(block),False])
        if (not isinstance(hdr,dict) or cold.as_int(hdr.get("number"))!=block
            or not native.HEX_HASH.fullmatch(str(hdr.get("hash") or ""))):
            raise ValueError("CURVE_BLOCK_HEADER_INVALID")
        block_hash=hdr["hash"].lower()
    sampled=0
    for kind,c,label,target in plans:
        if kind=="MISSED":
            obs=missed_snapshot(label,target,clock())
        else:
            try:
                # Do not trust a ticker join or token address from an external provider.
                pub=current.get(c["id"])
                if pub is None:
                    raise ValueError("CURVE_PUBLIC_FORWARD_CANDIDATE_MISSING")
                token,curve,pair=native.source_case(root,pub)
                if token!=c["token"] or curve!=c.get("curve",c.get("source_pool")):
                    raise ValueError("CURVE_IDENTITY_DRIFT")
                if pair!=native.ZERO:
                    raise ValueError("CURVE_NON_NATIVE_QUOTE_UNSUPPORTED")
                obs=reserve_observation(client,token,curve,block,block_hash,target,clock,label)
                sampled+=1
            except (ValueError,KeyError,TypeError,OSError):
                # Unverified identity never becomes a positive observation.
                obs={
                    "horizon":label,"target_utc":cold.iso(target),
                    "observed_at_utc":cold.iso(clock()),"status":"UNKNOWN_SOURCE_IDENTITY",
                    "block_number":block,"block_hash":block_hash,
                    "quote_reserve_wei":None,"token_reserve_raw":None,
                    "token_decimals":None,"ratio_eth_per_token":None,
                    "provider_timestamp_verified":False,"execution_verified":False,
                }
        if kind=="BASELINE":
            # A baseline starts only when a valid onchain ratio is observed.
            if obs["status"]!="NATIVE_CURVE_PROXY_CAPTURED":
                continue
            c2={"id":c["id"],"token":c["token"],"curve":c["source_pool"],
                "source_receipt":c["source_receipt"],
                "source_receipt_sha256":c["source_receipt_sha256"],
                "original_forward_t0_utc":c["t0_utc"],
                "native_t0_utc":obs["observed_at_utc"],
                "baseline":{**obs,"target_utc":obs["observed_at_utc"]},
                "observations":{"24H":[],"72H":[]},
                "quote_asset":"NATIVE_ETH","first_known_verified":False,
                "execution_verified":False}
            candidates.append(c2)
            events.append({"id":c["id"],**c2["baseline"]})
            new_today+=1
        else:
            c["observations"][label].append(obs)
            events.append({"id":c["id"],**obs})
    if requires_rpc:
        last=client.call("eth_getBlockByNumber",[hex(block),False])
        if (not isinstance(last,dict) or cold.as_int(last.get("number"))!=block
            or str(last.get("hash","")).lower()!=block_hash):
            raise ValueError("CURVE_BLOCK_REORG_NO_ADMISSION")
    if not events:
        return {"status":"NO_VALID_NATIVE_SOURCE_OR_RPC","source_calls":sampled}
    end=clock()
    if end<start or end-start>timedelta(minutes=5):
        raise ValueError("CURVE_CAPTURE_TIME_INVALID")
    receipt=cold.signed({
        "contract":CONTRACT,"authority":"PUBLIC_RESEARCH_ONLY",
        "run_id":run_id,"checkout_sha":checkout_sha,
        "started_at_utc":cold.iso(start),"finished_at_utc":cold.iso(end),
        "previous_state_sha256":prev["sha256"] if prev else None,
        "event_count":len(events),"events_sha256":cold.hash_obj(events),
        "source_samples_attempted":sampled,"source_block_number":block,
        "source_block_hash":block_hash,"first_known_verified":False,
        "economic_edge_verified":False,"execution_verified":False,
        "alert_authority":False,"portfolio_action":False})
    state=cold.signed({"contract":CONTRACT,"authority":"PUBLIC_RESEARCH_ONLY",
                       "run_id":run_id,"candidates":candidates,
                       "last_receipt_sha256":receipt["sha256"],
                       "alert_authority":False,"portfolio_action":False})
    stem=start.strftime("%Y%m%dT%H%M%SZ")+"_"+run_id
    folder=root/HISTORY/start.strftime("%Y/%m/%d")
    rp=folder/(stem+"_receipt.json")
    ep=folder/(stem+"_events.json")
    if rp.exists() or ep.exists():
        raise ValueError("CURVE_IMMUTABLE_RUN_COLLISION")
    folder.mkdir(parents=True,exist_ok=True)
    rp.write_bytes(cold.encode(receipt))
    ep.write_bytes(cold.encode(events))
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(cold.encode(state))
    return {"status":"PUBLIC_NATIVE_CURVE_CAPTURED_UNPUBLISHED",
            "receipt":rp.relative_to(root).as_posix(),
            "source_calls":sampled,"event_count":len(events)}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--repo-root",type=Path,default=Path("."))
    ap.add_argument("--validate-only",action="store_true")
    args=ap.parse_args()
    if args.validate_only:
        assert MAX_NEW_DAILY==2 and MAX_SAMPLES_PER_TICK==4
        print("PUBLIC_NATIVE_DUE_RESEARCH_ONLY_POLICY_PASS")
    else:
        print(json.dumps(capture(args.repo_root.resolve(),os.getenv("GITHUB_RUN_ID",""),
                                 os.getenv("GITHUB_CHECKOUT_SHA","")),sort_keys=True))


if __name__=="__main__":
    main()
