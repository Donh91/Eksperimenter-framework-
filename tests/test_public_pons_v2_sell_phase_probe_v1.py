"""Public onchain Pons V2 phase/fees, original source binding, no trade tests."""
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from public_pons_shadow import collector as cold
from public_pons_shadow import curve_forward_v1 as curve
from public_pons_shadow import v2_sell_phase_probe_v1 as sell
from tests.test_public_pons_native_curve_diagnostic_v1 import (
    FakeClient, seed, NOW, SHA, A, B, POOL,
)

NATIVE_T0=NOW+timedelta(minutes=2)
W=10**18


def word(v):
    return f"{v:064x}"


class FeePhaseClient(FakeClient):
    def __init__(self,phase=0,ready=False,graduated=False,sellable=7*10**26,
                 creator=50,tax=50,broken_factory=False,reorg=False):
        super().__init__(reorg=reorg)
        self.phase=phase
        self.ready=ready
        self.graduated=graduated
        self.sellable=sellable
        self.creator=creator
        self.tax=tax
        self.broken_factory=broken_factory

    def call(self,method,params):
        if method=="eth_call":
            request=params[0]
            selector=request["data"][:10]
            if selector==sell.SEL_LAUNCH:
                if self.broken_factory:
                    return "0x1234"
                token=int(request["data"][-40:],16)
                words=[token,int(POOL[2:],16),int("1"*40,16),
                       int("2"*40,16),0,4*10**18,
                       3000,10,self.creator,0,self.phase,0,0,0,1]
                return "0x"+"".join(word(x) for x in words)
            raw={
               sell.SEL_FEE:100,
               sell.SEL_TAX:self.tax,
               sell.SEL_SELLABLE:self.sellable,
               sell.SEL_READY:int(self.ready),
               sell.SEL_GRADUATED:int(self.graduated),
            }
            if selector in raw:return "0x"+word(raw[selector])
        return super().call(method,params)


