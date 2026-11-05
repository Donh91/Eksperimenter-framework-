#!/usr/bin/env python3
"""Bounded PUBLIC Pons V2 history gap, distinct from COLD/HOT/private canonical.

Start is a fixed, public chain height immediately after an independently
observed private canonical checkpoint. This is NOT source admission. A public
COLD bootstrap near head leaves older blocks; this source examines those
blocks without altering COLD, HOT or PRIVATE cursors. Fail closed on 429.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

try:
    from public_pons_shadow import collector as cold
except ImportError:
    import collector as cold

START_BLOCK=95_013_239
MAX_BLOCKS_PER_RUN=180_000  # 6x speed; <= existing cold.MAX_SCAN, 90s timeout still applies
MAX_CHUNK=2_000
STATE=Path("public_pons_shadow/historical_state.json")
RUNS=Path("public_pons_shadow/historical/runs")
CONTRACT="PUBLIC_PONS_V2_HISTORICAL_GAP_SHADOW_WRAPPER_V1"


def capture(root,client,run_id,checkout_sha,*,clock=cold.now):
    if (re.fullmatch(r"[0-9]{1,20}",run_id or "") is None
        or re.fullmatch(r"[a-f0-9]{40}",checkout_sha or "") is None):
        raise ValueError("HISTORICAL_RUN_BINDING_INVALID")
    # The source must already have a self-consistent cold bootstrap. We
    # *never* overwrite its live COLD state to import old blocks.
    current=cold.load_state(root)
    if current is None or current["bootstrap_from_block"]<=START_BLOCK:
        return {"status":"PUBLIC_HISTORICAL_TARGET_UNVERIFIED_NO_ADVANCE",
                "authority":"PUBLIC_RESEARCH_ONLY"}
    stop=current["bootstrap_from_block"]-1
    previous=root/STATE
    if previous.is_symlink():
        raise ValueError("HISTORICAL_STATE_SYMLINK_DENIED")
    if previous.is_file() and previous.stat().st_size>2_000_000:
        raise ValueError("HISTORICAL_STATE_OVERSIZED")
    with tempfile.TemporaryDirectory(prefix="public-pons-historical-") as temp:
        isolated=Path(temp)
        statefile=isolated/cold.STATE
        if previous.exists():
            statefile.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(previous,statefile)
        result=cold.collect(isolated,client,run_id,checkout_sha,clock=clock,
                            max_scan=MAX_BLOCKS_PER_RUN,chunk=MAX_CHUNK,
                            explicit_start=START_BLOCK,stop_before=stop)
        if result["status"]=="PUBLIC_HISTORICAL_GAP_RESEARCH_COMPLETE":
            return {"status":"PUBLIC_HISTORICAL_GAP_RESEARCH_COMPLETE",
                    "authority":"PUBLIC_RESEARCH_ONLY",
                    "from_block":START_BLOCK,"stop_block":stop,
                    "last_scanned_block":result["last_scanned_block"],
                    "private_canonical_source_admitted":False}
        if result["status"]=="NO_NEW_SAFE_BLOCK":
            return {"status":"PUBLIC_HISTORICAL_NO_SAFE_BLOCK",
                    "authority":"PUBLIC_RESEARCH_ONLY"}
        if result["status"]!="PUBLIC_SHADOW_CAPTURED_UNPUBLISHED":
            raise ValueError("HISTORICAL_COLLECTOR_UNEXPECTED_RESULT")
        receipt_path=isolated/result["receipt"]
        events_path=receipt_path.with_name(receipt_path.name.replace("_receipt.json","_events.json"))
        state=json.loads(statefile.read_text())
        receipt=json.loads(receipt_path.read_text())
        if (state.get("last_scanned_block")!=result["to_block"]
            or receipt.get("from_block")<START_BLOCK
            or receipt.get("to_block")>=current["bootstrap_from_block"]
            or receipt.get("complete_bounded_range") is not True
            or receipt.get("private_source_admitted") is not False
            or receipt.get("provider_first_known_verified") is not False):
            raise ValueError("HISTORICAL_SOURCE_BOUNDARY_INVALID")
        relative=receipt_path.relative_to(isolated/"public_pons_shadow/runs")
        dest=root/RUNS/relative
        dest_events=dest.with_name(dest.name.replace("_receipt.json","_events.json"))
        if dest.exists() or dest_events.exists():
            raise ValueError("HISTORICAL_IMMUTABLE_RUN_COLLISION")
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(receipt_path,dest)
        shutil.copyfile(events_path,dest_events)
        previous.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(statefile,previous)
        return {
            "status":"PUBLIC_HISTORICAL_CAPTURED_UNPUBLISHED",
            "contract":CONTRACT,"authority":"PUBLIC_RESEARCH_ONLY",
            "receipt":dest.relative_to(root).as_posix(),
            "from_block":result["from_block"],"to_block":result["to_block"],
            "remaining_to_public_cold_bootstrap":stop-result["to_block"],
            "private_canonical_source_admitted":False,
            "prospective_first_known_verified":False,
            "portfolio_action":False,"alert_authority":False,
        }


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--repo-root",type=Path,default=Path("."))
    ap.add_argument("--validate-only",action="store_true")
    args=ap.parse_args()
    if args.validate_only:
        assert START_BLOCK>0 and 0<MAX_BLOCKS_PER_RUN<=cold.MAX_SCAN
        print("PUBLIC_PONS_HISTORICAL_GAP_SOURCE_RESEARCH_ONLY_PASS")
        return
    result=capture(args.repo_root.resolve(),cold.Client(),
                   os.environ.get("GITHUB_RUN_ID",""),
                   os.environ.get("GITHUB_CHECKOUT_SHA",""))
    print(json.dumps(result,sort_keys=True))


if __name__=="__main__":
    main()
