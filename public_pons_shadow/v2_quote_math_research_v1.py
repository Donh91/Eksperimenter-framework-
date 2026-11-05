#!/usr/bin/env python3
"""Pons V2 curve quote math challenger, research-only and NEVER a trade quote.

Integer order follows https://docs.ponsfamily.com/v2#quoting-in-both-directions
(official Pons V2 docs, inspected 2026-11-05).
Inputs are UNTRUSTED until bound to one safe chain 4663 block+hash and phase
through a separately audited collector. No wallet address, signer, network,
contract call, USD conversion, fill record or portfolio signal is created here.
"""
from __future__ import annotations
import json

CONTRACT="PUBLIC_PONS_V2_SIZE_FEE_QUOTE_CHALLENGER_V1"
BPS=10_000
UINT256=2**256-1
PHASES={0:"NOT_GRADUATED",1:"SWEPT",2:"POOL_CREATED",3:"RESCUED"}
CAPACITY_WEI=(10**15,10**16,5*10**16,10**17)


def uint(value,name,zero=True):
    if type(value) is not int or value<0 or value>UINT256 or (not zero and value==0):
        raise ValueError(f"{name}_UINT256_INVALID")
    return value


def valid_state(state):
    """Reject unknown phase, missing independent fee/route inputs or stale client defaults."""
    if not isinstance(state,dict):
        raise ValueError("PONS_V2_STATE_REQUIRED")
    required={"chain_id","quote_asset","quote_reserve_wei","token_reserve_raw",
              "sellable_tokens_raw","fee_bps","creator_tax_bps",
              "phase","ready_to_graduate","graduated"}
    if set(state)!=required:
        raise ValueError("PONS_V2_STATE_INCOMPLETE_OR_EXCESS")
    if type(state["chain_id"]) is not int or state["chain_id"]!=4663:
        raise ValueError("PONS_V2_CHAIN_UNVERIFIED")
    if state["quote_asset"]!="NATIVE_ETH":
        raise ValueError("PONS_V2_NON_NATIVE_QUOTE_NOT_SUPPORTED")
    for key in ("quote_reserve_wei","token_reserve_raw","sellable_tokens_raw"):
        uint(state[key],key,zero=key=="sellable_tokens_raw")
    if state["sellable_tokens_raw"]>state["token_reserve_raw"]:
        raise ValueError("PONS_V2_SELLABLE_EXCEEDS_RESERVE")
    for key in ("fee_bps","creator_tax_bps"):
        if type(state[key]) is not int or not 0<=state[key]<BPS:
            raise ValueError("PONS_V2_FEE_UNKNOWN")
    if state["fee_bps"]+state["creator_tax_bps"]>=BPS-100:
        raise ValueError("PONS_V2_FEE_CAP_UNSAFE")
    if type(state["phase"]) is not int or state["phase"] not in PHASES:
        raise ValueError("PONS_V2_PHASE_UNVERIFIED")
    if type(state["ready_to_graduate"]) is not bool or type(state["graduated"]) is not bool:
        raise ValueError("PONS_V2_ROUTE_FLAGS_UNVERIFIED")
    if state["graduated"] and state["phase"]==0:
        raise ValueError("PONS_V2_PHASE_CONTRADICTION")
    return state


def amount_out(amount_in,reserve_in,reserve_out):
    uint(amount_in,"input",False)
    uint(reserve_in,"reserve_in",False)
    uint(reserve_out,"reserve_out",False)
    return amount_in*reserve_out//(reserve_in+amount_in)


def amount_in(amount_out_value,reserve_in,reserve_out):
    uint(amount_out_value,"output",False)
    uint(reserve_in,"reserve_in",False)
    uint(reserve_out,"reserve_out",False)
    if amount_out_value>=reserve_out:
        raise ValueError("PONS_V2_EXCEEDS_RESERVE")
    return amount_out_value*reserve_in//(reserve_out-amount_out_value)+1


def ceil_div(a,b):
    if a<0 or b<=0:
        raise ValueError("PONS_V2_DIVISION_INVALID")
    return (a+b-1)//b


def base_result(side):
    return {"contract":CONTRACT,"authority":"RESEARCH_ONLY_NO_TRADE",
            "side":side,"status":"UNKNOWN",
            "source_clock_verified":False,"source_block_hash_verified_by_module":False,
            "recipient_tax_verified":False,"execution_verified":False,
            "full_exit_verified":False,"economic_edge_verified":False,
            "gas_slippage_and_wallet_balance_verified":False,
            "can_broadcast":False,"trading_authority":False}


