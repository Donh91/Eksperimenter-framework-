"""Deterministic, offline positive and adversarial cases for public Pons source."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from public_pons_shadow import collector as p

NOW=datetime(2026,10,31,16,0,tzinfo=timezone.utc)
SHA="a"*40
BH="0x"+"b"*64

def log(block,idx=0):
    return dict(address=p.FACTORY,topics=[p.TOPIC0],
                removed=False,blockNumber=hex(block),logIndex=hex(idx),
                transactionHash="0x"+f"{block:064x}",blockHash=BH,data="0x")

class RPC:
    def __init__(self,head=100,bad=False):
        self.head=head
        self.bad=bad
        self.calls=[]
    def __call__(self,url,method,args):
        self.calls.append((method,args))
        if method=="eth_chainId":return hex(p.CHAIN_ID)
        if method=="eth_blockNumber":return hex(self.head)
        if method=="eth_getBlockByNumber":
            return {"number":args[0],"timestamp":hex(int(NOW.timestamp())-30)}
        if method=="eth_getLogs":
            if self.bad: raise RuntimeError("transient rpc down")
            begin=int(args[0]["fromBlock"],16);end=int(args[0]["toBlock"],16)
            return [log(begin)]
        raise AssertionError(method)

class PublicPonsTests(unittest.TestCase):
    def capture(self,root,rpc=None,run="100",bootstrap=10,limit=10):
        return p.collect(root,p.Client(rpc or RPC()),run,SHA,clock=lambda:NOW,
                         bootstrap=bootstrap,max_scan=limit,chunk=3)

    def test_positive_contiguous_first_capture(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);src=RPC()
            out=self.capture(root,src)
            self.assertEqual((out["from_block"],out["to_block"]),(89,98))
            receipt=json.loads((root/out["receipt"]).read_text())
            self.assertEqual(receipt["event_count"],4)
            self.assertTrue(receipt["complete_bounded_range"])
            self.assertTrue(receipt["bootstrap_not_full_history"])
            self.assertFalse(receipt["private_source_admitted"])
            self.assertFalse(receipt["provider_first_known_verified"])
            self.assertFalse(receipt["alert_authority"])
            self.assertEqual(p.load_state(root)["last_scanned_block"],98)
            self.assertEqual(len([x for x in src.calls if x[0]=="eth_getLogs"]),4)

    def test_second_run_continues_exact_next_block(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.capture(root,RPC(),run="1")
            out=self.capture(root,RPC(head=106),run="2")
            self.assertEqual((out["from_block"],out["to_block"]),(99,104))
            receipt=json.loads((root/out["receipt"]).read_text())
            self.assertEqual(receipt["previous_cursor"],98)
            self.assertFalse(receipt["bootstrap_not_full_history"])

    def test_missing_new_block_is_no_write(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.capture(root,RPC(),run="1")
            result=self.capture(root,RPC(),run="2")
            self.assertEqual(result["status"],"NO_NEW_SAFE_BLOCK")
            self.assertEqual(len(list((root/"public_pons_shadow/runs").rglob("*_receipt.json"))),1)

    def test_gap_is_exposed_not_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);out=self.capture(root,RPC(head=110),limit=4)
            receipt=json.loads((root/out["receipt"]).read_text())
            self.assertEqual(receipt["remaining_blocks"],6)
            self.assertEqual(receipt["to_block"],102)

    def test_bad_chain_never_writes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with self.assertRaisesRegex(RuntimeError,"NO_PUBLIC_CHAIN4663_RPC"):
                self.capture(root,lambda url,m,args:"0x1")
            self.assertFalse((root/p.STATE).exists())

    def test_all_rpc_fail_never_advances(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with self.assertRaisesRegex(RuntimeError,"BOTH_PUBLIC_RPC_ENDPOINTS_FAILED"):
                self.capture(root,RPC(bad=True))
            self.assertFalse((root/p.STATE).exists())

    def test_corrupted_cursor_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.capture(root,RPC())
            state=root/p.STATE;row=json.loads(state.read_text())
            row["last_scanned_block"]=1;state.write_text(json.dumps(row))
            with self.assertRaisesRegex(RuntimeError,"CURSOR_UNTRUSTED_NO_ADVANCE"):
                self.capture(root,RPC(head=200),run="2")

    def test_wrong_event_rejected(self):
        bad=log(90);bad["address"]="0x"+"d"*40
        with self.assertRaisesRegex(ValueError,"LOG_FACTORY_MISMATCH"):
            p.validate_log(bad,89,98)
        bad=log(90);bad["topics"]=["0x"+"e"*64]
        with self.assertRaisesRegex(ValueError,"LOG_TOPIC_MISMATCH"):
            p.validate_log(bad,89,98)
        bad=log(90);bad["removed"]=True
        with self.assertRaisesRegex(ValueError,"LOG_REMOVED_OR_INVALID"):
            p.validate_log(bad,89,98)

    def test_duplicate_and_cap_refused(self):
        class Duplicate(RPC):
            def __call__(self,url,method,args):
                if method=="eth_getLogs":
                    begin=int(args[0]["fromBlock"],16)
                    return [log(begin),log(begin)]
                return super().__call__(url,method,args)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(RuntimeError,"DUPLICATE_LOG"):
                self.capture(Path(d),Duplicate())
        with tempfile.TemporaryDirectory() as d:
            with patch.object(p,"MAX_EVENTS",0):
                with self.assertRaisesRegex(RuntimeError,"SOURCE_EVENT_CAP"):
                    self.capture(Path(d),RPC())
            self.assertFalse((Path(d)/p.STATE).exists())

    def test_immutable_same_run_cannot_rewrite(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.capture(root,RPC(),run="1")
            with self.assertRaisesRegex(RuntimeError,"IMMUTABLE_RUN_EXISTS"):
                self.capture(root,RPC(head=110),run="1")

    def test_no_implicit_github_identity(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,"RUN_OR_CHECKOUT_SHA_INVALID"):
                p.collect(Path(d),p.Client(RPC()),"","",clock=lambda:NOW)



class WorkflowPolicyTests(unittest.TestCase):
    def test_public_only_workflow_authority(self):
        text=Path(".github/workflows/public-pons-shadow.yml").read_text()
        self.assertIn("name: Public Pons V2 Factory Shadow",text)
        self.assertIn("group: experiment-execution-plane",text)
        self.assertIn("cancel-in-progress: false",text)
        self.assertIn("github.ref == 'refs/heads/main'",text)
        self.assertIn("github.repository == 'Donh91/Eksperimenter-framework-'",text)
        self.assertIn("permissions:\n  contents: read",text)
        self.assertIn("    permissions:\n      contents: write",text)
        self.assertNotIn("secrets.",text)
        self.assertNotIn("pull_request_target:",text)
        self.assertNotIn("workflow_run:",text)
        self.assertIn("GITHUB_CHECKOUT_SHA=$(git rev-parse --verify HEAD)",text)
        self.assertIn("PUBLIC_PONS_SHADOW_READBACK_PASS",text)
        self.assertIn("push:\n    branches: [main]",text)
        self.assertIn("'.github/workflows/public-pons-shadow.yml'",text)
        self.assertIn("'public_pons_shadow/collector.py'",text)
        self.assertNotIn("'public_pons_shadow/**'",text)
        self.assertNotIn("'public_pons_shadow/runs/**'",text)

    def test_workflow_actions_locked_to_successful_sha(self):
        text=Path(".github/workflows/public-pons-shadow.yml").read_text()
        checkout="actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
        python="actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"
        self.assertIn("uses: "+checkout, text)
        self.assertIn("uses: "+python, text)
        self.assertNotIn("uses: actions/checkout@v4", text)
        self.assertNotIn("uses: actions/setup-python@v5", text)
        # Pure code review fixture; no public source or credential mutation.
        def pinned(source):
            return ("uses: "+checkout in source and "uses: "+python in source
                    and "uses: actions/checkout@v4" not in source
                    and "uses: actions/setup-python@v5" not in source)
        self.assertTrue(pinned(text))
        self.assertFalse(pinned(text.replace(checkout, "actions/checkout@v4")))
        self.assertFalse(pinned(text.replace(python, "actions/setup-python@v5")))

    def test_mutation_guard_catches_privileged_source(self):
        text=Path(".github/workflows/public-pons-shadow.yml").read_text()
        def protected(source):
            return ("secrets." not in source and "pull_request_target:" not in source
                    and "github.ref == 'refs/heads/main'" in source
                    and "group: experiment-execution-plane" in source)
        self.assertTrue(protected(text))
        self.assertFalse(protected(text+"\n# secrets.LEAK"))
        self.assertFalse(protected(text.replace("workflow_dispatch:", "pull_request_target:")))
        self.assertFalse(protected(text.replace("refs/heads/main","refs/heads/any")))
        self.assertFalse(protected(text.replace("group: experiment-execution-plane","group: random")))


if __name__=="__main__":
    unittest.main()
