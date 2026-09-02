#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
MANIFEST_V1 = "EXPERIMENT_DISPATCH_MANIFEST_v1"
MANIFEST_V2 = "EXPERIMENT_DISPATCH_MANIFEST_v2_SCIENTIFIC_ADMISSION"
QUALIFIED = "QUALIFIED_FOR_FORWARD_TEST"

EMBEDDED_RECOMPUTE = "COMPONENT_RECOMPUTE_EMBEDDED_OBSERVATION"
STRUCTURAL_ONLY = "STRUCTURAL_ONLY_NO_COMPONENT_RESULTS"
WAITING_SCOPE = "WAITING_FOR_RECOMPUTABLE_DATA"
NO_SOURCE_REFERENCE = "NO_SOURCE_REFERENCE"


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_source(url: str | None, path: Path | None) -> dict[str, Any]:
    if path is not None:
        return json.loads(path.read_text(encoding="utf-8"))
    if not url:
        raise ValueError("manifest_source_required")
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.loads(response.read())


def fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.loads(response.read())


def recompute_row(row: dict[str, Any]) -> bool | None:
    operator = row.get("operator")
    latest = row.get("latest")
    previous = row.get("previous")
    delta = row.get("delta_pct")
    threshold = row.get("threshold")
    if latest is None or (str(operator).startswith("DELTA_") and delta is None):
        return None
    if operator == "AVAILABLE": return True
    if operator == "GT": return isinstance(latest, (int, float)) and isinstance(threshold, (int, float)) and float(latest) > float(threshold)
    if operator == "LT": return isinstance(latest, (int, float)) and isinstance(threshold, (int, float)) and float(latest) < float(threshold)
    if operator == "DELTA_PCT_GT": return isinstance(delta, (int, float)) and isinstance(threshold, (int, float)) and float(delta) > float(threshold)
    if operator == "DELTA_PCT_LT": return isinstance(delta, (int, float)) and isinstance(threshold, (int, float)) and float(delta) < float(threshold)
    if operator == "POSITIVE": return isinstance(latest, (int, float)) and float(latest) > 0
    if operator == "NEGATIVE": return isinstance(latest, (int, float)) and float(latest) < 0
    if operator == "CHANGED": return previous is not None and latest != previous
    return None


def recompute_status(request: dict[str, Any]) -> tuple[str, str, str]:
    """Recompute request status without treating structural transport as evidence.

    Component logic is recomputed from values embedded in the request. That is
    stronger than a structural receipt, but it is not independent data-source
    verification and must never be labelled as such.
    """
    observation = request.get("embedded_observation") or {}
    results = observation.get("component_results") or []
    if not results and request.get("spec", {}).get("kind") == "FORECAST_TEST":
        return (
            "REPLICATION_UNVERIFIED",
            STRUCTURAL_ONLY,
            "EMPTY_FORECAST_TEST_NO_RECOMPUTABLE_COMPONENTS",
        )
    if not results and observation.get("evaluation_status") == "WAITING_FOR_MAPPING":
        return (
            "REPLICATED_WAITING_FOR_DATA",
            WAITING_SCOPE,
            "WAITING_FOR_MAPPING",
        )
    recomputed = [recompute_row(row) for row in results]
    if any(value is None for value in recomputed):
        return (
            "REPLICATED_WAITING_FOR_DATA",
            WAITING_SCOPE,
            "COMPONENT_DATA_NOT_RECOMPUTABLE",
        )
    if recomputed and all(value is True for value in recomputed):
        return (
            "REPLICATED_FIRED",
            EMBEDDED_RECOMPUTE,
            "EMBEDDED_RECOMPUTE_MATCH_CANDIDATE",
        )
    return (
        "REPLICATED_NOT_FIRED",
        EMBEDDED_RECOMPUTE,
        "EMBEDDED_RECOMPUTE_MATCH_CANDIDATE",
    )


