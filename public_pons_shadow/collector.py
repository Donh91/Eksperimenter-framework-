#!/usr/bin/env python3
"""Public-only exhaustive Pons V2 TokenLaunched shadow, never Alpha authority."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CHAIN_ID=4663
FACTORY="0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e"
TOPIC0="0x8d4aad4953d0ca700d468f3753aa14432d1b35b43ec6409f051fb6aa43a89607"
RPCS=("https://rpc.mainnet.chain.robinhood.com",
      "https://rpc.nodeflare.app/robinhood/public")
CONTRACT="PUBLIC_PONS_V2_FACTORY_EXHAUSTIVE_SHADOW_V1"
STATE=Path("public_pons_shadow/state.json")
MAX_SCAN=60000
MAX_CHUNK=2000
MAX_EVENTS=5000
HEX32=re.compile(r"^0x[a-f0-9]{64}$")
HEXDATA=re.compile(r"^0x(?:[a-f0-9]{2})*$")


def encode(o):
    return (json.dumps(o,sort_keys=True,separators=(",",":"),ensure_ascii=True)+"\n").encode()


def hash_obj(o):
    return hashlib.sha256(encode(o)).hexdigest()


def signed(o):
    return dict(o,sha256=hash_obj(o))


def as_int(v):
    if not isinstance(v,str) or re.fullmatch(r"0x[0-9A-Fa-f]+",v) is None:
        raise ValueError("INVALID_RPC_HEX_INTEGER")
    return int(v,16)


def now():
    return datetime.now(timezone.utc)


def iso(d):
    return d.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z")


def transport(url,method,params):
    body=json.dumps(dict(jsonrpc="2.0",id=1,method=method,params=params),separators=(",",":")).encode()
    req=urllib.request.Request(url,data=body,method="POST",headers={
        "Content-Type":"application/json","Accept":"application/json",
        "User-Agent":"PublicPonsShadow/1.0"})
    with urllib.request.urlopen(req,timeout=25) as rsp:
        if rsp.status!=200: raise RuntimeError("RPC_HTTP_FAILED")
        raw=rsp.read(4_000_001)
        if len(raw)>4_000_000: raise RuntimeError("RPC_RESPONSE_TOO_LARGE")
    obj=json.loads(raw)
    if not isinstance(obj,dict) or obj.get("error") or "result" not in obj:
        raise RuntimeError("RPC_RESULT_INVALID")
    return obj["result"]


class Client:
    def __init__(self,send=transport):
        self.send=send
        self.url=None

    def connect(self):
        for url in RPCS:
            try:
                if as_int(self.send(url,"eth_chainId",[]))==CHAIN_ID:
                    self.url=url
                    return
            except Exception:
                pass
        raise RuntimeError("NO_PUBLIC_CHAIN4663_RPC")

    def call(self,method,params):
        if self.url is None: raise RuntimeError("RPC_UNVERIFIED")
        try:
            return self.send(self.url,method,params)
        except Exception:
            other=RPCS[1] if self.url==RPCS[0] else RPCS[0]
            try:
                if as_int(self.send(other,"eth_chainId",[]))!=CHAIN_ID:
                    raise RuntimeError("WRONG_FALLBACK_CHAIN")
                result=self.send(other,method,params)
                self.url=other
                return result
            except Exception as exc:
                raise RuntimeError("BOTH_PUBLIC_RPC_ENDPOINTS_FAILED") from exc


def validate_log(raw,lo,hi):
    if not isinstance(raw,dict) or raw.get("removed") is not False:
        raise ValueError("LOG_REMOVED_OR_INVALID")
    if str(raw.get("address","")).lower()!=FACTORY:
        raise ValueError("LOG_FACTORY_MISMATCH")
    topics=raw.get("topics")
    if not isinstance(topics,list) or not 1<=len(topics)<=4:
        raise ValueError("LOG_TOPICS_INVALID")
    topics=[str(x).lower() for x in topics]
    if topics[0]!=TOPIC0 or any(HEX32.fullmatch(x) is None for x in topics):
        raise ValueError("LOG_TOPIC_MISMATCH")
    block=as_int(raw.get("blockNumber"))
    idx=as_int(raw.get("logIndex"))
    tx=str(raw.get("transactionHash","")).lower()
    bh=str(raw.get("blockHash","")).lower()
    data=str(raw.get("data","")).lower()
    if (not lo<=block<=hi or idx<0 or HEX32.fullmatch(tx) is None
        or HEX32.fullmatch(bh) is None or HEXDATA.fullmatch(data) is None):
        raise ValueError("LOG_PROVENANCE_MISMATCH")
    return dict(block=block,log_index=idx,tx_hash=tx,block_hash=bh,
                topics=topics,data=data)


def load_state(root):
    path=root/STATE
    if not path.exists(): return None
    s=json.loads(path.read_text())
    if (not isinstance(s,dict) or s.get("sha256")!=hash_obj({k:v for k,v in s.items() if k!="sha256"})
        or s.get("contract")!=CONTRACT or s.get("authority")!="PUBLIC_SHADOW_ONLY"
        or s.get("chain_id")!=CHAIN_ID or s.get("factory")!=FACTORY
        or s.get("topic0")!=TOPIC0 or type(s.get("last_scanned_block")) is not int
        or s["last_scanned_block"]<0):
        raise RuntimeError("CURSOR_UNTRUSTED_NO_ADVANCE")
    return s


def collect(root,client,run_id,checkout_sha,clock=now,
            max_scan=MAX_SCAN,bootstrap=40000,chunk=MAX_CHUNK):
    if not 1<=max_scan<=MAX_SCAN or not 1<=bootstrap<=40000 or not 1<=chunk<=MAX_CHUNK:
        raise ValueError("SCAN_LIMIT_INVALID")
    if (re.fullmatch(r"[0-9]{1,20}",run_id) is None
        or re.fullmatch(r"[a-f0-9]{40}",checkout_sha) is None):
        raise ValueError("RUN_OR_CHECKOUT_SHA_INVALID")
    started=clock()
    existing=load_state(root)
    client.connect()
    safe_head=as_int(client.call("eth_blockNumber",[]))-2
    if safe_head<0: raise RuntimeError("SAFE_HEAD_INVALID")
    lo=max(0,safe_head-bootstrap+1) if existing is None else existing["last_scanned_block"]+1
    if lo>safe_head:
        return dict(status="NO_NEW_SAFE_BLOCK",safe_head=safe_head)
    hi=min(safe_head,lo+max_scan-1)
    events=[]
    calls=0
    for begin in range(lo,hi+1,chunk):
        end=min(hi,begin+chunk-1)
        raw=client.call("eth_getLogs",[dict(fromBlock=hex(begin),toBlock=hex(end),
                                            address=FACTORY,topics=[TOPIC0])])
        calls+=1
        if not isinstance(raw,list): raise RuntimeError("LOG_RESULT_NOT_ARRAY")
        if len(events)+len(raw)>MAX_EVENTS:
            raise RuntimeError("SOURCE_EVENT_CAP_NO_CURSOR_ADVANCE")
        events.extend(validate_log(row,begin,end) for row in raw)
        if calls%5==0: time.sleep(.15)
    events.sort(key=lambda x:(x["block"],x["log_index"],x["tx_hash"]))
    if len({(x["tx_hash"],x["log_index"]) for x in events})!=len(events):
        raise RuntimeError("DUPLICATE_LOG_NO_ADVANCE")
    final_block=client.call("eth_getBlockByNumber",[hex(hi),False])
    if not isinstance(final_block,dict) or as_int(final_block.get("number"))!=hi:
        raise RuntimeError("END_BLOCK_UNVERIFIED")
    block_ts=as_int(final_block.get("timestamp"))
    if block_ts<=0 or block_ts>int(clock().timestamp())+60:
        raise RuntimeError("BLOCK_CLOCK_INVALID")
    ended=clock()
    receipt=signed(dict(
        contract=CONTRACT,authority="PUBLIC_SOURCE_SHADOW_ONLY",
        chain_id=CHAIN_ID,factory=FACTORY,topic0=TOPIC0,
        source="PUBLIC_ROBINHOOD_RPC",rpc_url=client.url,
        from_block=lo,to_block=hi,safe_head=safe_head,remaining_blocks=safe_head-hi,
        complete_bounded_range=True,bootstrap_not_full_history=existing is None,
        previous_cursor=existing["last_scanned_block"] if existing else None,
        previous_cursor_sha=existing["sha256"] if existing else None,
        block_utc=iso(datetime.fromtimestamp(block_ts,timezone.utc)),
        scan_started_at_utc=iso(started),scan_finished_at_utc=iso(ended),
        github_run_id=run_id,github_checkout_sha=checkout_sha,
        event_count=len(events),rpc_log_calls=calls,events_sha256=hash_obj(events),
        provider_first_known_verified=False,github_first_publication_verified=False,
        private_source_admitted=False,portfolio_action=False,alert_authority=False))
    state=signed(dict(contract=CONTRACT,authority="PUBLIC_SHADOW_ONLY",
                      chain_id=CHAIN_ID,factory=FACTORY,topic0=TOPIC0,
                      last_scanned_block=hi,bootstrap_from_block=lo if existing is None else existing["bootstrap_from_block"],
                      last_run_id=run_id,last_receipt_sha256=receipt["sha256"]))
    stem=started.strftime("%Y%m%dT%H%M%SZ")+"_"+run_id
    dest=root/"public_pons_shadow"/"runs"/started.strftime("%Y/%m/%d")
    ep=dest/(stem+"_events.json")
    rp=dest/(stem+"_receipt.json")
    if ep.exists() or rp.exists(): raise RuntimeError("IMMUTABLE_RUN_EXISTS")
    dest.mkdir(parents=True,exist_ok=True)
    ep.write_bytes(encode(events))
    rp.write_bytes(encode(receipt))
    (root/STATE).parent.mkdir(parents=True,exist_ok=True)
    (root/STATE).write_bytes(encode(state))
    return dict(status="PUBLIC_SHADOW_CAPTURED_UNPUBLISHED",from_block=lo,to_block=hi,
                events=len(events),remaining_blocks=safe_head-hi,receipt=str(rp.relative_to(root)))


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--repo-root",type=Path,default=Path("."))
    ap.add_argument("--validate-only",action="store_true")
    args=ap.parse_args()
    if args.validate_only:
        assert len(FACTORY)==42 and len(TOPIC0)==66
        assert all(x.startswith("https://") for x in RPCS)
        print("PUBLIC_PONS_POLICY_PASS")
        return
    result=collect(args.repo_root.resolve(),Client(),
                   os.environ.get("GITHUB_RUN_ID",""),
                   os.environ.get("GITHUB_CHECKOUT_SHA",""))
    print(json.dumps(result,sort_keys=True))


if __name__=="__main__":
    main()
