"""PIT, source-custody, retry and economic-authority tests for native shadow."""
import json
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from public_pons_shadow import collector as cold
from public_pons_shadow import curve_forward_v1 as curve
from public_pons_shadow import curve_forward_gate_v1 as gate
from tests.test_public_pons_native_curve_diagnostic_v1 import FakeClient, seed, NOW, SHA


class TestPublicNativeDue(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        self.source=root/"source"
        self.published=root/"published"
        self.stage=root/"stage"
        self.source.mkdir()
        self.published.mkdir()
        seed(self.source)
        for path in (self.source/"public_pons_shadow/hot/runs").rglob("*.json"):
            rel=path.relative_to(self.source)
            dst=self.published/rel
            dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(path,dst)
        state=self.source/"public_pons_shadow/forward_state.json"
        target=self.published/"public_pons_shadow/forward_state.json"
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(state,target)
        self.native_t0=NOW+timedelta(minutes=2)

    def capture(self,when,runid,client=None):
        return curve.capture(self.source,runid,SHA,client=client or FakeClient(),
                             clock=lambda:when)

    def st(self):
        return json.loads((self.source/curve.STATE).read_text())

    def stage_files(self,out):
        self.stage.mkdir(parents=True,exist_ok=True)
        for rel in (curve.STATE,Path(out["receipt"]),
                    Path(out["receipt"].replace("_receipt.json","_events.json"))):
            dest=self.stage/rel
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(self.source/rel,dest)

    def publish(self,out):
        self.stage_files(out)
        allowed=gate.validate(self.stage,self.published,out["receipt"].split("_")[-2],SHA)
        self.assertEqual(len(allowed),3)
        for path in allowed:
            dst=self.published/path
            dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(self.stage/path,dst)
        shutil.rmtree(self.stage)

    def baseline(self,runid="101"):
        out=self.capture(self.native_t0,runid)
        self.assertEqual(out["status"],"PUBLIC_NATIVE_CURVE_CAPTURED_UNPUBLISHED")
        self.publish(out)
        return out

    def test_two_distinct_real_native_baselines_never_rewrite_forward_t0(self):
        raw=(self.published/"public_pons_shadow/forward_state.json").read_bytes()
        self.baseline()
        state=self.st()
        self.assertEqual(len(state["candidates"]),2)
        self.assertEqual(state["authority"],"PUBLIC_RESEARCH_ONLY")
        self.assertFalse(state["portfolio_action"])
        for c in state["candidates"]:
            self.assertEqual(c["native_t0_utc"],cold.iso(self.native_t0))
            self.assertEqual(c["original_forward_t0_utc"],cold.iso(NOW))
            self.assertNotEqual(c["native_t0_utc"],c["original_forward_t0_utc"])
            self.assertEqual(c["baseline"]["target_utc"],cold.iso(self.native_t0))
            self.assertEqual(c["baseline"]["status"],"NATIVE_CURVE_PROXY_CAPTURED")
            self.assertFalse(c["first_known_verified"])
            self.assertFalse(c["execution_verified"])
            self.assertEqual(set(c["observations"]),{"24H","72H"})
            self.assertEqual(c["observations"]["24H"],[])
        self.assertEqual((self.published/"public_pons_shadow/forward_state.json").read_bytes(),raw)
        self.assertEqual(self.capture(self.native_t0+timedelta(minutes=10),"102")["status"],
                         "NO_NATIVE_OBSERVATIONS_DUE")

    def test_24h_72h_due_chain_and_frozen_blocked_clock(self):
        self.baseline()
        h24=self.native_t0+timedelta(hours=24,minutes=22)
        out=self.capture(h24,"102")
        self.assertEqual(out["source_calls"],2)
        self.publish(out)
        for c in self.st()["candidates"]:
            o=c["observations"]["24H"]
            self.assertEqual(len(o),1)
            self.assertEqual(o[0]["status"],"NATIVE_CURVE_PROXY_CAPTURED")
            self.assertEqual(o[0]["target_utc"],cold.iso(self.native_t0+timedelta(hours=24)))
            self.assertEqual(o[0]["observed_at_utc"],cold.iso(h24))
            self.assertIsNone(o[0].get("realizable_net_return"))
        h72=self.native_t0+timedelta(hours=72,minutes=27)
        o2=self.capture(h72,"103")
        self.publish(o2)
        self.assertEqual(len(self.st()["candidates"]),2)
        for c in self.st()["candidates"]:
            self.assertEqual(len(c["observations"]["24H"]),1)
            self.assertEqual(len(c["observations"]["72H"]),1)
            self.assertEqual(c["observations"]["72H"][0]["status"],"NATIVE_CURVE_PROXY_CAPTURED")
        self.assertEqual(self.capture(h72+timedelta(minutes=7),"104")["status"],
                         "NO_NATIVE_OBSERVATIONS_DUE")

    def test_due_responding_after_four_hours_is_missed_without_price(self):
        self.baseline()
        late=self.native_t0+timedelta(hours=29)
        client=FakeClient()
        out=self.capture(late,"102",client)
        self.assertEqual(out["source_calls"],0)
        self.publish(out)
        self.assertFalse(any(name=="eth_call" for name,params in client.calls))
        for c in self.st()["candidates"]:
            o=c["observations"]["24H"][0]
            self.assertEqual(o["status"],"WINDOW_MISSED")
            self.assertIsNone(o["ratio_eth_per_token"])
            self.assertIsNone(o["block_hash"])

    def test_provider_unknown_then_retry_preserves_unknown_and_success(self):
        self.baseline()
        t=self.native_t0+timedelta(hours=24)
        out=self.capture(t,"102",FakeClient(malformed=True))
        self.publish(out)
        self.assertTrue(all(c["observations"]["24H"][0]["status"]=="UNKNOWN_NATIVE_CURVE_RPC"
                            for c in self.st()["candidates"]))
        again=self.capture(t+timedelta(minutes=15),"103")
        self.publish(again)
        for c in self.st()["candidates"]:
            self.assertEqual([x["status"] for x in c["observations"]["24H"]],
                             ["UNKNOWN_NATIVE_CURVE_RPC","NATIVE_CURVE_PROXY_CAPTURED"])
        self.assertEqual(self.capture(t+timedelta(minutes=17),"104")["status"],
                         "NO_NATIVE_OBSERVATIONS_DUE")

    def test_three_unknown_retries_not_falsely_marked_missed_before_window(self):
        self.baseline()
        due=self.native_t0+timedelta(hours=24)
        for i in range(3):
            self.publish(self.capture(due+timedelta(minutes=15*i),str(102+i),
                                      FakeClient(malformed=True)))
        self.assertEqual(self.capture(due+timedelta(minutes=60),"105")["status"],
                         "NO_NATIVE_OBSERVATIONS_DUE")
        out=self.capture(due+timedelta(hours=5),"106")
        self.publish(out)
        for c in self.st()["candidates"]:
            statuses=[v["status"] for v in c["observations"]["24H"]]
            self.assertEqual(statuses,["UNKNOWN_NATIVE_CURVE_RPC"]*3+["WINDOW_MISSED"])

    def test_raw_source_and_frozen_native_t0_tampering_rejected_after_rehash(self):
        self.baseline()
        out=self.capture(self.native_t0+timedelta(hours=24),"102")
        self.stage_files(out)
        state=self.stage/curve.STATE
        obj=json.loads(state.read_text())
        obj["candidates"][0]["native_t0_utc"]=cold.iso(NOW-timedelta(days=1))
        obj["sha256"]=cold.hash_obj({k:v for k,v in obj.items() if k!="sha256"})
        state.write_bytes(cold.encode(obj))
        with self.assertRaisesRegex(ValueError,"CURVE_GATE_FROZEN_ENTRY_REWRITTEN"):
            gate.validate(self.stage,self.published,"102",SHA)

    def test_self_rehashed_fake_economic_profit_or_price_rejected(self):
        out=self.capture(self.native_t0,"101")
        self.stage_files(out)
        f=self.stage/curve.STATE
        obj=json.loads(f.read_text())
        obj["candidates"][0]["baseline"]["realizable_net_return"]=990
        obj["sha256"]=cold.hash_obj({k:v for k,v in obj.items() if k!="sha256"})
        f.write_bytes(cold.encode(obj))
        with self.assertRaises(ValueError):
            gate.validate(self.stage,self.published,"101",SHA)

    def test_ordered_events_and_no_token_value_if_missing_public_source(self):
        forward=self.source/"public_pons_shadow/forward_state.json"
        forward.unlink()
        self.assertEqual(self.capture(self.native_t0,"101")["status"],
                         "NO_NATIVE_OBSERVATIONS_DUE")

    def test_reorg_prevents_any_native_state_commit(self):
        with self.assertRaisesRegex(ValueError,"CURVE_BLOCK_REORG_NO_ADMISSION"):
            self.capture(self.native_t0,"101",FakeClient(reorg=True))
        self.assertFalse((self.source/curve.STATE).exists())

    def test_no_extra_unaudited_file_is_admitted(self):
        out=self.capture(self.native_t0,"101")
        self.stage_files(out)
        extra=self.stage/"public_pons_shadow/curve/runs/injected_wallet.csv"
        extra.parent.mkdir(parents=True,exist_ok=True)
        extra.write_text("secret")
        from public_pons_shadow import publish_gate
        from unittest.mock import patch
        with patch.object(publish_gate.subprocess,"run"),patch.object(
            publish_gate.subprocess,"check_output",return_value=SHA+"\n"):
            with self.assertRaises(ValueError):
                publish_gate.validate(self.stage,self.published,"101",SHA)


if __name__=="__main__":
    unittest.main()