def validate_manifest_and_request(manifest: dict[str, Any], request: dict[str, Any]) -> None:
    contract = manifest.get("contract")
    if contract not in {MANIFEST_V1, MANIFEST_V2}:
        raise SystemExit("invalid_dispatch_manifest_contract")
    if contract == MANIFEST_V2:
        if manifest.get("admission_required") != QUALIFIED:
            raise SystemExit("invalid_scientific_admission_requirement")
        if request.get("scientific_admission_status") != QUALIFIED:
            raise SystemExit("request_not_scientifically_admitted")
        if not request.get("scientific_admission_sha256"):
            raise SystemExit("scientific_admission_hash_missing")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest-url")
    ap.add_argument("--manifest-file", type=Path)
    ap.add_argument("--source-root", type=Path)
    ap.add_argument("--request-root", type=Path, required=True)
    ap.add_argument("--receipt-root", type=Path, required=True)
    ap.add_argument("--manifest-output", type=Path, required=True)
    ap.add_argument("--state-output", type=Path, required=True)
    ap.add_argument("--repository", default="Donh91/Eksperimenter-framework-")
    ap.add_argument("--branch", default="main")
    args = ap.parse_args()

    manifest = load_source(args.manifest_url, args.manifest_file)
    if manifest.get("contract") not in {MANIFEST_V1, MANIFEST_V2}:
        raise SystemExit("invalid_dispatch_manifest_contract")
    if manifest.get("contract") == MANIFEST_V2 and manifest.get("admission_required") != QUALIFIED:
        raise SystemExit("invalid_scientific_admission_requirement")

    processed = mismatches = unverified = component_recomputes = 0
    for item in manifest.get("requests", []):
        if args.manifest_file is None:
            request = fetch_json(item["raw_url"])
        else:
            source_root = args.source_root or args.manifest_file.parent
            request = json.loads((source_root / item["path"]).read_text(encoding="utf-8"))
        if sha256(request) != item["sha256"]:
            raise SystemExit(f"request_hash_mismatch:{item.get('request_id')}")
        if request.get("contract") != "EXPERIMENT_REQUEST_v1":
            raise SystemExit("invalid_request_contract")
        validate_manifest_and_request(manifest, request)

        request_path = args.request_root / f"{request['request_id']}.json"
        request_path.parent.mkdir(parents=True, exist_ok=True)
        if not request_path.exists():
            request_path.write_bytes(canonical(request))

        local_status, verification_scope, verification_reason = recompute_status(request)
        source_status = str((request.get("embedded_observation") or {}).get("evaluation_status") or "UNKNOWN")
        expected_by_source = {
            "FIRED": "REPLICATED_FIRED",
            "FIRED_NO_TARGET": "REPLICATED_FIRED",
            "WAITING_FOR_DATA": "REPLICATED_WAITING_FOR_DATA",
            "WAITING_FOR_MAPPING": "REPLICATED_WAITING_FOR_DATA",
            "OBSERVED_NOT_FIRED": "REPLICATED_NOT_FIRED",
        }
        expected = expected_by_source.get(source_status)

        if local_status in {"REPLICATION_UNVERIFIED", "REPLICATED_WAITING_FOR_DATA"}:
            replication_status = local_status
        elif expected is None:
            replication_status = "REPLICATION_UNVERIFIED"
            verification_scope = NO_SOURCE_REFERENCE
            verification_reason = "UNKNOWN_SOURCE_EVALUATION_STATUS"
        elif local_status != expected:
            replication_status = "REPLICATION_MISMATCH"
            verification_reason = "EMBEDDED_RECOMPUTE_DISAGREES_WITH_SOURCE"
        else:
            replication_status = local_status
            verification_reason = "EMBEDDED_RECOMPUTE_MATCH"

        mismatches += int(replication_status == "REPLICATION_MISMATCH")
        unverified += int(replication_status == "REPLICATION_UNVERIFIED")
        component_recomputes += int(verification_scope == EMBEDDED_RECOMPUTE)

        receipt = {
            "contract": "EXPERIMENT_EXECUTION_RECEIPT_v1",
            "receipt_id": "XR-" + hashlib.sha256(f"{request['request_id']}|{replication_status}".encode()).hexdigest()[:20],
            "request_id": request["request_id"],
            "candidate_id": request["candidate_id"],
            "created_at_utc": now_iso(),
            "request_sha256": sha256(request),
            "source_manifest_contract": manifest.get("contract"),
            "scientific_admission_status": request.get("scientific_admission_status"),
            "scientific_admission_sha256": request.get("scientific_admission_sha256"),
            "source_evaluation_status": source_status,
            "replication_status": replication_status,
            "verification_scope": verification_scope,
            "verification_reason": verification_reason,
            "component_recompute_performed": verification_scope == EMBEDDED_RECOMPUTE,
            "independent_data_verification_performed": False,
            "local_frozen_forecast_id": request.get("local_frozen_forecast_id"),
            "rules": {"automatic_promotion": False, "age_based_expiry": False, "weird_or_novel_hypotheses_allowed": True, "measurement_and_falsifier_required": True},
            "authority": {"portfolio_action": False, "framework_state_change": False, "model_weight_change": False, "canonical_promotion": False},
        }
        receipt_path = args.receipt_root / f"{receipt['receipt_id']}.json"
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        if not receipt_path.exists():
            receipt_path.write_bytes(canonical(receipt))
            processed += 1

    receipts = []
    for path in sorted(args.receipt_root.glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        rel = str(path)
        receipts.append({
            "receipt_id": value["receipt_id"],
            "candidate_id": value["candidate_id"],
            "path": rel,
            "sha256": sha256(value),
            "raw_url": f"https://raw.githubusercontent.com/{args.repository}/{args.branch}/{rel}",
            "replication_status": value["replication_status"],
            "verification_scope": value.get("verification_scope", "LEGACY_UNSPECIFIED"),
        })
    output = {"contract": "EXPERIMENT_EXECUTION_RECEIPT_MANIFEST_v1", "generated_at_utc": now_iso(), "receipt_count": len(receipts), "receipts": receipts, "authority": "SHADOW_ONLY"}
    args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_output.write_bytes(canonical(output))
    state = {
        "contract": "EXPERIMENT_EXECUTION_PLANE_STATE_v1",
        "updated_at_utc": output["generated_at_utc"],
        "source_manifest_contract": manifest.get("contract"),
        "scientific_admission_enforced": manifest.get("contract") == MANIFEST_V2,
        "dispatch_request_count": len(manifest.get("requests", [])),
        "new_receipt_count": processed,
        "total_receipt_count": len(receipts),
        "replication_mismatch_count": mismatches,
        "replication_unverified_count": unverified,
        "component_recompute_count": component_recomputes,
        "independent_data_verification_count": 0,
        "status": "DEGRADED" if (mismatches or unverified) else "PASS",
        "authority": "NO_CANONICAL_OR_PORTFOLIO_AUTHORITY",
    }
    args.state_output.parent.mkdir(parents=True, exist_ok=True)
    args.state_output.write_bytes(canonical(state))
    print(json.dumps(state, sort_keys=True))


if __name__ == "__main__":
    main()
