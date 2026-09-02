import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "experiment_execution_plane.py"


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha(value):
    import hashlib
    return hashlib.sha256(canonical(value)).hexdigest()


class ExecutionPlaneTest(unittest.TestCase):
    def run_case(self, request):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source"
            target = Path(td) / "target"
            req_path = source / f"research/experiment_lifecycle/dispatch/2026/09/02/{request['request_id']}.json"
            req_path.parent.mkdir(parents=True)
            req_path.write_text(json.dumps(request))
            manifest = {"contract": "EXPERIMENT_DISPATCH_MANIFEST_v1", "requests": [{
                "request_id": request["request_id"],
                "candidate_id": request["candidate_id"],
                "path": str(req_path.relative_to(source)),
                "sha256": sha(request),
                "raw_url": "unused",
            }]}
            manifest_path = source / "research/experiment_lifecycle/LATEST_EXPERIMENT_DISPATCH_MANIFEST.json"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest))
            subprocess.run([
                sys.executable, str(SCRIPT),
                "--manifest-file", str(manifest_path),
                "--source-root", str(source),
                "--request-root", str(target / "experiment_bridge/requests"),
                "--receipt-root", str(target / "experiment_bridge/receipts"),
                "--manifest-output", str(target / "experiment_bridge/LATEST_EXECUTION_RECEIPT_MANIFEST.json"),
                "--state-output", str(target / "experiment_bridge/state.json"),
            ], check=True)
            receipt = json.loads(next((target / "experiment_bridge/receipts").glob("*.json")).read_text())
            state = json.loads((target / "experiment_bridge/state.json").read_text())
            return receipt, state

    @staticmethod
    def request(evaluation_status, component_results, *, request_id="ER-test", kind="SENSOR_COMBINATION"):
        return {
            "contract": "EXPERIMENT_REQUEST_v1",
            "request_id": request_id,
            "candidate_id": "EC-test",
            "request_type": "SENSOR_FIRE_REPLICATION",
            "spec": {"kind": kind},
            "embedded_observation": {
                "evaluation_status": evaluation_status,
                "component_results": component_results,
            },
            "local_frozen_forecast_id": "EXP-FC-test",
        }

    def test_replicates_firing_with_embedded_component_recompute(self):
        request = self.request("FIRED", [
            {"operator": "GT", "latest": 60, "previous": 45, "threshold": 50, "delta_pct": 33.3, "matched": True},
            {"operator": "DELTA_PCT_GT", "latest": 0.0295, "previous": 0.0290, "threshold": 0.5, "delta_pct": 1.72, "matched": True},
        ])
        receipt, state = self.run_case(request)
        self.assertEqual(receipt["replication_status"], "REPLICATED_FIRED")
        self.assertEqual(receipt["verification_scope"], "COMPONENT_RECOMPUTE_EMBEDDED_OBSERVATION")
        self.assertTrue(receipt["component_recompute_performed"])
        self.assertFalse(receipt["independent_data_verification_performed"])
        self.assertEqual(state["component_recompute_count"], 1)
        self.assertEqual(state["independent_data_verification_count"], 0)
        self.assertEqual(state["status"], "PASS")

    def test_replicates_not_fired(self):
        request = self.request("OBSERVED_NOT_FIRED", [
            {"operator": "GT", "latest": 40, "previous": 45, "threshold": 50, "delta_pct": -11.1, "matched": False},
        ], request_id="ER-not-fired")
        receipt, state = self.run_case(request)
        self.assertEqual(receipt["replication_status"], "REPLICATED_NOT_FIRED")
        self.assertEqual(receipt["verification_scope"], "COMPONENT_RECOMPUTE_EMBEDDED_OBSERVATION")
        self.assertTrue(receipt["component_recompute_performed"])
        self.assertFalse(receipt["independent_data_verification_performed"])
        self.assertEqual(state["status"], "PASS")

    def test_embedded_recompute_disagreement_is_mismatch(self):
        request = self.request("FIRED", [
            {"operator": "GT", "latest": 40, "previous": 45, "threshold": 50, "delta_pct": -11.1, "matched": False},
        ], request_id="ER-mismatch")
        receipt, state = self.run_case(request)
        self.assertEqual(receipt["replication_status"], "REPLICATION_MISMATCH")
        self.assertEqual(receipt["verification_reason"], "EMBEDDED_RECOMPUTE_DISAGREES_WITH_SOURCE")
        self.assertEqual(state["replication_mismatch_count"], 1)
        self.assertEqual(state["status"], "DEGRADED")

    def test_empty_forecast_test_is_structural_only_not_replication(self):
        request = self.request("FIRED", [], request_id="ER-empty-forecast", kind="FORECAST_TEST")
        receipt, state = self.run_case(request)
        self.assertEqual(receipt["replication_status"], "REPLICATION_UNVERIFIED")
        self.assertEqual(receipt["verification_scope"], "STRUCTURAL_ONLY_NO_COMPONENT_RESULTS")
        self.assertEqual(receipt["verification_reason"], "EMPTY_FORECAST_TEST_NO_RECOMPUTABLE_COMPONENTS")
        self.assertFalse(receipt["component_recompute_performed"])
        self.assertFalse(receipt["independent_data_verification_performed"])
        self.assertEqual(state["replication_unverified_count"], 1)
        self.assertEqual(state["status"], "DEGRADED")

    def test_unknown_source_status_fails_closed(self):
        request = self.request("NEW_UNKNOWN_STATUS", [
            {"operator": "GT", "latest": 60, "previous": 45, "threshold": 50, "delta_pct": 33.3, "matched": True},
        ], request_id="ER-unknown-source")
        receipt, state = self.run_case(request)
        self.assertEqual(receipt["replication_status"], "REPLICATION_UNVERIFIED")
        self.assertEqual(receipt["verification_scope"], "NO_SOURCE_REFERENCE")
        self.assertEqual(receipt["verification_reason"], "UNKNOWN_SOURCE_EVALUATION_STATUS")
        self.assertEqual(state["replication_unverified_count"], 1)
        self.assertEqual(state["status"], "DEGRADED")


if __name__ == "__main__":
    unittest.main()
