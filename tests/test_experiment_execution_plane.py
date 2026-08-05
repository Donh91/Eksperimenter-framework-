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
    def test_replicates_firing(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source"
            target = Path(td) / "target"
            req_path = source / "research/experiment_lifecycle/dispatch/2026/08/05/ER-test.json"
            req_path.parent.mkdir(parents=True)
            request = {
                "contract": "EXPERIMENT_REQUEST_v1", "request_id": "ER-test", "candidate_id": "EC-test",
                "request_type": "SENSOR_FIRE_REPLICATION", "spec": {"kind": "SENSOR_COMBINATION"},
                "embedded_observation": {"evaluation_status": "FIRED", "component_results": [
                    {"operator": "GT", "latest": 60, "previous": 45, "threshold": 50, "delta_pct": 33.3, "matched": True},
                    {"operator": "DELTA_PCT_GT", "latest": 0.0295, "previous": 0.0290, "threshold": 0.5, "delta_pct": 1.72, "matched": True}
                ]},
                "local_frozen_forecast_id": "EXP-FC-test"
            }
            req_path.write_text(json.dumps(request))
            manifest = {"contract": "EXPERIMENT_DISPATCH_MANIFEST_v1", "requests": [{
                "request_id": "ER-test", "candidate_id": "EC-test", "path": str(req_path.relative_to(source)), "sha256": sha(request), "raw_url": "unused"
            }]}
            manifest_path = source / "research/experiment_lifecycle/LATEST_EXPERIMENT_DISPATCH_MANIFEST.json"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest))
            subprocess.run([
                sys.executable, str(SCRIPT), "--manifest-file", str(manifest_path), "--source-root", str(source),
                "--request-root", str(target / "experiment_bridge/requests"), "--receipt-root", str(target / "experiment_bridge/receipts"),
                "--manifest-output", str(target / "experiment_bridge/LATEST_EXECUTION_RECEIPT_MANIFEST.json"),
                "--state-output", str(target / "experiment_bridge/state.json")
            ], check=True)
            receipt = json.loads(next((target / "experiment_bridge/receipts").glob("*.json")).read_text())
            self.assertEqual(receipt["replication_status"], "REPLICATED_FIRED")
            state = json.loads((target / "experiment_bridge/state.json").read_text())
            self.assertEqual(state["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