def research_buy(state,quote_in_wei,snipe_tax_bps):
    """Explicit recipient-specific snipe tax required, never assume 0."""
    out=base_result("BUY")
    try:
        s=valid_state(state)
        quote=uint(quote_in_wei,"quote_in",False)
        if snipe_tax_bps is None:
            out["status"]="UNKNOWN_RECIPIENT_SPECIFIC_SNIPE_TAX"
            return out
        if type(snipe_tax_bps) is not int or not 0<=snipe_tax_bps<BPS:
            raise ValueError("PONS_V2_SNIPE_TAX_INVALID")
        if s["phase"]!=0 or s["graduated"]:
            out["status"]="CURVE_CLOSED_ROUTE_V4_OR_RECOVERY_REQUIRED"
            return out
        sellable=s["sellable_tokens_raw"]
        if sellable<=0 or s["ready_to_graduate"]:
            out["status"]="CURVE_BUY_CLOSED_READY_TO_GRADUATE"
            return out
        max_snipe=BPS-s["fee_bps"]-s["creator_tax_bps"]-100
        eff_snipe=min(snipe_tax_bps,max_snipe) if snipe_tax_bps>0 else 0
        fee=quote*s["fee_bps"]//BPS
        creator=quote*s["creator_tax_bps"]//BPS
        snipe=quote*eff_snipe//BPS
        net=quote-fee-creator-snipe
        if net<=0:
            out["status"]="UNKNOWN_NET_INPUT_ZERO"
            return out
        tokens=amount_out(net,s["quote_reserve_wei"],s["token_reserve_raw"])
        spent=quote
        if tokens>sellable:
            tokens=sellable
            required_net=amount_in(sellable,s["quote_reserve_wei"],s["token_reserve_raw"])
            required_gross=ceil_div(required_net*BPS,
                                    BPS-s["fee_bps"]-s["creator_tax_bps"]-eff_snipe)
            spent=min(required_gross,quote)
        if tokens<=0 or spent<=0:
            out["status"]="UNKNOWN_NO_MEANINGFUL_FILL"
            return out
        out.update({"status":"THEORETICAL_CURVE_BUY_NOT_EXECUTABLE",
            "quote_in_wei":str(quote),"token_out_raw":str(tokens),
            "nominal_spent_wei":str(spent),"refund_wei":str(quote-spent),
            "fee_bps":s["fee_bps"],"creator_tax_bps":s["creator_tax_bps"],
            "snipe_tax_bps_explicit_input":snipe_tax_bps,
            "snipe_tax_bps_capped":eff_snipe,
            "recipient_tax_verified":False,
            "possible_partial_graduation_fill":spent<quote})
        return out
    except (ValueError,TypeError,OverflowError) as exc:
        out["status"]="UNKNOWN_INVALID_STATE_OR_SIZE"
        out["reason_code"]=str(exc)
        return out


def research_sell(state,tokens_in_raw):
    """Purely theoretical pregraduation *sell* proceeds, not a full exit quote."""
    out=base_result("SELL")
    try:
        s=valid_state(state)
        qty=uint(tokens_in_raw,"tokens_in",False)
        if s["phase"]!=0 or s["graduated"]:
            out["status"]="CURVE_CLOSED_ROUTE_V4_OR_RECOVERY_REQUIRED"
            return out
        if s["ready_to_graduate"]:
            out["status"]="CURVE_SELL_CLOSED_READY_TO_GRADUATE"
            return out
        gross=amount_out(qty,s["token_reserve_raw"],s["quote_reserve_wei"])
        fee=gross*s["fee_bps"]//BPS
        creator=gross*s["creator_tax_bps"]//BPS
        net=gross-fee-creator
        if net<=0:
            out["status"]="UNKNOWN_ZERO_SELL_PROCEEDS"
            return out
        out.update({"status":"THEORETICAL_CURVE_SELL_NOT_EXECUTABLE",
            "tokens_in_raw":str(qty),"gross_quote_out_wei":str(gross),
            "fee_quote_wei":str(fee),"creator_tax_quote_wei":str(creator),
            "indicative_net_quote_out_wei":str(net),
            "fee_bps":s["fee_bps"],"creator_tax_bps":s["creator_tax_bps"],
            "snipe_tax_applied_on_sell":False})
        return out
    except (ValueError,TypeError,OverflowError) as exc:
        out["status"]="UNKNOWN_INVALID_STATE_OR_SIZE"
        out["reason_code"]=str(exc)
        return out


def main():
    # No provider, wallet, URL or action. Intentional empty smoke (research API only).
    print(json.dumps({"contract":CONTRACT,"status":"PURE_OFFLINE_RESEARCH_PRIMITIVE",
                      "side_count":2,"external_calls":0,"trading_authority":False},
                     sort_keys=True))


if __name__=="__main__":
    main()
