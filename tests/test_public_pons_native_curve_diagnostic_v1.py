"""Offline original-source and read-only native Pons V2 reserve proxy tests."""
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from public_pons_shadow import collector as cold
from public_pons_shadow import forward_outcome as fw
from public_pons_shadow import native_curve_diagnostic_v1 as native
from tests.test_public_pons_forward_outcome import fake_source, NOW, SHA, POOL, A, B

BLOCKHASH="0x"+"c"*64
QUOTE_RESERVE=2*10**18
TOKEN_RESERVE=10**27

class FakeClient:
    def __init__(self,wrong_chain=False,malformed=False,reorg=False):
        self.url="fixture"
        self.calls=[]
        self.wrong_chain=wrong_chain
        self.malformed=malformed
        self.reorg=reorg
        self.blocks=0

    def connect(self):
        if self.wrong_chain:
            raise RuntimeError("NO_PUBLIC_CHAIN4663_RPC")
        self.calls.append(("eth_chainId",[]))

    def call(self,method,params):
        self.calls.append((method,params))
        if method=="eth_blockNumber":
            return hex(202)
        if method=="eth_getBlockByNumber":
            self.blocks+=1
            return {"number":hex(200),"hash":("0x"+"d"*64 if self.reorg and self.blocks>1 else BLOCKHASH)}
        if method=="eth_call":
            if params[0]["data"]==native.RESERVES_SELECTOR:
                return "0x1234" if self.malformed else "0x"+f"{QUOTE_RESERVE:064x}"+f"{TOKEN_RESERVE:064x}"
            if params[0]["data"]==native.DECIMALS_SELECTOR:
                return hex(18)
        raise AssertionError((method,params))

def seed(root,pair=native.ZERO):
    rp=fake_source(root,seen=NOW-timedelta(minutes=20))
    ep=rp.with_name(rp.name.replace("_receipt.json","_events.json"))
    events=json.loads(ep.read_text())
    for ev in events:
        ev["data"]="0x"+"0"*24+pair[2:]
    ep.write_bytes(cold.encode(events))
    receipt=json.loads(rp.read_text())
    receipt["events_sha256"]=cold.hash_obj(events)
    receipt["sha256"]=cold.hash_obj({k:v for k,v in receipt.items() if k!="sha256"})
    rp.write_bytes(cold.encode(receipt))
    fw.run(root,"101",SHA,clock=lambda:NOW,
           reader=lambda token,pair:{"state":"UNKNOWN_EXACT_PUBLIC_PAIR","price_usd":None,"pair":None})
    return rp


class NativeCurveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_native_curve_proxy_proven_source_and_exact_block_not_usd_or_trade(self):
        seed(self.root)
        state_before=(self.root/fw.STATE).read_bytes()
        client=FakeClient()
        result=native.read_proxies(self.root,client,clock=lambda:NOW+timedelta(minutes=3))
        self.assertEqual(result["candidate_count"],2)
        self.assertEqual(result["read_block_number"],200)
        self.assertEqual(result["read_block_hash"],BLOCKHASH)
        self.assertEqual(result["status"],"RESEARCH_ONLY_COMPLETED")
        self.assertFalse(result["execution_verified"])
        self.assertFalse(result["retrospective_t0_credit"])
        self.assertFalse(result["provider_first_known_verified"])
        self.assertEqual(len(result["results"]),2)
        for item in result["results"]:
            self.assertEqual(item["status"],"NATIVE_CURVE_PROXY_CAPTURED")
            self.assertEqual(item["price_proxy_eth_per_token"],"2E-9")
            self.assertEqual(item["quote_asset_address"],native.ZERO)
            self.assertEqual(item["valuation_type"],"VIRTUAL_RESERVE_RATIO_PROXY_NOT_EXECUTABLE")
            self.assertEqual(item["original_t0_utc"],cold.iso(NOW))
            self.assertIs(item["economic_edge_verified"],False)
        self.assertEqual((self.root/fw.STATE).read_bytes(),state_before)
        self.assertTrue(all(p[1][-1]==hex(200) for name,p in client.calls if name=="eth_call"))

    def test_custom_pair_yields_unknown_not_usd_price_or_rpc_call(self):
        seed(self.root,pair="0x"+"a"*40)
        client=FakeClient()
        out=native.read_proxies(self.root,client,clock=lambda:NOW+timedelta(minutes=3))
        self.assertTrue(all(r["status"]=="UNSUPPORTED_NON_NATIVE_QUOTE" for r in out["results"]))
        self.assertTrue(all(r["price_proxy_eth_per_token"] is None for r in out["results"]))
        self.assertFalse(any(name=="eth_call" for name,p in client.calls))

    def test_wrong_chain_fails_closed(self):
        seed(self.root)
        with self.assertRaisesRegex(RuntimeError,"NO_PUBLIC_CHAIN4663_RPC"):
            native.read_proxies(self.root,FakeClient(wrong_chain=True),clock=lambda:NOW)

    def test_malformed_reserves_research_unknown(self):
        seed(self.root)
        out=native.read_proxies(self.root,FakeClient(malformed=True),clock=lambda:NOW)
        self.assertEqual([x["status"] for x in out["results"]],["SOURCE_OR_RPC_UNAVAILABLE"]*2)
        self.assertTrue(all(x["price_proxy_eth_per_token"] is None for x in out["results"]))

    def test_reorg_invalidates_entire_run(self):
        seed(self.root)
        with self.assertRaisesRegex(RuntimeError,"PONS_NATIVE_BLOCK_REORG_NO_CREDIT"):
            native.read_proxies(self.root,FakeClient(reorg=True),clock=lambda:NOW)

    def test_state_hash_poison_cannot_be_reinterpreted_as_price(self):
        seed(self.root)
        f=self.root/fw.STATE
        obj=json.loads(f.read_text())
        obj["candidates"][0]["source_pool"]="0x"+"0"*40
        f.write_text(json.dumps(obj))
        with self.assertRaisesRegex(ValueError,"PONS_NATIVE_FORWARD_STATE_UNTRUSTED"):
            native.read_proxies(self.root,FakeClient(),clock=lambda:NOW)

    def test_no_forward_cohort_has_no_network_or_trade_side_effect(self):
        c=FakeClient()
        out=native.read_proxies(self.root,c,clock=lambda:NOW)
        self.assertEqual(out["status"],"NO_PUBLIC_FORWARD_FREEZE")
        self.assertEqual(c.calls,[])
        self.assertFalse(out["portfolio_action"])

    def test_reject_invalid_event_pair_word(self):
        seed(self.root)
        rp=json.loads((self.root/fw.STATE).read_text())["candidates"][0]["source_receipt"]
        ep=(self.root/rp).with_name(Path(rp).name.replace("_receipt.json","_events.json"))
        ev=json.loads(ep.read_text())
        ev[0]["data"]="0x"
        ep.write_bytes(cold.encode(ev))
        out=native.read_proxies(self.root,FakeClient(),clock=lambda:NOW)
        self.assertEqual(out["results"][0]["status"],"SOURCE_OR_RPC_UNAVAILABLE")
        self.assertIsNone(out["results"][0]["price_proxy_eth_per_token"])

if __name__=="__main__":
    unittest.main()
