"""Strict, offline HOT/COLD invariants; no network, no private source admission."""
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from public_pons_shadow import collector as cold
from public_pons_shadow import hot_collector as hot

NOW=datetime(2026,11,2,8,0,tzinfo=timezone.utc)
SHA="a"*40
BH="0x"+"b"*64

class FakeRPC:
    def __init__(self,head=100000,fail=False):
        self.head=head
        self.fail=fail
        self.calls=[]
    def __call__(self,url,method,args):
        self.calls.append((method,args))
        if method=="eth_chainId":
            return hex(cold.CHAIN_ID)
        if method=="eth_blockNumber":
            return hex(self.head)
        if method=="eth_getLogs":
            if self.fail: raise RuntimeError("RPC_THROTTLED")
            lo=int(args[0]["fromBlock"],16)
            return [dict(address=cold.FACTORY,topics=[cold.TOPIC0],removed=False,
                         blockNumber=hex(lo),logIndex="0x0",
                         transactionHash="0x"+f"{lo:064x}",blockHash=BH,data="0x")]
        if method=="eth_getBlockByNumber":
            return {"number":args[0],"timestamp":hex(int(NOW.timestamp())-30),"hash":BH}
        raise AssertionError(method)

class TestHotShadow(unittest.TestCase):
    def capture(self,root,rpc,run="100",window=12):
        return hot.hot_capture(root,cold.Client(rpc),run,SHA,clock=lambda:NOW,window=window)

    def test_hot_newest_independent_of_cold(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/"public_pons_shadow").mkdir()
            coldstate=cold.signed(dict(contract=cold.CONTRACT,authority="PUBLIC_SHADOW_ONLY",
                 chain_id=cold.CHAIN_ID,factory=cold.FACTORY,topic0=cold.TOPIC0,
                 last_scanned_block=10,bootstrap_from_block=1,last_run_id="1",
                 last_receipt_sha256="abc"))
            p=root/cold.STATE
            p.write_bytes(cold.encode(coldstate))
            out=self.capture(root,FakeRPC(),window=12)
            self.assertEqual((out["from_block"],out["to_block"]),(99987,99998))
            self.assertEqual(json.loads(p.read_text()),coldstate)
            receipt=json.loads((root/out["receipt"]).read_text())
            self.assertEqual(receipt["authority"],"PUBLIC_HOT_SHADOW_ONLY")
            self.assertTrue(receipt["real_time_window_candidate"])
            for k in ("canonical_census_complete","private_source_admitted",
                      "provider_first_known_verified","alert_authority","portfolio_action"):
                self.assertIs(receipt[k],False)

    def test_contiguous_success_and_no_gap(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            self.capture(root,FakeRPC(head=100),run="101",window=12)
            out=self.capture(root,FakeRPC(head=107),run="102",window=12)
            self.assertEqual((out["from_block"],out["to_block"]),(99,105))
            self.assertEqual(out["missed_hot_range_count"],0)

    def test_skipped_schedule_creates_explicit_gap(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            self.capture(root,FakeRPC(head=100),run="101",window=12)
            out=self.capture(root,FakeRPC(head=200),run="102",window=12)
            self.assertEqual((out["from_block"],out["to_block"]),(187,198))
            receipt=json.loads((root/out["receipt"]).read_text())
            self.assertEqual(receipt["missed_hot_ranges"],
                             [{"from_block":99,"to_block":186,
                               "reason":"HOT_MISSED_SCHEDULE_UNKNOWN_COLD_COVERAGE"}])
            self.assertIs(receipt["history_complete"],False)

    def test_rpc_failure_does_not_mutate_any_hot_cursor(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            self.capture(root,FakeRPC(head=100),run="101",window=12)
            before=(root/hot.STATE).read_bytes()
            with self.assertRaises(RuntimeError):
                self.capture(root,FakeRPC(head=105,fail=True),run="102",window=12)
            self.assertEqual((root/hot.STATE).read_bytes(),before)

    def test_no_new_safe_block_does_not_write(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            self.capture(root,FakeRPC(head=100),run="101",window=12)
            self.assertEqual(self.capture(root,FakeRPC(head=100),run="102",window=12)["status"],"NO_NEW_HOT_SAFE_BLOCK")
            self.assertFalse(list(root.glob("**/*_102_receipt.json")))

    def test_safe_head_regression_is_not_a_no_op(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            self.capture(root,FakeRPC(head=100),run="101",window=12)
            before=(root/hot.STATE).read_bytes()
            with self.assertRaisesRegex(RuntimeError,"HOT_HEAD_REGRESSED_NO_ADVANCE"):
                self.capture(root,FakeRPC(head=96),run="102",window=12)
            self.assertEqual((root/hot.STATE).read_bytes(),before)
            self.assertFalse(list(root.glob("**/*_102_receipt.json")))

    def test_equal_head_reorg_is_not_misclassified_as_no_new_block(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            self.capture(root,FakeRPC(head=100),run="101",window=12)
            class SameHeightReorgRPC(FakeRPC):
                def __call__(self,url,method,args):
                    result=super().__call__(url,method,args)
                    if method=="eth_getBlockByNumber":
                        result["hash"]="0x"+"c"*64
                    return result
            before=(root/hot.STATE).read_bytes()
            with self.assertRaisesRegex(RuntimeError,"HOT_REORG_ANCHOR_MISMATCH_NO_ADVANCE"):
                self.capture(root,SameHeightReorgRPC(head=100),run="102",window=12)
            self.assertEqual((root/hot.STATE).read_bytes(),before)

    def test_reorg_at_previous_hot_cursor_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            self.capture(root,FakeRPC(head=100),run="101",window=12)
            class ReorgRPC(FakeRPC):
                def __call__(self,url,method,args):
                    result=super().__call__(url,method,args)
                    if method=="eth_getBlockByNumber" and args[0]==hex(98):
                        result["hash"]="0x"+"c"*64
                    return result
            before=(root/hot.STATE).read_bytes()
            with self.assertRaisesRegex(RuntimeError,"HOT_REORG_ANCHOR_MISMATCH_NO_ADVANCE"):
                self.capture(root,ReorgRPC(head=105),run="102",window=12)
            self.assertEqual((root/hot.STATE).read_bytes(),before)

    def test_poisoned_previous_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            self.capture(root,FakeRPC(head=100),run="101",window=12)
            obj=json.loads((root/hot.STATE).read_text());obj["last_observed_block"]=500
            (root/hot.STATE).write_text(json.dumps(obj))
            with self.assertRaisesRegex(RuntimeError,"HOT_STATE_UNTRUSTED"):
                self.capture(root,FakeRPC(head=200),run="102",window=12)

    def test_limits_and_malformed_log_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with self.assertRaises(ValueError):
                self.capture(root,FakeRPC(),window=hot.WINDOW+1)
            class BadRPC(FakeRPC):
                def __call__(self,url,method,args):
                    out=super().__call__(url,method,args)
                    if method=="eth_getLogs":out[0]["address"]="0x"+"0"*40
                    return out
            with self.assertRaisesRegex(ValueError,"LOG_FACTORY_MISMATCH"):
                self.capture(root,BadRPC(),window=12)

    def test_hot_has_no_synthetic_first_seen_authority(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            out=self.capture(root,FakeRPC(),window=12)
            receipt=json.loads((root/out["receipt"]).read_text())
            self.assertNotIn("first_known_verified",receipt)
            self.assertIs(receipt["github_first_publication_verified"],False)
            self.assertIs(receipt["canonical_census_complete"],False)

if __name__=="__main__":
    unittest.main()
