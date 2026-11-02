"""Offline regression for public HOT/COLD source and separate credential boundary."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from public_pons_shadow import publish_gate
from public_pons_shadow import hot_collector as hot
from tests.test_public_pons_hot import FakeRPC, NOW, SHA

WORKFLOW=Path(__file__).resolve().parents[1]/".github/workflows/public-pons-shadow.yml"

class TestPublicPonsSecurity(unittest.TestCase):
    def test_pr_runs_real_executable_checks(self):
        raw=WORKFLOW.read_text()
        self.assertIn("pull_request:",raw)
        self.assertIn("if: github.event_name == 'pull_request'",raw)
        self.assertIn("python -m unittest tests.test_public_pons_shadow tests.test_public_pons_hot tests.test_public_pons_workflow_security_v1 -v",raw)
        self.assertIn("contents: read",raw)

    def test_only_publish_job_has_write_token(self):
        raw=WORKFLOW.read_text()
        self.assertEqual(raw.count("contents: write"),1)
        collect=raw.split("\n  collect:\n",1)[1].split("\n  publish:\n",1)[0]
        publish=raw.split("\n  publish:\n",1)[1]
        self.assertIn("persist-credentials: false",collect)
        self.assertIn("contents: read",collect)
        self.assertNotIn("contents: write",collect)
        self.assertIn("contents: write",publish)
        self.assertNotIn("Capture public source",publish)
        self.assertNotIn("hot_collector.py --repo-root",publish)
        self.assertNotIn("collector.py --repo-root",publish)
        self.assertIn("python public_pons_shadow/publish_gate.py",publish)

    def test_no_untrusted_privileged_events(self):
        raw=WORKFLOW.read_text()
        self.assertNotIn("pull_request_target:",raw)
        self.assertNotIn("workflow_run:",raw)
        self.assertIn("group: experiment-execution-plane",raw)
        self.assertIn("cancel-in-progress: false",raw)
        self.assertIn("PUBLIC_PONS_COLD_DEGRADED_NO_CURSOR_ADVANCE",raw)
        self.assertIn("PUBLIC_PONS_HOT_DEGRADED_NO_CURSOR_ADVANCE",raw)

    def stage_hot(self,base):
        root=base/"source"
        root.mkdir()
        result=hot.hot_capture(root,hot.cold.Client(FakeRPC(head=100)),
                               "100",SHA,clock=lambda:NOW,window=12)
        receipt=root/result["receipt"]
        stage=base/"stage"
        for rel in [hot.STATE,receipt.relative_to(root),
                    receipt.with_name(receipt.name.replace("_receipt.json","_events.json")).relative_to(root)]:
            target=stage/rel
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(root/rel,target)
        publisher=base/"publisher"
        (publisher/"public_pons_shadow").mkdir(parents=True)
        return stage,publisher,receipt.relative_to(root)

    def validate(self,stage,publisher):
        with patch.object(publish_gate.subprocess,"run"), patch.object(
            publish_gate.subprocess,"check_output",return_value=SHA+"\n"
        ):
            return publish_gate.validate(stage,publisher,"100",SHA)

    def test_genuine_hot_stage_is_unadmitted(self):
        with tempfile.TemporaryDirectory() as d:
            stage,publisher,receipt=self.stage_hot(Path(d))
            updates=self.validate(stage,publisher)
            self.assertEqual(len(updates),3)
            r=json.loads((stage/receipt).read_text())
            self.assertIs(r["private_source_admitted"],False)

    def test_tampered_event_does_not_pass_self_hash(self):
        with tempfile.TemporaryDirectory() as d:
            stage,publisher,receipt=self.stage_hot(Path(d))
            e=stage/receipt.with_name(receipt.name.replace("_receipt.json","_events.json"))
            e.write_text("[]\n")
            with self.assertRaisesRegex(ValueError,"PONS_RECEIPT_INVALID"):
                self.validate(stage,publisher)

    def test_unexpected_stage_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            stage,publisher,receipt=self.stage_hot(Path(d))
            (stage/"extra.txt").write_text("unexpected")
            with self.assertRaisesRegex(ValueError,"PONS_EXTRA_OR_MISSING_ARTIFACT"):
                self.validate(stage,publisher)

    def test_caller_supplied_run_id_cannot_be_swapped(self):
        with tempfile.TemporaryDirectory() as d:
            stage,publisher,receipt=self.stage_hot(Path(d))
            with patch.object(publish_gate.subprocess,"run"),patch.object(
                publish_gate.subprocess,"check_output",return_value=SHA+"\n"
            ):
                with self.assertRaisesRegex(ValueError,"PONS_(STATE_INVALID|RUN_RECEIPT_SET_INVALID)"):
                    publish_gate.validate(stage,publisher,"999",SHA)

    def test_symlinked_staged_data_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            stage,publisher,receipt=self.stage_hot(Path(d))
            rp=stage/receipt
            rp.rename(stage/"outside.txt")
            rp.symlink_to(stage/"outside.txt")
            with self.assertRaises(ValueError):
                self.validate(stage,publisher)

if __name__=="__main__":
    unittest.main()
