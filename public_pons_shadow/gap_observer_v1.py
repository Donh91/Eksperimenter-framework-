#!/usr/bin/env python3
"""Bounded offline public HOT gap diagnostics from the existing Pons producer.

NO RPC, network, new schedule, source mutation, private source admission, or
historical first-known recovery. Outputs only a compact log receipt, never
exports the original market events or public/private wallets.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import re

from public_pons_shadow import collector as cold, hot_collector as hot

CONTRACT = "PUBLIC_PONS_GAP_INTELLIGENCE_READ_ONLY_V1"
SCHEDULES = {
    "27 * * * *": "COLD_AND_HOT",
    "57 * * * *": "HOT_AND_DUE_OUTCOME",
    "2,7,12,17,22,32,37,42,47,52 * * * *": "HOT_ONLY",
}
RECEIPT_RE = re.compile(
    r"public_pons_shadow/hot/runs/\d{4}/\d{2}/\d{2}/"
    r"\d{8}T\d{6}Z_([0-9]{1,20})_receipt\.json\Z"
)
MAX_INPUT_BYTES = 2_000_000
MAX_GAP_RANGES = 1000


def deny(reason: str) -> None:
    raise ValueError("PUBLIC_PONS_GAP_OBSERVER_DENIED:" + reason)


def load_exact(path: Path) -> object:
    if path.is_symlink() or not path.is_file():
        deny("missing_or_symlinked_input")
    size = path.stat().st_size
    if not 0 < size <= MAX_INPUT_BYTES:
        deny("oversized_or_empty_input")
    def no_duplicate(pairs):
        d = {}
        for k, v in pairs:
            if k in d:
                deny("duplicate_json_key")
            d[k] = v
        return d
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=no_duplicate,
        parse_constant=lambda _: deny("nonfinite_json"),
    )


def utc(s: str) -> datetime:
    if not isinstance(s, str) or not s.endswith("Z"):
        deny("clock_not_explicit_utc")
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        deny("invalid_clock")
    if d.utcoffset() is None or d.utcoffset().total_seconds() != 0:
        deny("clock_not_utc")
    return d


def state_check(root: Path) -> dict:
    state = load_exact(root / hot.STATE)
    if (not isinstance(state, dict)
        or state.get("sha256") != cold.hash_obj({k:v for k,v in state.items() if k != "sha256"})
        or state.get("contract") != hot.CONTRACT
        or state.get("chain_id") != cold.CHAIN_ID
        or state.get("factory") != cold.FACTORY
        or state.get("topic0") != cold.TOPIC0
        or state.get("authority") != "PUBLIC_HOT_SHADOW_ONLY"
        or type(state.get("last_observed_block")) is not int
        or state["last_observed_block"] < 0
        or not isinstance(state.get("unobserved_gaps"), list)):
        deny("source_state_not_bound")
    gaps = state["unobserved_gaps"]
    if len(gaps) > MAX_GAP_RANGES:
        deny("source_gap_cap_exceeded")
    for gap in gaps:
        if (not isinstance(gap, dict)
            or set(gap) != {"from_block", "to_block", "reason"}
            or type(gap["from_block"]) is not int
            or type(gap["to_block"]) is not int
            or gap["from_block"] < 0
            or gap["to_block"] < gap["from_block"]
            or gap["reason"] != "HOT_MISSED_SCHEDULE_UNKNOWN_COLD_COVERAGE"):
            deny("unverified_gap_shape")
    return state


def diagnose(root: Path, result_file: Path, *, run_id: str,
             checkout_sha: str, event: str, schedule: str,
             as_of_utc: str) -> dict:
    if not re.fullmatch(r"[0-9]{1,20}", run_id or ""):
        deny("invalid_run_id")
    if not re.fullmatch(r"[a-f0-9]{40}", checkout_sha or ""):
        deny("invalid_checkout_sha")
    if event not in ("schedule", "push", "workflow_dispatch"):
        deny("untrusted_event")
    if event == "schedule" and schedule not in SCHEDULES:
        deny("unrecognized_schedule")
    if event != "schedule" and schedule != "":
        deny("non_schedule_must_not_claim_a_slot")
    now = utc(as_of_utc)
    summary = {
        "contract": CONTRACT,
        "authority": "PUBLIC_RESEARCH_DIAGNOSTIC_ONLY",
        "run_id": run_id,
        "source_checkout_sha": checkout_sha,
        "event": event,
        "schedule_group": SCHEDULES.get(schedule, "NOT_SCHEDULED"),
        "observed_at_utc": cold.iso(now),
        # github.event.schedule is the CRON expression, NOT the actual intended
        # UTC trigger. No exact delivery-lag estimate from this run alone.
        "exact_scheduled_slot_utc": None,
        "github_schedule_delivery_proven": False,
        "status": "UNKNOWN_CAPTURE_NOT_PRESENT",
        "source_last_observed_block": None,
        "source_gap_range_count": None,
        "new_missed_hot_blocks": None,
        "source_scan_age_seconds": None,
        "original_receipt_sha256": None,
        "source_state_sha256": None,
        "first_known_verified": False,
        "private_source_admitted": False,
        "public_site_admitted": False,
        "alert_authority": False,
    }
    if not result_file.exists():
        # Existing producer logs capture failures separately. A missing file
        # never means no launches or a negative cohort.
        return summary
    result = load_exact(result_file)
    if not isinstance(result, dict):
        deny("result_not_object")
    status = result.get("status")
    if status not in ("NO_NEW_HOT_SAFE_BLOCK", "PUBLIC_HOT_CAPTURED_UNPUBLISHED"):
        deny("unknown_source_result")
    state = state_check(root)
    summary["source_state_sha256"] = state["sha256"]
    summary["source_gap_range_count"] = len(state["unobserved_gaps"])
    summary["source_last_observed_block"] = state["last_observed_block"]
    if status == "NO_NEW_HOT_SAFE_BLOCK":
        if (set(result) != {"status", "safe_head"}
            or type(result["safe_head"]) is not int
            or result["safe_head"] != state["last_observed_block"]):
            deny("no_new_head_unbound")
        summary["status"] = "NO_NEW_SAFE_BLOCK_NOT_ZERO_LAUNCHES"
        return summary

    if (set(result) != {"status", "from_block", "to_block", "events",
                        "missed_hot_range_count", "receipt"}
        or any(type(result[k]) is not int for k in ("from_block", "to_block", "events", "missed_hot_range_count"))
        or result["events"] < 0 or result["events"] > cold.MAX_EVENTS):
        deny("unexpected_capture_fields")
    rel = result["receipt"]
    if (not isinstance(rel, str) or not RECEIPT_RE.fullmatch(rel)
        or RECEIPT_RE.fullmatch(rel).group(1) != run_id):
        deny("untrusted_receipt_reference")
    rel_path = PurePosixPath(rel)
    path = root.joinpath(*rel_path.parts)
    if path.resolve().is_relative_to(root.resolve()) is False or any(
        p.is_symlink() for p in (path, *path.parents) if p != root
    ):
        deny("unsafe_source_receipt_path")
    receipt = load_exact(path)
    events_path = path.with_name(path.name.replace("_receipt.json", "_events.json"))
    events = load_exact(events_path)
    if not isinstance(receipt, dict) or not isinstance(events, list):
        deny("source_receipt_events_type")
    if (receipt.get("sha256") != cold.hash_obj({k:v for k,v in receipt.items() if k != "sha256"})
        or receipt.get("sha256") != state.get("last_receipt_sha256")
        or receipt.get("contract") != hot.CONTRACT
        or receipt.get("authority") != "PUBLIC_HOT_SHADOW_ONLY"
        or receipt.get("chain_id") != cold.CHAIN_ID
        or receipt.get("factory") != cold.FACTORY
        or receipt.get("topic0") != cold.TOPIC0
        or receipt.get("github_run_id") != run_id
        or receipt.get("github_checkout_sha") != checkout_sha
        or state.get("last_run_id") != run_id
        or receipt.get("to_block") != state["last_observed_block"]
        or receipt.get("to_block") != result["to_block"]
        or receipt.get("from_block") != result["from_block"]
        or receipt.get("event_count") != len(events)
        or receipt.get("event_count") != result["events"]
        or receipt.get("events_sha256") != cold.hash_obj(events)
        or receipt.get("complete_scanned_window") is not True
        or receipt.get("history_complete") is not False
        or receipt.get("private_source_admitted") is not False
        or receipt.get("canonical_census_complete") is not False
        or receipt.get("provider_first_known_verified") is not False
        or receipt.get("alert_authority") is not False
        or receipt.get("portfolio_action") is not False):
        deny("source_receipt_unbound_or_unauthorized")
    ranges = receipt.get("missed_hot_ranges")
    if (not isinstance(ranges, list) or ranges != state["unobserved_gaps"]
        or receipt.get("missed_hot_range_count") != len(ranges)
        or result["missed_hot_range_count"] != len(ranges)):
        deny("source_gap_chain_mismatch")
    previous = receipt.get("previous_hot_block")
    if previous is not None and (type(previous) is not int or previous < 0):
        deny("source_previous_block_invalid")
    new_gap_blocks = max(0, result["from_block"] - previous - 1) if previous is not None else None
    if previous is not None:
        expect = ({"from_block": previous+1,
                   "to_block":result["from_block"]-1,
                   "reason":"HOT_MISSED_SCHEDULE_UNKNOWN_COLD_COVERAGE"} if new_gap_blocks else None)
        if expect is not None and expect not in ranges:
            deny("new_gap_not_in_original_ledger")
    finished = utc(receipt.get("scan_finished_at_utc"))
    if finished > now:
        deny("future_dated_source_capture")
    age = int((now - finished).total_seconds())
    summary.update(
        status="BOUND_SOURCE_CAPTURE_NOT_LIVE_ADMISSION",
        source_scan_age_seconds=age,
        new_missed_hot_blocks=new_gap_blocks,
        original_receipt_sha256=receipt["sha256"],
    )
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", type=Path, default=Path("."))
    ap.add_argument("--result-file", type=Path, required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--checkout-sha", required=True)
    ap.add_argument("--event", required=True)
    ap.add_argument("--schedule", default="")
    args = ap.parse_args()
    report = diagnose(
        args.repo_root.resolve(), args.result_file,
        run_id=args.run_id, checkout_sha=args.checkout_sha,
        event=args.event, schedule=args.schedule,
        as_of_utc=cold.iso(datetime.now(timezone.utc)),
    )
    print("PUBLIC_PONS_GAP_INTELLIGENCE " + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
