#!/usr/bin/env python3
"""Pons V2 public curve sell-friction probe, STRICTLY research-only.

Use already published public source/forward/native cohorts, exact source event
(token, curve, native ETH pair), factory getLaunchedToken phase and same-safe-
block view reads. No wallet, sender, approval, transaction simulation, USD
price, user position, fill, executable trade quote or portfolio authority.
Protocol read ABI: https://docs.ponsfamily.com/v2 (2026-11-05).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

try:
    from public_pons_shadow import collector as cold
    from public_pons_shadow import forward_outcome as fw
    from public_pons_shadow import native_curve_diagnostic_v1 as native
    from public_pons_shadow import v2_quote_math_research_v1 as math
except ImportError:
    import collector as cold
    import forward_outcome as fw
    import native_curve_diagnostic_v1 as native
    import v2_quote_math_research_v1 as math

CONTRACT="PUBLIC_PONS_V2_ONCHAIN_SELL_FRICTION_SHADOW_V1"
CURVE=Path("public_pons_shadow/curve_state.json")
FACTORY="0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e"
SEL_LAUNCH="0x3cf28b5a"       # getLaunchedToken(address)
SEL_FEE="0x24a9d853"          # feeBps()
SEL_TAX="0xc1bb8901"          # creatorTaxBps()
SEL_SELLABLE="0x808bcddc"     # sellableTokens()
SEL_READY="0xc68360a5"        # readyToGraduate()
SEL_GRADUATED="0xe7c2b772"    # graduated()
SIZE_TOKENS=(100_000,1_000_000,10_000_000)
MAX_CASES=2
HEX_32=re.compile(r"0x[0-9a-fA-F]{64}\Z")
HEX_LAUNCH=re.compile(r"0x[0-9a-fA-F]{960}\Z")


def committed_source(root,path):
    root=Path(root)
    if (root/".git").exists():
        try:
            raw=subprocess.check_output(
                ["git","show","HEAD:"+str(path)],cwd=root,
                stderr=subprocess.DEVNULL,timeout=4)
        except (subprocess.CalledProcessError,subprocess.TimeoutExpired,OSError):
            return None
        if len(raw)>2_000_000:raise ValueError("SELL_SOURCE_TOO_LARGE")
        o=json.loads(raw)
    else:
        p=root/path
        if not p.exists():return None
        if p.is_symlink() or p.stat().st_size>2_000_000:
            raise ValueError("SELL_SOURCE_UNTRUSTED_FILE")
        o=json.loads(p.read_text())
    if (not isinstance(o,dict) or o.get("sha256")!=cold.hash_obj({k:v for k,v in o.items() if k!="sha256"})
        or o.get("authority")!="PUBLIC_RESEARCH_ONLY"
        or not isinstance(o.get("candidates"),list)):
        raise ValueError("SELL_SOURCE_STATE_UNTRUSTED")
    return o


def u32(raw):
    if not isinstance(raw,str) or not HEX_32.fullmatch(raw):
        raise ValueError("SELL_VIEW_RETURNS_NOT_ONE_UINT_WORD")
    return int(raw,16)


def b32(raw):
    v=u32(raw)
    if v not in (0,1):
        raise ValueError("SELL_VIEW_BOOL_INVALID")
    return bool(v)


def decode_factory(raw):
    if not isinstance(raw,str) or not HEX_LAUNCH.fullmatch(raw):
        raise ValueError("SELL_FACTORY_TUPLE_MALFORMED")
    w=[int(raw[2+i*64:2+(i+1)*64],16) for i in range(15)]
    if w[9] not in (0,1) or w[14]!=1 or w[10] not in range(4):
        raise ValueError("SELL_FACTORY_PHASE_OR_EXISTS_INVALID")
    def address(i):
        if w[i]>>160:raise ValueError("SELL_FACTORY_ADDRESS_PADDING_INVALID")
        return "0x"+f"{w[i]:040x}"
    return {"token":address(0),"curve":address(1),"pair":address(4),
            "creator_tax_bps":w[8],"phase":w[10],
            "exists":True,"buyback_enabled":bool(w[9])}


def call_view(client,address,selector,block,extra=""):
    data=selector+extra
    return client.call("eth_call",[{"to":address,"data":data},hex(block)])


def read_sell_shadow(root,client,clock=None):
    clock=clock or (lambda:datetime.now(timezone.utc))
    started=clock()
    result={"contract":CONTRACT,"authority":"PUBLIC_RESEARCH_ONLY",
            "as_of_utc":cold.iso(started),"source":"PUBLIC_COMMITTED_PONS_CURVE_COHORT",
            "sample_count":0,"results":[],
            "execution_verified":False,"gas_verified":False,
            "wallet_balance_verified":False,"full_exit_verified":False,
            "source_first_known_verified":False,"economic_edge_verified":False,
            "auto_trade_allowed":False,"alert_authority":False}
    curve=committed_source(root,CURVE)
    forward=committed_source(root,fw.STATE)
    if curve is None or forward is None:
        result["status"]="NO_PUBLISHED_PUBLIC_SOURCES"
        return cold.signed(result)
    if curve.get("contract")!="PUBLIC_PONS_NATIVE_DUE_SHADOW_V1" or forward.get("contract")!=fw.CONTRACT:
        raise ValueError("SELL_WRONG_SOURCE_CONTRACT")
    forward_by_id={c["id"]:c for c in forward["candidates"]}
    frozen=curve["candidates"][:MAX_CASES]
    if not frozen:
        result["status"]="NO_FROZEN_NATIVE_CANDIDATES"
        return cold.signed(result)
    client.connect()  # source client requires exact Robinhood chain ID 4663.
    block=cold.as_int(client.call("eth_blockNumber",[]))-2
    if block<0:raise ValueError("SELL_SAFE_BLOCK_NOT_FOUND")
    first=client.call("eth_getBlockByNumber",[hex(block),False])
    if (not isinstance(first,dict) or cold.as_int(first.get("number"))!=block
        or not native.HEX_HASH.fullmatch(str(first.get("hash") or ""))):
        raise ValueError("SELL_BLOCK_HEADER_INVALID")
    bh=first["hash"].lower()
    result.update({"read_block_number":block,"read_block_hash":bh})
    for f in frozen:
        row={"id":f.get("id"),"token":f.get("token"),"curve":f.get("curve"),
             "original_native_t0_utc":f.get("native_t0_utc"),
             "status":"UNKNOWN_RESEARCH_ONLY","phase":None,"quote_asset":"NATIVE_ETH",
             "token_sizes":[], "trade_execution_verified":False,
             "source_authority":"PUBLIC_RESEARCH_ONLY",
             "full_exit_verified":False}
        try:
            if f.get("first_known_verified") is not False or f.get("execution_verified") is not False:
                raise ValueError("SELL_FALSE_FROZEN_SOURCE_AUTHORITY")
            orig=forward_by_id.get(f["id"])
            if orig is None:raise ValueError("SELL_FORWARD_SOURCE_MISSING")
            token,curve_address,pair=native.source_case(root,orig)
            if (token!=f["token"] or curve_address!=f["curve"]
                or pair!=native.ZERO or f["source_receipt_sha256"]!=orig["source_receipt_sha256"]
                or f["source_receipt"]!=orig["source_receipt"]
                or f["original_forward_t0_utc"]!=orig["t0_utc"]):
                raise ValueError("SELL_CROSS_SOURCE_IDENTITY_MISMATCH")
            # Strict 15-word static ABI factory struct, not ticker or pool inference.
            launch=decode_factory(call_view(
                client,FACTORY,SEL_LAUNCH,block,extra=("0"*24+token[2:])))
            if launch["token"]!=token or launch["curve"]!=curve_address or launch["pair"]!=native.ZERO:
                raise ValueError("SELL_FACTORY_SOURCE_IDENTITY_MISMATCH")
            fee=u32(call_view(client,curve_address,SEL_FEE,block))
            tax=u32(call_view(client,curve_address,SEL_TAX,block))
            sellable=u32(call_view(client,curve_address,SEL_SELLABLE,block))
            ready=b32(call_view(client,curve_address,SEL_READY,block))
            graduated=b32(call_view(client,curve_address,SEL_GRADUATED,block))
            reserves=native.decode_reserves(call_view(
                client,curve_address,native.RESERVES_SELECTOR,block))
            dec=u32(call_view(client,token,native.DECIMALS_SELECTOR,block))
            if dec>30:raise ValueError("SELL_DECIMALS_INVALID")
            if tax!=launch["creator_tax_bps"]:
                raise ValueError("SELL_FACTORY_CURVE_CREATOR_TAX_CONFLICT")
            if (graduated != (launch["phase"]!=0)
                or (launch["phase"]==0 and ready!=(sellable==0))):
                raise ValueError("SELL_FACTORY_CURVE_PHASE_CONTRADICTION")
            state={"chain_id":4663,"quote_asset":"NATIVE_ETH",
                   "quote_reserve_wei":reserves[0],"token_reserve_raw":reserves[1],
                   "sellable_tokens_raw":sellable,"fee_bps":fee,
                   "creator_tax_bps":tax,"phase":launch["phase"],
                   "ready_to_graduate":ready,"graduated":graduated}
            math.valid_state(state)
            row.update({"phase":launch["phase"],"ready_to_graduate":ready,
                "graduated":graduated,"fee_bps":fee,"creator_tax_bps":tax,
                "sellable_tokens_raw":str(sellable),
                "quote_reserve_wei":str(reserves[0]),
                "token_reserve_raw":str(reserves[1]),
                "token_decimals":dec})
            for size in SIZE_TOKENS:
                r=math.research_sell(state,size*10**dec)
                row["token_sizes"].append({"tokens":size,
                    "calculation_status":r["status"],
                    "indicative_net_quote_wei":r.get("indicative_net_quote_out_wei"),
                    "execution_verified":False,
                    "wallet_amount_verified":False})
            row["status"]="RESEARCH_PHASE_AND_FEES_OBSERVED_NO_EXECUTION"
        except (ValueError,KeyError,TypeError,OSError,RuntimeError) as exc:
            row["status"]="UNKNOWN_ONCHAIN_PHASE_OR_FEE_SOURCE"
            row["reason_class"]=type(exc).__name__
            row["token_sizes"]=[]
        row["read_at_utc"]=cold.iso(clock())
        result["results"].append(row)
    final=client.call("eth_getBlockByNumber",[hex(block),False])
    if (not isinstance(final,dict) or cold.as_int(final.get("number"))!=block
        or str(final.get("hash","")).lower()!=bh):
        raise ValueError("SELL_SAFE_BLOCK_REORG_INVALIDATES_ALL")
    result["sample_count"]=len(result["results"])
    result["status"]="RESEARCH_ONLY_COMPLETED"
    result["finished_at_utc"]=cold.iso(clock())
    return cold.signed(result)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--repo-root",type=Path,default=Path("."))
    ap.add_argument("--validate-only",action="store_true")
    args=ap.parse_args()
    if args.validate_only:
        assert MAX_CASES==2 and FACTORY==cold.FACTORY
        print("PUBLIC_PONS_V2_SELL_FRICTION_SOURCE_POLICY_PASS")
    else:
        print(json.dumps(read_sell_shadow(args.repo_root.resolve(),cold.Client()),sort_keys=True))


if __name__=="__main__":
    main()
