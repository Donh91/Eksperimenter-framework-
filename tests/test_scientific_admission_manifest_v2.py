import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "experiment_execution_plane.py"
V2 = "EXPERIMENT_DISPATCH_MANIFEST_v2_SCIENTIFIC_ADMISSION"
QUALIFIED = "QUALIFIED_FOR_FORWARD_TEST"


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha(value):
    import hashlib
    return hashlib.sha256(canonical(value)).hexdigest()


def run_plane(source: Path, target: Path, request: dict):
    req_path = source / "research/experiment_lifecycle/dispatch/2026/08/23/ER-v2.json"
    req_path.parent.mkdir(parents=True, exist_ok=True)
    req_path.write_text(json.dumps(request))
    manifest = {
        "contract": V2,
        "admission_required": QUALIFIED,
        "requests": [{
            "request_id": request["request_id"],
            "candidate_id": request["candidate_id"],
            "path": str(req_path.relative_to(source)),
            "sha256": sha(request),
            "raw_url": "unused",
        }],
    }
    manifest_path = source / "research/experiment_lifecycle/LATEST_EXPERIMENT_DISPATCH_MANIFEST.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest))
    cmd = [
        sys.executable, str(SCRIPT),
        "--manifest-file", str(manifest_path),
        "--source-root", str(source),
        "--request-root", str(target / "experiment_bridge/requests"),
        "--receipt-root", str(target / "experiment_bridge/receipts"),
        "--manifest-output", str(target / "experiment_bridge/LATEST_EXECUTION_RECEIPT_MANIFEST.json"),
        "--state-output", str(target / "experiment_bridge/state.json"),
    ]
    return subprocess.run(cmd, capture_output=True, text=True)


def valid_request():
    return {
        "contract": "EXPERIMENT_REQUEST_v1",
        "request_id": "ER-v2",
        "candidate_id": "EC-v2",
        "request_type": "SENSOR_FIRE_REPLICATION",
        "spec": {"kind": "SENSOR_COMBINATION"},
        "scientific_admission_status": QUALIFIED,
        "scientific_admission_sha256": "a" * 64,
        "embedded_observation": {
            "evaluation_status": "FIRED",
            "component_results": [
                {"operator": "GT", "latest": 60, "previous": 52, "threshold": 55, "delta_pct": 15.38, "matched": True},
                {"operator": "GT", "latest": 0.0302, "previous": 0.0298, "threshold": 0.03, "delta_pct": 1.34, "matched": True},
            ],
        },
        "local_frozen_forecast_id": "EXP-FC-v2",
    }


class ScientificAdmissionManifestV2Test(unittest.TestCase):
    def test_v2_accepts_only_qualified_request_and_preserves_binding(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source"
            target = Path(td) / "target"
            result = run_plane(source, target, valid_request())
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(next((target / "experiment_bridge/receipts").glob("*.json")).read_text())
            self.assertEqual(receipt["replication_status"], "REPLICATED_FIRED")
            self.assertEqual(receipt["source_manifest_contract"], V2)
            self.assertEqual(receipt["scientific_admission_status"], QUALIFIED)
            self.assertEqual(receipt["scientific_admission_sha256"], "a" * 64)
            state = json.loads((target / "experiment_bridge/state.json").read_text())
            self.assertTrue(state["scientific_admission_enforced"])
            self.assertEqual(state["status"], "PASS")

    def test_v2_fails_closed_when_request_is_not_qualified(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source"
            target = Path(td) / "target"
            request = valid_request()
            request["scientific_admission_status"] = "KEEP_SHADOW"
            result = run_plane(source, target, request)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("request_not_scientifically_admitted", result.stderr + result.stdout)

    def test_v2_fails_closed_when_admission_hash_is_missing(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source"
            target = Path(td) / "target"
            request = valid_request()
            request.pop("scientific_admission_sha256")
            result = run_plane(source, target, request)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("scientific_admission_hash_missing", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
