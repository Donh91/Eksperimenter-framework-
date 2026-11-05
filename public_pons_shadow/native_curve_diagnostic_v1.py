#!/usr/bin/env python3
"""Read-only, public-only native Pons V2 reserve proxy diagnostic, never a fill.

Uses the immutable public HOT source bound by the EXISTING forward research gate.
This *later* chain observation has its own capture clock and cannot patch T0 or
claim an executable price. No wallet, private Alpha source or trading authority.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path

try:
    from public_pons_shadow import collector as cold
    from public_pons_shadow import forward_gate, forward_outcome
except ImportError:
    import collector as cold
    import forward_gate
    import forward_outcome

CONTRACT = "PUBLIC_PONS_NATIVE_CURVE_PROXY_DIAGNOSTIC_V1"
RESERVES_SELECTOR = "0x0902f1ac"  # Pons IPonsCurve.getReserves()
DECIMALS_SELECTOR = "0x313ce567"  # ERC20.decimals()
ZERO = "0x" + "0"*40
MAX_CASES = 2
HEX_RESERVES = re.compile(r"0x[0-9a-fA-F]{128}\Z")
HEX_HASH = re.compile(r"0x[0-9a-fA-F]{64}\Z")
HEX_DECIMALS = re.compile(r"0x[0-9a-fA-F]{1,64}\Z")


def source_case(root, candidate):
    """Only take the original source token/curve/pair, never an arbitrary ticker."""
    forward_gate.verify_source(root, candidate)
    rel = candidate["source_receipt"]
    rp = root / rel
    ep = rp.with_name(rp.name.replace("_receipt.json", "_events.json"))
    rows = forward_gate.read(ep)
    for event in rows:
        if not isinstance(event,dict):
            continue
        topics = event.get("topics")
        if not isinstance(topics,list) or len(topics)!=4 or topics[0]!=cold.TOPIC0:
            continue
        token = "0x"+topics[1][-40:]
        curve = "0x"+topics[2][-40:]
        identity = cold.hash_obj({
            "chain_id":cold.CHAIN_ID, "token":token,
            "source_tx":event.get("tx_hash"), "block":event.get("block"),
            "log_index":event.get("log_index")})[:24]
        if identity!=candidate["id"] or token!=candidate["token"] or curve!=candidate["source_pool"]:
            continue
        data=event.get("data")
        if not isinstance(data,str) or len(data)<66 or not re.fullmatch(r"0x[0-9a-fA-F]+",data):
            raise ValueError("PONS_NATIVE_PAIR_WORD_MISSING")
        # Solidity's first static ABI word carries TokenLaunched.pairToken.
        pair="0x"+data[26:66].lower()
        if not forward_outcome.TOKEN.fullmatch(pair):
            raise ValueError("PONS_NATIVE_PAIR_WORD_INVALID")
        return token,curve,pair
    raise ValueError("PONS_NATIVE_SOURCE_IDENTITY_NOT_FOUND")


def decode_reserves(raw):
    if not isinstance(raw,str) or not HEX_RESERVES.fullmatch(raw):
        raise ValueError("PONS_NATIVE_RESERVES_MALFORMED")
    return int(raw[2:66],16),int(raw[66:130],16)


def read_proxies(root, client, *, clock=lambda:datetime.now(timezone.utc)):
    now=clock()
    result={"contract":CONTRACT,"authority":"PUBLIC_RESEARCH_ONLY",
            "source":"PUBLIC_PONS_HOT_ALREADY_PUBLISHED",
            "sample_read_started_utc":cold.iso(now),"portfolio_action":False,
            "alert_authority":False,"execution_verified":False,
            "provider_first_known_verified":False,"retrospective_t0_credit":False,
            "token_cohort":"EXISTING_FORWARD_FROZEN_MAX2",
            "candidate_count":0,"results":[]}
    state_file=root/forward_outcome.STATE
    # Never use a newly staged forward freeze that has not been committed to
    # the public repository. Production jobs have an actual .git checkout.
    if (root/".git").exists():
        try:
            raw=subprocess.check_output(
                ["git","show","HEAD:"+forward_outcome.STATE.as_posix()],
                cwd=root,stderr=subprocess.DEVNULL,timeout=4)
        except (subprocess.CalledProcessError,subprocess.TimeoutExpired,OSError):
            result["status"]="NO_PRIOR_PUBLISHED_FORWARD_FREEZE"
            return cold.signed(result)
        if len(raw)>2_000_000:
            raise ValueError("PONS_NATIVE_FORWARD_STATE_OVERSIZED")
        state=json.loads(raw)
    else:
        if not state_file.exists():
            result["status"]="NO_PUBLIC_FORWARD_FREEZE"
            return cold.signed(result)
        state=forward_gate.read(state_file)
    if (state.get("contract")!=forward_outcome.CONTRACT
        or state.get("authority")!="PUBLIC_RESEARCH_ONLY"
        or state.get("sha256")!=cold.hash_obj({k:v for k,v in state.items() if k!="sha256"})
        or not isinstance(state.get("candidates"),list)
        or len(state["candidates"])>forward_outcome.MAX_TRACKED):
        raise ValueError("PONS_NATIVE_FORWARD_STATE_UNTRUSTED")
    candidates=state["candidates"][:MAX_CASES]
    if not candidates:
        result["status"]="NO_FROZEN_CANDIDATES"
        return cold.signed(result)
    result["candidate_count"]=len(candidates)
    client.connect()  # enforces Robinhood chain 4663 from public RPC source
    safe=cold.as_int(client.call("eth_blockNumber",[]))-2
    if safe<0:
        raise ValueError("PONS_NATIVE_SAFE_HEAD_INVALID")
    header=client.call("eth_getBlockByNumber",[hex(safe),False])
    if (not isinstance(header,dict) or cold.as_int(header.get("number"))!=safe
        or not HEX_HASH.fullmatch(str(header.get("hash") or ""))):
        raise ValueError("PONS_NATIVE_BLOCK_HEADER_INVALID")
    block_hash=header["hash"].lower()
    result.update({"read_block_number":safe,"read_block_hash":block_hash})
    for c in candidates:
        event={"id":c["id"],"token":c["token"],"curve":c["source_pool"],
               "source_receipt_sha256":c["source_receipt_sha256"],
               "original_t0_utc":c["t0_utc"],
               "sample_read_at_utc":cold.iso(clock()),
               "status":"UNKNOWN","price_proxy_eth_per_token":None,
               "valuation_type":"VIRTUAL_RESERVE_RATIO_PROXY_NOT_EXECUTABLE",
               "economic_edge_verified":False,"independent_trade_quote":False}
        try:
            token,curve,pair=source_case(root,c)
            event["quote_asset_address"]=pair
            if pair!=ZERO:
                event["status"]="UNSUPPORTED_NON_NATIVE_QUOTE"
            else:
                raw=client.call("eth_call",[{"to":curve,"data":RESERVES_SELECTOR},hex(safe)])
                qr,tr=decode_reserves(raw)
                dr=client.call("eth_call",[{"to":token,"data":DECIMALS_SELECTOR},hex(safe)])
                if not isinstance(dr,str) or not HEX_DECIMALS.fullmatch(dr):
                    raise ValueError("PONS_NATIVE_DECIMALS_INVALID")
                dec=int(dr,16)
                if not 0<=dec<=30:
                    raise ValueError("PONS_NATIVE_DECIMALS_OUT_OF_BOUNDS")
                event.update({"quote_reserve_raw":str(qr),"token_reserve_raw":str(tr),"token_decimals":dec})
                if qr==0 or tr==0:
                    event["status"]="UNKNOWN_EMPTY_RESERVES"
                else:
                    with localcontext() as ctx:
                        ctx.prec=55
                        ratio=(Decimal(qr)/Decimal(10**18))/(Decimal(tr)/Decimal(10**dec))
                    event["price_proxy_eth_per_token"]=str(ratio)
                    event["status"]="NATIVE_CURVE_PROXY_CAPTURED"
        except (OSError,ValueError,RuntimeError,TypeError,KeyError) as exc:
            event["status"]="SOURCE_OR_RPC_UNAVAILABLE"
            event["error_class"]=type(exc).__name__
        result["results"].append(event)
    # A matching block hash at completion guards short reorgs during the calls.
    final=client.call("eth_getBlockByNumber",[hex(safe),False])
    if (not isinstance(final,dict) or cold.as_int(final.get("number"))!=safe
        or str(final.get("hash","")).lower()!=block_hash):
        raise RuntimeError("PONS_NATIVE_BLOCK_REORG_NO_CREDIT")
    result["sample_read_completed_utc"]=cold.iso(clock())
    result["status"]="RESEARCH_ONLY_COMPLETED"
    return cold.signed(result)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--repo-root",type=Path,default=Path("."))
    ap.add_argument("--validate-only",action="store_true")
    args=ap.parse_args()
    if args.validate_only:
        assert MAX_CASES==2 and CONTRACT.endswith("_V1")
        print("PUBLIC_PONS_NATIVE_CURVE_RESEARCH_POLICY_PASS")
        return
    report=read_proxies(args.repo_root.resolve(),cold.Client())
    print(json.dumps(report,sort_keys=True))


if __name__=="__main__":
    main()
