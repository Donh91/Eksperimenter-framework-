#!/usr/bin/env python3
"""Offline-only public shadow stage validator for the restricted publish job.

Never downloads, executes fetched files, grants private Alpha admission, or
manufactures first-known timing. The GitHub workflow owns git commit/readback.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

CHAIN_ID=4663
FACTORY="0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e"
TOPIC0="0x8d4aad4953d0ca700d468f3753aa14432d1b35b43ec6409f051fb6aa43a89607"
CONTRACTS={
  "cold":("PUBLIC_PONS_V2_FACTORY_EXHAUSTIVE_SHADOW_V1","PUBLIC_SOURCE_SHADOW_ONLY","PUBLIC_SHADOW_ONLY"),
  "hot":("PUBLIC_PONS_V2_HOT_SHADOW_V1","PUBLIC_HOT_SHADOW_ONLY","PUBLIC_HOT_SHADOW_ONLY"),
}
MAX_FILE=2_000_000


def loads(path):
    if not path.is_file() or path.is_symlink() or path.stat().st_size>MAX_FILE:
        raise ValueError("PONS_STAGE_FILE_INVALID")
    def pairs(xs):
        out={}
        for k,v in xs:
            if k in out: raise ValueError("PONS_DUPLICATE_JSON_KEY")
            out[k]=v
        return out
    return json.loads(path.read_text(),object_pairs_hook=pairs,
                      parse_constant=lambda v: (_ for _ in ()).throw(ValueError("PONS_NAN")))


def digest(obj):
    return hashlib.sha256((json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=True)+"\n").encode()).hexdigest()


def authentic_self_hash(obj):
    return isinstance(obj,dict) and obj.get("sha256")==digest({k:v for k,v in obj.items() if k!="sha256"})


def validate(stage,root,run_id,checkout_sha):
    if not re.fullmatch(r"[0-9]{1,20}",run_id):
        raise ValueError("PONS_RUN_ID_INVALID")
    if not re.fullmatch(r"[a-f0-9]{40}",checkout_sha):
        raise ValueError("PONS_CHECKOUT_INVALID")
    # Never accept symlink/extra files, including unexpected metadata and paths.
    files=[p for p in stage.rglob("*") if p.is_file() or p.is_symlink()]
    if not 3<=len(files)<=6:
        raise ValueError("PONS_STAGE_FILE_COUNT_INVALID")
    for file in files:
        if file.is_symlink() or not file.resolve().is_relative_to(stage.resolve()):
            raise ValueError("PONS_SYMLINK_OR_TRAVERSAL")
    expected=set()
    updates=[]
    for lane in ("hot","cold"):
        state_rel=Path("public_pons_shadow/hot_state.json" if lane=="hot" else "public_pons_shadow/state.json")
        state_file=stage/state_rel
        if not state_file.exists():
            continue
        contract,receipt_authority,state_authority=CONTRACTS[lane]
        state=loads(state_file)
        current_file=root/state_rel
        before=loads(current_file) if current_file.exists() else None
        if (not authentic_self_hash(state)
            or state.get("contract")!=contract or state.get("chain_id")!=CHAIN_ID
            or state.get("factory")!=FACTORY or state.get("topic0")!=TOPIC0
            or state.get("authority")!=state_authority
            or state.get("last_run_id")!=run_id):
            raise ValueError("PONS_STATE_INVALID")
        dirname=Path("public_pons_shadow/hot/runs" if lane=="hot" else "public_pons_shadow/runs")
        receipts=[p for p in files if p.name.endswith("_receipt.json")
                  and p.relative_to(stage).as_posix().startswith(dirname.as_posix()+"/")]
        if len(receipts)!=1:
            raise ValueError("PONS_RUN_RECEIPT_SET_INVALID")
        rp=receipts[0]; rel=rp.relative_to(stage)
        if not re.fullmatch(
            re.escape(dirname.as_posix())+r"/\d{4}/\d{2}/\d{2}/\d{8}T\d{6}Z_"+
            re.escape(run_id)+r"_receipt\.json", rel.as_posix()):
            raise ValueError("PONS_RECEIPT_PATH_INVALID")
        ep=rp.with_name(rp.name.replace("_receipt.json","_events.json"))
        events=loads(ep);receipt=loads(rp)
        if (not authentic_self_hash(receipt)
            or receipt.get("contract")!=contract or receipt.get("authority")!=receipt_authority
            or receipt.get("chain_id")!=CHAIN_ID or receipt.get("factory")!=FACTORY
            or receipt.get("topic0")!=TOPIC0
            or receipt.get("github_run_id")!=run_id
            or receipt.get("github_checkout_sha")!=checkout_sha
            or receipt.get("event_count")!=len(events)
            or receipt.get("events_sha256")!=digest(events)
            or not isinstance(events,list) or len(events)>5000
            or receipt.get("to_block") != (state.get("last_observed_block") if lane=="hot" else state.get("last_scanned_block"))
            or receipt.get("from_block",0)>receipt.get("to_block",-1)
            or state.get("last_receipt_sha256")!=receipt.get("sha256")
            or any(receipt.get(k) is not False for k in
                    ("private_source_admitted","provider_first_known_verified","alert_authority","portfolio_action"))):
            raise ValueError("PONS_RECEIPT_INVALID")
        if lane=="hot":
            prev=before.get("last_observed_block") if before else None
            if (receipt.get("previous_hot_block")!=prev
                or receipt.get("previous_hot_state_sha")!=(before.get("sha256") if before else None)
                or receipt.get("history_complete") is not False
                or receipt.get("canonical_census_complete") is not False
                or receipt.get("github_first_publication_verified") is not False
                or not isinstance(state.get("last_observed_block_hash"),str)
                or re.fullmatch(r"0x[a-f0-9]{64}",state.get("last_observed_block_hash","")) is None
                or receipt.get("observed_head_block_hash")!=state.get("last_observed_block_hash")
                or not isinstance(receipt.get("missed_hot_ranges"),list)
                or receipt.get("missed_hot_range_count")!=len(receipt["missed_hot_ranges"])):
                raise ValueError("PONS_HOT_CONTINUITY_INVALID")
            for gap in receipt["missed_hot_ranges"]:
                if (not isinstance(gap,dict) or type(gap.get("from_block")) is not int
                    or type(gap.get("to_block")) is not int or gap["from_block"]>gap["to_block"]):
                    raise ValueError("PONS_HOT_GAP_INVALID")
            if prev is not None and receipt["to_block"]<=prev:
                raise ValueError("PONS_HOT_NOT_ADVANCING")
            if before and before.get("unobserved_gaps")!=receipt.get("missed_hot_ranges")[:len(before.get("unobserved_gaps",[]))]:
                raise ValueError("PONS_HOT_GAP_HISTORY_CHANGED")
            if state.get("unobserved_gaps")!=receipt.get("missed_hot_ranges"):
                raise ValueError("PONS_HOT_STATE_GAP_MISMATCH")
        else:
            prev=before.get("last_scanned_block") if before else None
            if (receipt.get("previous_cursor")!=prev
                or receipt.get("previous_cursor_sha")!=(before.get("sha256") if before else None)
                or not receipt.get("complete_bounded_range")
                or (prev is not None and receipt["from_block"]!=prev+1)):
                raise ValueError("PONS_COLD_CONTINUITY_INVALID")
        expected.update((state_file,rp,ep))
        updates.extend((state_rel,rp.relative_to(stage),ep.relative_to(stage)))
    if set(files)!=expected:
        raise ValueError("PONS_EXTRA_OR_MISSING_ARTIFACT")
    if not updates:
        raise ValueError("PONS_NO_VALID_UPDATES")
    # Read-only source run origin verified against the checked-out main tree.
    subprocess.run(["git","merge-base","--is-ancestor",checkout_sha,"HEAD"],check=True)
    for source in (".github/workflows/public-pons-shadow.yml","public_pons_shadow/collector.py",
                   "public_pons_shadow/hot_collector.py"):
        source_blob=subprocess.check_output(["git","rev-parse",checkout_sha+":"+source],text=True).strip()
        target_blob=subprocess.check_output(["git","rev-parse","HEAD:"+source],text=True).strip()
        if source_blob!=target_blob:
            raise ValueError("PONS_COLLECTOR_CHANGED_AFTER_CAPTURE")
    for rel in updates:
        dest=root/rel
        if "/runs/" in rel.as_posix() and dest.exists():
            raise ValueError("PONS_IMMUTABLE_RUN_COLLISION")
    return updates


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--stage-dir",type=Path,required=True)
    ap.add_argument("--repo-root",type=Path,default=Path("."))
    ap.add_argument("--run-id",required=True)
    ap.add_argument("--checkout-sha",required=True)
    args=ap.parse_args()
    stage=args.stage_dir.resolve(strict=True)
    root=args.repo_root.resolve(strict=True)
    updates=validate(stage,root,args.run_id,args.checkout_sha)
    for rel in updates:
        dest=root/rel
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(stage/rel,dest)
    print("PUBLIC_PONS_READ_ONLY_SOURCE_TO_WRITER_VALIDATED_UNADMITTED files="+str(len(updates)))


if __name__=="__main__":
    main()
