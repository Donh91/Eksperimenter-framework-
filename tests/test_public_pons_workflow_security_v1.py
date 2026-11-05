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
        self.assertIn("python -m unittest tests.test_public_pons_shadow tests.test_public_pons_hot tests.test_public_pons_forward_outcome tests.test_public_pons_forward_gate tests.test_public_pons_native_curve_diagnostic_v1 tests.test_public_pons_workflow_security_v1 -v",raw)
        self.assertIn("contents: read",raw)

    def test_public_half_hour_hot_does_not_duplicate_private_or_cold_source(self):
        raw=WORKFLOW.read_text()
        self.assertIn("- cron: '27 * * * *'", raw)
        self.assertIn("- cron: '57 * * * *'", raw)
        self.assertIn("PUBLIC_PONS_SCHEDULE_SLOT: ${{ github.event.schedule }}", raw)
        self.assertIn('"$PUBLIC_PONS_SCHEDULE_SLOT" == "27 * * * *"', raw)
        self.assertIn("PUBLIC_PONS_COLD_INTENTIONALLY_SKIPPED_HOT_ONLY_SLOT", raw)
        self.assertEqual(raw.count("python public_pons_shadow/hot_collector.py --repo-root ."), 1)
        self.assertEqual(raw.count("python public_pons_shadow/collector.py --repo-root ."), 1)
        self.assertNotIn("Donh91/secrets", raw)
        self.assertNotIn("OPENAI_API_KEY", raw)

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
        self.assertIn("python -B public_pons_shadow/publish_gate.py",publish)
        self.assertIn("PYTHONDONTWRITEBYTECODE: '1'",publish)
        self.assertIn("UNEXPECTED_PUBLIC_PONS_WRITE_PATH",publish)

    def test_artifact_transfer_uses_verified_immutable_action_commits(self):
        raw=WORKFLOW.read_text()
        self.assertIn("actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",raw)
        self.assertIn("actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093",raw)
        self.assertNotIn("uses: actions/upload-artifact@v4",raw)
        self.assertNotIn("uses: actions/download-artifact@v4",raw)

    def test_publisher_rejects_remote_cursor_change_before_push(self):
        raw=WORKFLOW.read_text()
        publisher=raw.split("\n  publish:\n",1)[1]
        self.assertIn("baseline_main=$(git rev-parse HEAD)",publisher)
        self.assertIn("git fetch origin main",publisher)
        self.assertIn('source_blob "$baseline_main" "$path"',publisher)
        self.assertIn('source_blob origin/main "$path"',publisher)
        self.assertIn("PUBLIC_PONS_CONCURRENT_CURSOR_CHANGE_NO_OVERWRITE",publisher)
        self.assertLess(publisher.index("PUBLIC_PONS_CONCURRENT_CURSOR_CHANGE_NO_OVERWRITE"),publisher.index("git push origin HEAD:main"))
        for path in ("public_pons_shadow/state.json","public_pons_shadow/hot_state.json","public_pons_shadow/forward_state.json"):
            self.assertIn(path,publisher)

    def test_no_untrusted_privileged_events(self):
        raw=WORKFLOW.read_text()
        self.assertNotIn("pull_request_target:",raw)
        self.assertNotIn("workflow_run:",raw)
        self.assertIn("public-pons-review-",raw)
        self.assertIn("experiment-execution-plane",raw)
        self.assertIn("cancel-in-progress: false",raw)
        self.assertIn("PUBLIC_PONS_COLD_DEGRADED_NO_CURSOR_ADVANCE",raw)
        self.assertIn("PUBLIC_PONS_HOT_DEGRADED_NO_CURSOR_ADVANCE",raw)
        self.assertIn("PUBLIC_FORWARD_OUTCOME_DEGRADED_NO_STATE_ADVANCE",raw)
        self.assertIn("public_pons_shadow/forward_outcome.py --repo-root .",raw)
        self.assertIn("forward/runs/",raw)
        self.assertNotIn("Donh91/secrets",raw)
        self.assertNotIn("OPENAI_API_KEY",raw)
        self.assertEqual(raw.count("timeout --signal=TERM --kill-after=5s 240s env GITHUB_CHECKOUT_SHA"),2)
        self.assertNotIn("live-source-canary:",raw)
        self.assertIn("rm -f /tmp/pons_cold.json",raw)
        self.assertIn("rm -f /tmp/pons_hot.json",raw)

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

    def test_hot_and_cold_publish_together_without_cursor_cross_admission(self):
        from public_pons_shadow import collector as cold
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            root=base/"source"
            root.mkdir()
            hot_out=hot.hot_capture(root,cold.Client(FakeRPC(head=100)),
                                    "100",SHA,clock=lambda:NOW,window=12)
            cold_out=cold.collect(root,cold.Client(FakeRPC(head=100)),
                                  "100",SHA,clock=lambda:NOW,bootstrap=12,max_scan=12,chunk=3)
            stage=base/"stage"
            for out, state in ((hot_out,hot.STATE),(cold_out,cold.STATE)):
                receipt=Path(out["receipt"])
                events=receipt.with_name(receipt.name.replace("_receipt.json","_events.json"))
                for rel in (state,receipt,events):
                    target=stage/rel
                    target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(root/rel,target)
            publisher=base/"publisher"
            (publisher/"public_pons_shadow").mkdir(parents=True)
            updates=self.validate(stage,publisher)
            self.assertEqual(len(updates),6)
            self.assertEqual(len(set(map(str,updates))),6)
            for key in ("private_source_admitted","provider_first_known_verified"):
                for out in (hot_out,cold_out):
                    self.assertFalse(json.loads((root/out["receipt"]).read_text())[key])

    def test_cold_only_stage_is_accepted_without_hot_assumptions(self):
        from public_pons_shadow import collector as cold
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            root=base/"source"
            root.mkdir()
            out=cold.collect(root,cold.Client(FakeRPC(head=100)),
                             "100",SHA,clock=lambda:NOW,bootstrap=12,max_scan=12,chunk=3)
            receipt=Path(out["receipt"])
            events=receipt.with_name(receipt.name.replace("_receipt.json","_events.json"))
            stage=base/"stage"
            for rel in (cold.STATE,receipt,events):
                target=stage/rel
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(root/rel,target)
            publisher=base/"publisher"
            (publisher/"public_pons_shadow").mkdir(parents=True)
            updates=self.validate(stage,publisher)
            self.assertEqual(len(updates),3)
            self.assertFalse(json.loads((stage/receipt).read_text())["private_source_admitted"])

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
