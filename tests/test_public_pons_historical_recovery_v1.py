"""Offline isolation and historical source-bridge proof, no private import."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from public_pons_shadow import collector as cold
from public_pons_shadow import historical_recovery_v1 as hist
from public_pons_shadow import publish_gate
from tests.test_public_pons_shadow import RPC, NOW, SHA


class PublicHistoricalGapTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)/"collector"
        self.publish=Path(temp.name)/"published"
        self.stage=Path(temp.name)/"stage"
        self.root.mkdir()
        self.publish.mkdir()
        # Ordinary COLD bootstraps from 79..98. Historical research scans
        # its missing predecessor [20..78] with separate isolated state.
        cold.collect(self.root,cold.Client(RPC(head=100)),"100",SHA,
                     clock=lambda:NOW,bootstrap=20,max_scan=20,chunk=3)
        source=self.root/cold.STATE
        target=self.publish/cold.STATE
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)

    def get(self,run="101",rpc=None):
        with patch.object(hist,"START_BLOCK",20):
            return hist.capture(self.root,cold.Client(rpc or RPC(head=100)),
                                run,SHA,clock=lambda:NOW)

    def stage_files(self,out):
        self.stage.mkdir(exist_ok=True)
        for rel in [hist.STATE,Path(out["receipt"]),
                    Path(out["receipt"].replace("_receipt.json","_events.json"))]:
            dest=self.stage/rel
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(self.root/rel,dest)

    def admit(self,run,old_override=None):
        with patch.object(hist,"START_BLOCK",20),patch.object(
            publish_gate.subprocess,"run"),patch.object(
                publish_gate.subprocess,"check_output",return_value=SHA+"\n"):
            return publish_gate.validate(self.stage,self.publish,run,SHA)

    def commit_approved(self,allowed):
        for rel in allowed:
            dest=self.publish/rel
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(self.stage/rel,dest)
        shutil.rmtree(self.stage)

    def test_historical_bootstrap_exact_bound_and_does_not_touch_cold_or_hot(self):
        original=(self.root/cold.STATE).read_bytes()
        result=self.get()
        self.assertEqual(result["status"],"PUBLIC_HISTORICAL_CAPTURED_UNPUBLISHED")
        self.assertEqual((result["from_block"],result["to_block"]),(20,78))
        self.assertEqual(result["remaining_to_public_cold_bootstrap"],0)
        self.assertFalse(result["private_canonical_source_admitted"])
        receipt=json.loads((self.root/result["receipt"]).read_text())
        self.assertIs(receipt["bootstrap_not_full_history"],True)
        self.assertIsNone(receipt["previous_cursor"])
        self.assertFalse(receipt["provider_first_known_verified"])
        self.assertFalse(receipt["private_source_admitted"])
        self.assertEqual((self.root/cold.STATE).read_bytes(),original)
        self.stage_files(result)
        allowed=self.admit("101")
        self.assertEqual(len(allowed),3)
        self.commit_approved(allowed)
        self.assertEqual(self.get("102")["status"],"PUBLIC_HISTORICAL_GAP_RESEARCH_COMPLETE")
        self.assertEqual((self.publish/cold.STATE).read_bytes(),original)

    def test_bounded_chunk_progression_reuses_exact_previous_state(self):
        with patch.object(hist,"MAX_BLOCKS_PER_RUN",20):
            a=self.get("101")
            self.assertEqual((a["from_block"],a["to_block"]),(20,39))
            self.stage_files(a)
            with patch.object(hist,"START_BLOCK",20):
                allowed=self.admit("101")
            self.commit_approved(allowed)
            b=self.get("102")
            self.assertEqual((b["from_block"],b["to_block"]),(40,59))
            row=json.loads((self.root/b["receipt"]).read_text())
            self.assertEqual(row["previous_cursor"],39)
            self.assertIs(row["bootstrap_not_full_history"],False)
            self.stage_files(b)
            allowed=self.admit("102")
            self.commit_approved(allowed)
            c=self.get("103")
            self.assertEqual((c["from_block"],c["to_block"]),(60,78))
            self.stage_files(c)
            allowed=self.admit("103")
            self.commit_approved(allowed)
        self.assertEqual(self.get("104")["status"],"PUBLIC_HISTORICAL_GAP_RESEARCH_COMPLETE")

    def test_rpc_429_never_issues_partial_or_canonical_cursor(self):
        original=(self.root/cold.STATE).read_bytes()
        with self.assertRaisesRegex(RuntimeError,"BOTH_PUBLIC_RPC_ENDPOINTS_FAILED"):
            self.get("101",RPC(head=100,bad=True))
        self.assertFalse((self.root/hist.STATE).exists())
        self.assertEqual((self.root/cold.STATE).read_bytes(),original)

    def test_corrupt_recovery_cursor_stops_without_any_advance(self):
        self.get("101")
        path=self.root/hist.STATE
        s=json.loads(path.read_text())
        s["last_scanned_block"]=999
        path.write_text(json.dumps(s))
        with self.assertRaisesRegex(RuntimeError,"CURSOR_UNTRUSTED_NO_ADVANCE"):
            self.get("102")
        self.assertEqual(json.loads(path.read_text())["last_scanned_block"],999)

    def test_wrong_upper_boundary_is_rejected_at_writer(self):
        out=self.get("101")
        self.stage_files(out)
        # Writer COLD source manifest is authoritative for this target floor.
        file=self.publish/cold.STATE
        x=json.loads(file.read_text())
        x["bootstrap_from_block"]=45
        x["sha256"]=cold.hash_obj({k:v for k,v in x.items() if k!="sha256"})
        file.write_bytes(cold.encode(x))
        with self.assertRaisesRegex(ValueError,"PONS_HISTORICAL_BOUNDARY"):
            self.admit("101")

    def test_extra_public_or_private_file_not_admitted(self):
        out=self.get("101")
        self.stage_files(out)
        hidden=self.stage/"public_pons_shadow/historical/runs/secret_wallet.txt"
        hidden.parent.mkdir(parents=True,exist_ok=True)
        hidden.write_text("private")
        with self.assertRaisesRegex(ValueError,"PONS_STAGE_FILE_COUNT_INVALID|PONS_EXTRA_OR_MISSING_ARTIFACT"):
            self.admit("101")

    def test_existing_cold_bootstrap_must_be_real(self):
        (self.root/cold.STATE).unlink()
        with patch.object(hist,"START_BLOCK",20):
            self.assertEqual(hist.capture(self.root,cold.Client(RPC(head=100)),
                                          "101",SHA,clock=lambda:NOW)["status"],
                             "PUBLIC_HISTORICAL_TARGET_UNVERIFIED_NO_ADVANCE")

if __name__=="__main__":
    unittest.main()
