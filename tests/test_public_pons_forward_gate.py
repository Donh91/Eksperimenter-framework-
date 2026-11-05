"""Offline stage gate tests, source-bound HOT-only, no network or private lists."""
import json
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from tests.test_public_pons_forward_outcome import NOW, SHA, POOL, fake_source
from public_pons_shadow import collector as cold
from public_pons_shadow import forward_outcome as fw
from public_pons_shadow import forward_gate as gate


class TestForwardStage(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.source=self.root/"source"
        self.publish=self.root/"publish"
        self.stage=self.root/"stage"
        self.source.mkdir()
        self.publish.mkdir()
        rp=fake_source(self.source)
        for rel in (rp.relative_to(self.source),
                    rp.with_name(rp.name.replace("_receipt.json","_events.json")).relative_to(self.source)):
            dest=self.publish/rel
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(self.source/rel,dest)

    def stage_run(self,when,runid):
        def read(token,pair):
            return {"state":"PUBLIC_SPOT_OBSERVED_NON_EXECUTABLE",
                    "price_usd":"0.00091","pair":pair or POOL}
        out=fw.run(self.source,runid,SHA,clock=lambda:when,reader=read)
        self.assertEqual(out["status"],"PUBLIC_FORWARD_CAPTURED_UNPUBLISHED")
        self.stage.mkdir(parents=True,exist_ok=True)
        for rel in [fw.STATE,Path(out["receipt"]),
                    Path(out["receipt"].replace("_receipt.json","_events.json"))]:
            dest=self.stage/rel
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(self.source/rel,dest)
        return out

    def publish_state(self,out):
        for rel in [fw.STATE,Path(out["receipt"]),
                    Path(out["receipt"].replace("_receipt.json","_events.json"))]:
            dest=self.publish/rel
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(self.stage/rel,dest)

    def test_stage_replays_public_provenance_and_frozen_observation(self):
        out=self.stage_run(NOW,"101")
        update=gate.validate(self.stage,self.publish,"101",SHA)
        self.assertEqual(len(update),3)
        self.assertEqual(str(update[0]),str(fw.STATE))
        self.publish_state(out)
        shutil.rmtree(self.stage)
        out2=self.stage_run(NOW+timedelta(hours=24),"102")
        update2=gate.validate(self.stage,self.publish,"102",SHA)
        self.assertEqual(len(update2),3)
        self.assertEqual(json.loads((self.stage/fw.STATE).read_text())["candidates"][0]["t0_utc"],cold.iso(NOW))

    def test_private_or_unpublished_source_is_not_admitted(self):
        self.stage_run(NOW,"101")
        # Missing original source publication in writer checkout.
        shutil.rmtree(self.publish)
        self.publish.mkdir()
        with self.assertRaises(ValueError):
            gate.validate(self.stage,self.publish,"101",SHA)

    def test_tampered_source_claim_or_rewritten_freeze_rejected_even_with_new_hash(self):
        out=self.stage_run(NOW,"101")
        self.publish_state(out)
        shutil.rmtree(self.stage)
        self.stage_run(NOW+timedelta(hours=24),"102")
        state=self.stage/fw.STATE
        obj=json.loads(state.read_text())
        obj["candidates"][0]["t0_utc"]="2026-10-01T00:00:00Z"
        obj["sha256"]=cold.hash_obj({k:v for k,v in obj.items() if k!="sha256"})
        state.write_bytes(cold.encode(obj))
        with self.assertRaisesRegex(ValueError,"FORWARD_FREEZE_REWRITTEN"):
            gate.validate(self.stage,self.publish,"102",SHA)

    def test_unknown_provider_price_cannot_be_substituted_with_return(self):
        self.stage_run(NOW,"101")
        state=self.stage/fw.STATE
        obj=json.loads(state.read_text())
        obj["candidates"][0]["observations"]["BASELINE"]["realizable_net_return"]=100.0
        obj["sha256"]=cold.hash_obj({k:v for k,v in obj.items() if k!="sha256"})
        state.write_bytes(cold.encode(obj))
        with self.assertRaises(ValueError):
            gate.validate(self.stage,self.publish,"101",SHA)

    def test_unrelated_stage_files_not_admitted_by_public_gate(self):
        self.stage_run(NOW,"101")
        self.stage.joinpath("untrusted_user_positions.csv").write_text("wallet,private\n")
        from public_pons_shadow import publish_gate
        from unittest.mock import patch
        with patch.object(publish_gate.subprocess,"run"),patch.object(
                publish_gate.subprocess,"check_output",return_value=SHA+"\n"):
            with self.assertRaisesRegex(ValueError,"PONS_EXTRA_OR_MISSING_ARTIFACT"):
                publish_gate.validate(self.stage,self.publish,"101",SHA)

if __name__=="__main__":
    unittest.main()