class TestV2SellFrictionProbe(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        seed(self.root)
        curve.capture(self.root,"102",SHA,client=FakeClient(),clock=lambda:NATIVE_T0)

    def read(self,client=None):
        return sell.read_sell_shadow(self.root,client or FeePhaseClient(),
                                     clock=lambda:NATIVE_T0+timedelta(minutes=8))

    def test_source_bound_real_factory_phase_and_fee_yields_raw_quote_only(self):
        out=self.read()
        self.assertEqual(out["status"],"RESEARCH_ONLY_COMPLETED")
        self.assertEqual(out["read_block_number"],200)
        self.assertEqual(out["sample_count"],2)
        self.assertFalse(out["execution_verified"])
        self.assertFalse(out["auto_trade_allowed"])
        self.assertFalse(out["source_first_known_verified"])
        self.assertEqual(out["sha256"],cold.hash_obj({k:v for k,v in out.items() if k!="sha256"}))
        self.assertEqual({x["token"] for x in out["results"]},{A,B})
        for item in out["results"]:
            self.assertEqual(item["status"],"RESEARCH_PHASE_AND_FEES_OBSERVED_NO_EXECUTION")
            self.assertEqual(item["phase"],0)
            self.assertFalse(item["ready_to_graduate"])
            self.assertEqual(item["fee_bps"],100)
            self.assertEqual(item["creator_tax_bps"],50)
            self.assertEqual(len(item["token_sizes"]),3)
            self.assertTrue(all(i["calculation_status"]=="THEORETICAL_CURVE_SELL_NOT_EXECUTABLE"
                                for i in item["token_sizes"]))
            self.assertTrue(all(int(i["indicative_net_quote_wei"])>0
                                for i in item["token_sizes"]))
            self.assertFalse(item["full_exit_verified"])
            self.assertEqual(item["original_native_t0_utc"],cold.iso(NATIVE_T0))
        state=json.loads((self.root/curve.STATE).read_text())
        self.assertTrue(all(c["observations"]["24H"]==[] for c in state["candidates"]))

    def test_sell_closed_when_ready_before_graduation(self):
        out=self.read(FeePhaseClient(sellable=0,ready=True))
        self.assertEqual(len(out["results"]),2)
        for row in out["results"]:
            self.assertEqual(row["status"],"RESEARCH_PHASE_AND_FEES_OBSERVED_NO_EXECUTION")
            self.assertTrue(row["ready_to_graduate"])
            self.assertTrue(all(q["calculation_status"]=="CURVE_SELL_CLOSED_READY_TO_GRADUATE"
                                for q in row["token_sizes"]))
            self.assertTrue(all(q["indicative_net_quote_wei"] is None for q in row["token_sizes"]))

    def test_swept_and_pool_created_do_not_appear_sellable_on_curve(self):
        for phase in (1,2,3):
            with self.subTest(phase=phase):
                result=self.read(FeePhaseClient(phase=phase,ready=True,graduated=True,sellable=0))
                for row in result["results"]:
                    self.assertTrue(all(q["calculation_status"]=="CURVE_CLOSED_ROUTE_V4_OR_RECOVERY_REQUIRED"
                                        for q in row["token_sizes"]))

    def test_factory_creator_tax_conflict_no_positive_sell_quote(self):
        result=self.read(FeePhaseClient(tax=250,creator=50))
        for row in result["results"]:
            self.assertEqual(row["status"],"UNKNOWN_ONCHAIN_PHASE_OR_FEE_SOURCE")
            self.assertEqual(row["token_sizes"],[])

    def test_factory_bad_abi_no_positive_quote(self):
        result=self.read(FeePhaseClient(broken_factory=True))
        self.assertTrue(all(x["status"]=="UNKNOWN_ONCHAIN_PHASE_OR_FEE_SOURCE"
                            for x in result["results"]))

    def test_phase_and_curve_flags_conflict(self):
        result=self.read(FeePhaseClient(phase=0,ready=False,graduated=True))
        self.assertTrue(all(x["status"]=="UNKNOWN_ONCHAIN_PHASE_OR_FEE_SOURCE"
                            for x in result["results"]))

    def test_source_state_with_rehashed_wrong_curve_rejected_by_original_receipt(self):
        path=self.root/curve.STATE
        state=json.loads(path.read_text())
        state["candidates"][0]["curve"]="0x"+"f"*40
        state["sha256"]=cold.hash_obj({k:v for k,v in state.items() if k!="sha256"})
        path.write_bytes(cold.encode(state))
        result=self.read()
        self.assertEqual(result["results"][0]["status"],"UNKNOWN_ONCHAIN_PHASE_OR_FEE_SOURCE")
        self.assertEqual(result["results"][0]["token_sizes"],[])

    def test_wrong_chain_rpc_never_emits_prices(self):
        with self.assertRaisesRegex(RuntimeError,"NO_PUBLIC_CHAIN4663_RPC"):
            self.read(FakeClient(wrong_chain=True))

    def test_block_reorg_censors_entire_quote_proof(self):
        with self.assertRaisesRegex(ValueError,"SELL_SAFE_BLOCK_REORG_INVALIDATES_ALL"):
            self.read(FeePhaseClient(reorg=True))

    def test_missing_canonical_frozen_case_no_rpc(self):
        (self.root/curve.STATE).unlink()
        client=FeePhaseClient()
        result=self.read(client)
        self.assertEqual(result["status"],"NO_PUBLISHED_PUBLIC_SOURCES")
        self.assertEqual(client.calls,[])
        self.assertFalse(result["auto_trade_allowed"])

    def test_factory_address_padding_and_struct_length_fail_closed(self):
        good="0x"+"".join(word(0) for _ in range(15))
        with self.assertRaisesRegex(ValueError,"PHASE_OR_EXISTS_INVALID"):
            sell.decode_factory(good)
        with self.assertRaisesRegex(ValueError,"TUPLE_MALFORMED"):
            sell.decode_factory("0x"+word(1))

    def test_no_private_wallet_or_trade_side_effects(self):
        import inspect
        src=inspect.getsource(sell)
        self.assertNotIn("wallet.writeContract",src)
        self.assertNotIn("sendTransaction",src)
        self.assertNotIn("private_key",src)
        self.assertEqual(sell.MAX_CASES,2)


if __name__=="__main__":
    unittest.main()
