#!/usr/bin/env python3
"""Public HOT/COLD source custody reconciliation. Completeness != live timing.

Read-only proof from the *published repository*: individual COLD immutable
receipt/event digests, chained previous cursor/state SHA and current state SHA.
Late COLD coverage of a HOT gap NEVER restores first-known/live opportunity.
No token decisions, trading, private data, new scheduler or writer.
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
from datetime import datetime, timezone

try:
    from public_pons_shadow import collector as cold
except ImportError:
    import collector as cold

CONTRACT="PUBLIC_PONS_HOT_COLD_SOURCE_CUSTODY_AUDIT_V1"
MAX_RECEIPTS=10000
MAX_BYTES=2_000_000
RECEIPT_PATTERN=re.compile(
    r"public_pons_shadow/runs/\d{4}/\d{2}/\d{2}/"
    r"\d{8}T\d{6}Z_\d{1,20}_receipt\.json\Z"
)
EXPECTED_COLD_STATE={
    "contract","authority","chain_id","factory","topic0",
    "last_scanned_block","bootstrap_from_block",
    "last_run_id","last_receipt_sha256","sha256",
}


def load_json(path):
    if (not path.is_file() or path.is_symlink()
        or path.stat().st_size>MAX_BYTES):
        raise ValueError("COVERAGE_FILE_MISSING_OVERSIZED_OR_SYMLINK")
    def pairs(items):
        d={}
        for k,v in items:
            if k in d:
                raise ValueError("COVERAGE_DUPLICATE_JSON_KEY")
            d[k]=v
        return d
    return json.loads(path.read_text(),object_pairs_hook=pairs,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError("COVERAGE_NONFINITE")))


def audited(root,clock=None):
    clock=clock or (lambda:datetime.now(timezone.utc))
    root=Path(root)
    coldstate=load_json(root/cold.STATE)
    if (set(coldstate)!=EXPECTED_COLD_STATE
        or coldstate.get("sha256")!=cold.hash_obj({k:v for k,v in coldstate.items() if k!="sha256"})
        or coldstate.get("contract")!=cold.CONTRACT
        or coldstate.get("authority")!="PUBLIC_SHADOW_ONLY"
        or coldstate.get("chain_id")!=cold.CHAIN_ID
        or coldstate.get("factory")!=cold.FACTORY
        or coldstate.get("topic0")!=cold.TOPIC0):
        raise ValueError("COVERAGE_COLD_STATE_UNTRUSTED")
    receipts=sorted((root/"public_pons_shadow/runs").rglob("*_receipt.json"))
    if not 1<=len(receipts)<=MAX_RECEIPTS:
        raise ValueError("COVERAGE_COLD_RECEIPT_CENSUS_INVALID")
    previous=None
    reconstructed=None
    events_total=0
    last_clock=None
    for i,rp in enumerate(receipts):
        rel=rp.relative_to(root).as_posix()
        if not RECEIPT_PATTERN.fullmatch(rel):
            raise ValueError("COVERAGE_RECEIPT_OUTSIDE_IMMUTABLE_PATH")
        receipt=load_json(rp)
        ep=rp.with_name(rp.name.replace("_receipt.json","_events.json"))
        events=load_json(ep)
        if (not isinstance(receipt,dict)
            or receipt.get("sha256")!=cold.hash_obj({k:v for k,v in receipt.items() if k!="sha256"})
            or not isinstance(events,list)
            or receipt.get("event_count")!=len(events)
            or receipt.get("events_sha256")!=cold.hash_obj(events)
            or receipt.get("contract")!=cold.CONTRACT
            or receipt.get("authority")!="PUBLIC_SOURCE_SHADOW_ONLY"
            or receipt.get("factory")!=cold.FACTORY
            or receipt.get("chain_id")!=cold.CHAIN_ID
            or receipt.get("topic0")!=cold.TOPIC0
            or receipt.get("complete_bounded_range") is not True
            or receipt.get("bootstrap_not_full_history") is not (i==0)
            or any(receipt.get(k) is not False for k in (
                "private_source_admitted","provider_first_known_verified",
                "github_first_publication_verified","portfolio_action","alert_authority"))):
            raise ValueError("COVERAGE_COLD_RECEIPT_OR_EVENTS_UNTRUSTED")
        a,b=receipt.get("from_block"),receipt.get("to_block")
        if type(a) is not int or type(b) is not int or a<0 or b<a:
            raise ValueError("COVERAGE_INVALID_BLOCK_RANGE")
        if i==0:
            if (a!=coldstate["bootstrap_from_block"]
                or receipt.get("previous_cursor") is not None
                or receipt.get("previous_cursor_sha") is not None):
                raise ValueError("COVERAGE_BOOTSTRAP_MISMATCH")
        else:
            if (a!=previous+1 or receipt.get("previous_cursor")!=previous
                or receipt.get("previous_cursor_sha")!=reconstructed["sha256"]):
                raise ValueError("COVERAGE_NONCONTIGUOUS_OR_UNLINKED_RECEIPT")
        if not isinstance(receipt.get("github_run_id"),str):
            raise ValueError("COVERAGE_RUN_ID_MISSING")
        if last_clock is not None and str(receipt.get("scan_finished_at_utc",""))<last_clock:
            raise ValueError("COVERAGE_SOURCE_CLOCK_REVERSED")
        last_clock=receipt["scan_finished_at_utc"]
        reconstructed=cold.signed({
            "contract":cold.CONTRACT,"authority":"PUBLIC_SHADOW_ONLY",
            "chain_id":cold.CHAIN_ID,"factory":cold.FACTORY,"topic0":cold.TOPIC0,
            "last_scanned_block":b,
            "bootstrap_from_block":coldstate["bootstrap_from_block"],
            "last_run_id":receipt["github_run_id"],
            "last_receipt_sha256":receipt["sha256"],
        })
        previous=b
        events_total+=len(events)
    if reconstructed!=coldstate:
        raise ValueError("COVERAGE_FINAL_COLD_STATE_CHAIN_MISMATCH")
    hotpath=root/"public_pons_shadow/hot_state.json"
    hot=None
    if hotpath.is_file():
        hot=load_json(hotpath)
        if (not isinstance(hot,dict) or hot.get("sha256")!=cold.hash_obj({k:v for k,v in hot.items() if k!="sha256"})
            or hot.get("authority")!="PUBLIC_HOT_SHADOW_ONLY"
            or hot.get("chain_id")!=cold.CHAIN_ID
            or hot.get("factory")!=cold.FACTORY
            or hot.get("topic0")!=cold.TOPIC0
            or not isinstance(hot.get("unobserved_gaps"),list)):
            raise ValueError("COVERAGE_HOT_STATE_UNTRUSTED")
    gaps=[]
    for i,g in enumerate((hot or {}).get("unobserved_gaps",[])):
        if (not isinstance(g,dict) or set(g)!={"from_block","to_block","reason"}
            or type(g.get("from_block")) is not int or type(g.get("to_block")) is not int
            or g["from_block"]>g["to_block"]):
            raise ValueError("COVERAGE_HOT_GAP_INVALID")
        recovered=(coldstate["bootstrap_from_block"]<=g["from_block"]
                   and g["to_block"]<=coldstate["last_scanned_block"])
        gaps.append({"gap_index":i,"from_block":g["from_block"],
                     "to_block":g["to_block"],
                     "cold_historical_range_covered":recovered,
                     "recovered_first_known":False})
    # Lazy import avoids circular dependency: historical uses our strict
    # existing duplicate-key / symlink-safe immutable JSON reader.
    try:
        from public_pons_shadow import historical_custody_v1
    except ImportError:
        import historical_custody_v1
    historical_info=historical_custody_v1.audit(root,coldstate)
    result={
        "contract":CONTRACT,"authority":"PUBLIC_READ_ONLY_RESEARCH",
        "observed_at_utc":cold.iso(clock()),
        "cold_verified_receipt_count":len(receipts),
        "cold_bounded_complete_receipts_claim_verified":True,
        "cold_verified_start_block":coldstate["bootstrap_from_block"],
        "cold_verified_end_block":coldstate["last_scanned_block"],
        "cold_verified_event_row_count":events_total,
        "cold_final_state_hash":coldstate["sha256"],
        "hot_final_state_hash":hot.get("sha256") if hot else None,
        "hot_gap_count":len(gaps),
        "hot_gaps_cold_historical_range_covered":sum(x["cold_historical_range_covered"] for x in gaps),
        "hot_gaps_cold_historical_range_uncovered":sum(not x["cold_historical_range_covered"] for x in gaps),
        "hot_gap_evidence":gaps,
        "status":"COLD_CONTIGUOUS_RECEIPT_AND_EVENT_CHAIN_VALIDATED",
        "source_first_known_verified":False,
        "hot_real_time_coverage_recovered":False,
        "canonical_private_source_admitted":False,
        "economic_edge_verified":False,
        "portfolio_action":False,
        "alert_authority":False,
        **historical_info,
    }
    return cold.signed(result)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--repo-root",type=Path,default=Path("."))
    args=ap.parse_args()
    print(json.dumps(audited(args.repo_root.resolve()),sort_keys=True))


if __name__=="__main__":
    main()
