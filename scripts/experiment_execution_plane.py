#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

UTC = timezone.utc
MANIFEST_V1 = "EXPERIMENT_DISPATCH_MANIFEST_v1"
MANIFEST_V2 = "EXPERIMENT_DISPATCH_MANIFEST_v2_SCIENTIFIC_ADMISSION"
MANIFEST_V3 = "EXPERIMENT_DISPATCH_MANIFEST_v3_PAGED_SCIENTIFIC_ADMISSION"
PAGE_V3 = "EXPERIMENT_DISPATCH_PAGE_v3"
MAX_PAGE_BYTES = 2_000_000
MAX_PAGE_REQUESTS = 1_024
MAX_MANIFEST_PAGES = 1_024
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


def valid_digest(digest: Any) -> bool:
    return isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def expand_manifest_requests(
    manifest: dict[str, Any], *, manifest_file: Path | None, source_root: Path | None
) -> list[dict[str, Any]]:
    """Verify complete v3 pages BEFORE request or receipt state is mutated.

    V1/v2 remain unchanged and are deliberately not reinterpreted as v3 pages.
    """
    contract = manifest.get("contract")
    if contract in {MANIFEST_V1, MANIFEST_V2}:
        rows = manifest.get("requests", [])
        if not isinstance(rows, list):
            raise SystemExit("legacy_manifest_requests_invalid")
        return rows
    if contract != MANIFEST_V3:
        raise SystemExit("invalid_dispatch_manifest_contract")
    if manifest.get("admission_required") != QUALIFIED:
        raise SystemExit("invalid_scientific_admission_requirement")
    source_repo = manifest.get("source_repository")
    source_branch = manifest.get("source_branch")
    if source_repo != "Donh91/Investering-Framework-Archive-v1" or source_branch != "main":
        raise SystemExit("v3_untrusted_source_identity")
    pages = manifest.get("pages")
    count = manifest.get("request_count")
    if (not isinstance(pages, list) or len(pages) > MAX_MANIFEST_PAGES
            or type(count) is not int or count < 0 or count > MAX_MANIFEST_PAGES * MAX_PAGE_REQUESTS
            or (count > 0 and not pages) or (count == 0 and pages)):
        raise SystemExit("v3_manifest_index_invalid")
    combined: list[dict[str, Any]] = []
    previous_id = ""
    paths: set[str] = set()
    for sequence, item in enumerate(pages):
        if not isinstance(item, dict):
            raise SystemExit("v3_page_metadata_invalid")
        rel = item.get("path")
        declared = item.get("sha256")
        expected_count = item.get("request_count")
        if (not isinstance(rel, str) or not rel.startswith("research/experiment_lifecycle/dispatch_manifest_pages/")
                or any(part in {"", ".", ".."} for part in rel.split("/"))
                or PurePosixPath(rel).is_absolute() or rel in paths
                or not valid_digest(declared)
                or type(expected_count) is not int or not 1 <= expected_count <= MAX_PAGE_REQUESTS
                or item.get("sequence") != sequence):
            raise SystemExit("v3_page_metadata_invalid")
        paths.add(rel)
        url = f"https://raw.githubusercontent.com/{source_repo}/{source_branch}/{rel}"
        if item.get("raw_url") != url:
            raise SystemExit("v3_page_url_mismatch")
        if manifest_file is None:
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read(MAX_PAGE_BYTES + 1)
        else:
            root = source_root or manifest_file.parent
            target = (root / rel).resolve()
            if not target.is_relative_to(root.resolve()):
                raise SystemExit("v3_page_path_escape")
            data = target.read_bytes()
        if len(data) > MAX_PAGE_BYTES or hashlib.sha256(data).hexdigest() != declared:
            raise SystemExit("v3_page_digest_or_size_mismatch")
        try:
            page = json.loads(data)
        except (ValueError, UnicodeError) as exc:
            raise SystemExit("v3_page_invalid_json") from exc
        if (not isinstance(page, dict) or page.get("contract") != PAGE_V3
                or page.get("sequence") != sequence
                or page.get("request_count") != expected_count
                or not isinstance(page.get("requests"), list)
                or len(page["requests"]) != expected_count
                or canonical(page) != data):
            raise SystemExit("v3_page_contract_or_count_mismatch")
        first_id = None
        for entry in page["requests"]:
            if not isinstance(entry, dict):
                raise SystemExit("v3_request_index_invalid")
            request_id = entry.get("request_id")
            if (not isinstance(request_id, str) or not request_id.startswith("ER-")
                    or len(request_id) > 128
                    or any(not (ch.isascii() and (ch.isalnum() or ch in "_-")) for ch in request_id)
                    or not valid_digest(entry.get("sha256"))
                    or not isinstance(entry.get("candidate_id"), str)
                    or not isinstance(entry.get("path"), str)
                    or not isinstance(entry.get("raw_url"), str)):
                raise SystemExit("v3_request_index_invalid")
            if request_id <= previous_id:
                raise SystemExit("v3_duplicate_or_unsorted_request_id")
            if first_id is None:
                first_id = request_id
            previous_id = request_id
        if item.get("first_request_id") != first_id or item.get("last_request_id") != previous_id:
            raise SystemExit("v3_page_bounds_mismatch")
        combined.extend(page["requests"])
    if len(combined) != count:
        raise SystemExit("v3_manifest_total_count_mismatch")
    return combined


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
    if contract not in {MANIFEST_V1, MANIFEST_V2, MANIFEST_V3}:
        raise SystemExit("invalid_dispatch_manifest_contract")
    if contract in {MANIFEST_V2, MANIFEST_V3}:
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
    if manifest.get("contract") not in {MANIFEST_V1, MANIFEST_V2, MANIFEST_V3}:
        raise SystemExit("invalid_dispatch_manifest_contract")
    if manifest.get("contract") in {MANIFEST_V2, MANIFEST_V3} and manifest.get("admission_required") != QUALIFIED:
        raise SystemExit("invalid_scientific_admission_requirement")
    # All pages, hashes, counts and deterministic order must be accepted before
    # writing even the first local request or receipt.
    source_requests = expand_manifest_requests(
        manifest, manifest_file=args.manifest_file, source_root=args.source_root
    )

    processed = mismatches = unverified = component_recomputes = 0
    cached_requests = fetched_requests = 0
    for item in source_requests:
        request_id = item.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise SystemExit("manifest_request_id_missing")
        request_path = args.request_root / f"{request_id}.json"
        if request_path.exists():
            request = json.loads(request_path.read_text(encoding="utf-8"))
            cached_requests += 1
        elif args.manifest_file is None:
            request = fetch_json(item["raw_url"])
            fetched_requests += 1
        else:
            source_root = args.source_root or args.manifest_file.parent
            request = json.loads((source_root / item["path"]).read_text(encoding="utf-8"))
            fetched_requests += 1
        if sha256(request) != item["sha256"]:
            raise SystemExit(f"request_hash_mismatch:{item.get('request_id')}")
        if request.get("request_id") != request_id:
            raise SystemExit(f"request_identity_mismatch:{request_id}")
        if request.get("contract") != "EXPERIMENT_REQUEST_v1":
            raise SystemExit("invalid_request_contract")
        validate_manifest_and_request(manifest, request)

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
        "scientific_admission_enforced": manifest.get("contract") in {MANIFEST_V2, MANIFEST_V3},
        "dispatch_request_count": len(source_requests),
        "cached_request_count": cached_requests,
        "fetched_request_count": fetched_requests,
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
