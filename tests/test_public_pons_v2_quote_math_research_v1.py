"""Independent arithmetic and adversarial tests for official Pons V2 quote math."""
import copy
import unittest
from public_pons_shadow import v2_quote_math_research_v1 as q

STATE={
    "chain_id":4663,"quote_asset":"NATIVE_ETH",
    "quote_reserve_wei":2*10**18,"token_reserve_raw":10**27,
    "sellable_tokens_raw":7*10**26,
    "fee_bps":100,"creator_tax_bps":50,
    "phase":0,"ready_to_graduate":False,"graduated":False,
}


class PonsV2QuoteMathTests(unittest.TestCase):
    def test_buy_exact_fee_order_and_constant_product_no_fills_claimed(self):
        quote=10**16
        net=quote-(quote*100//10000)-(quote*50//10000)
        expected=net*10**27//(2*10**18+net)
        got=q.research_buy(STATE,quote,0)
        self.assertEqual(got["status"],"THEORETICAL_CURVE_BUY_NOT_EXECUTABLE")
        self.assertEqual(int(got["token_out_raw"]),expected)
        self.assertEqual(int(got["nominal_spent_wei"]),quote)
        self.assertEqual(int(got["refund_wei"]),0)
        self.assertFalse(got["execution_verified"])
        self.assertFalse(got["recipient_tax_verified"])
        self.assertFalse(got["trading_authority"])
        self.assertFalse(got["economic_edge_verified"])

    def test_sell_fees_are_charged_after_gross_not_as_mirrored_buy(self):
        n=1_000_000*10**18
        gross=n*2*10**18//(10**27+n)
        fees=gross*100//10000
        tax=gross*50//10000
        got=q.research_sell(STATE,n)
        self.assertEqual(got["status"],"THEORETICAL_CURVE_SELL_NOT_EXECUTABLE")
        self.assertEqual(int(got["gross_quote_out_wei"]),gross)
        self.assertEqual(int(got["indicative_net_quote_out_wei"]),gross-fees-tax)
        self.assertLess(int(got["indicative_net_quote_out_wei"]),gross)
        self.assertFalse(got["snipe_tax_applied_on_sell"])
        self.assertFalse(got["full_exit_verified"])
        self.assertFalse(got["gas_slippage_and_wallet_balance_verified"])

    def test_explicit_recipient_specific_buy_snipe_required(self):
        got=q.research_buy(STATE,10**16,None)
        self.assertEqual(got["status"],"UNKNOWN_RECIPIENT_SPECIFIC_SNIPE_TAX")
        self.assertNotIn("token_out_raw",got)
        self.assertFalse(got["recipient_tax_verified"])

    def test_snipe_cap_matches_official_v2_per_recipient_fee_order(self):
        v=q.research_buy(STATE,10**18,9999)
        self.assertEqual(v["snipe_tax_bps_capped"],9750)
        self.assertEqual(v["snipe_tax_bps_explicit_input"],9999)
        self.assertEqual(v["status"],"THEORETICAL_CURVE_BUY_NOT_EXECUTABLE")

    def test_buy_partial_fill_near_graduation_refunds_remainder(self):
        s=dict(STATE,sellable_tokens_raw=500*10**18)
        out=q.research_buy(s,10**18,0)
        self.assertEqual(out["status"],"THEORETICAL_CURVE_BUY_NOT_EXECUTABLE")
        self.assertEqual(int(out["token_out_raw"]),500*10**18)
        self.assertGreater(int(out["refund_wei"]),0)
        self.assertTrue(out["possible_partial_graduation_fill"])
        self.assertLess(int(out["nominal_spent_wei"]),10**18)
        self.assertEqual(int(out["nominal_spent_wei"])+int(out["refund_wei"]),10**18)

    def test_sell_closed_before_graduation_if_ready(self):
        s=dict(STATE,ready_to_graduate=True)
        self.assertEqual(q.research_sell(s,10**18)["status"],
                         "CURVE_SELL_CLOSED_READY_TO_GRADUATE")
        self.assertEqual(q.research_buy(s,10**18,0)["status"],
                         "CURVE_BUY_CLOSED_READY_TO_GRADUATE")

    def test_phase_swept_pool_created_and_rescue_never_curve_sell_or_buy(self):
        for phase in (1,2,3):
            with self.subTest(phase=phase):
                s=dict(STATE,phase=phase,graduated=True)
                self.assertEqual(q.research_sell(s,10**18)["status"],
                                 "CURVE_CLOSED_ROUTE_V4_OR_RECOVERY_REQUIRED")
                self.assertEqual(q.research_buy(s,10**18,0)["status"],
                                 "CURVE_CLOSED_ROUTE_V4_OR_RECOVERY_REQUIRED")

    def test_phase_and_graduated_contradiction_is_unknown(self):
        s=dict(STATE,phase=0,graduated=True)
        self.assertEqual(q.research_sell(s,10**18)["status"],
                         "UNKNOWN_INVALID_STATE_OR_SIZE")

    def test_missing_stale_or_unsupported_state_has_no_quote(self):
        for key,value in [
            ("chain_id",1),("chain_id","4663"),("quote_asset","USDG"),
            ("quote_reserve_wei",None),("quote_reserve_wei",0),
            ("token_reserve_raw",0),("fee_bps",None),
            ("creator_tax_bps",9999),("phase",4),
            ("ready_to_graduate",None),
            ("sellable_tokens_raw",2*10**27),
        ]:
            with self.subTest(key=key,value=value):
                s=dict(STATE,**{key:value})
                out=q.research_sell(s,10**18)
                self.assertEqual(out["status"],"UNKNOWN_INVALID_STATE_OR_SIZE")
                self.assertFalse(out["execution_verified"])

    def test_oversized_and_negative_and_bool_amounts_are_unknown(self):
        for n in (-1,0,True,q.UINT256+1,None):
            with self.subTest(value=n):
                self.assertEqual(q.research_sell(STATE,n)["status"],
                                 "UNKNOWN_INVALID_STATE_OR_SIZE")
                self.assertEqual(q.research_buy(STATE,n,0)["status"],
                                 "UNKNOWN_INVALID_STATE_OR_SIZE")

    def test_amount_in_rounding_and_reserve_safety(self):
        self.assertEqual(q.amount_out(100,200,300),100)
        self.assertEqual(q.amount_in(100,200,300),101)
        with self.assertRaisesRegex(ValueError,"EXCEEDS_RESERVE"):
            q.amount_in(300,200,300)

    def test_no_net_value_snipe_fee_or_zero_fill_cannot_claim_executable(self):
        s=dict(STATE,quote_reserve_wei=2*10**18,token_reserve_raw=1)
        out=q.research_buy(s,1,0)
        self.assertNotEqual(out["status"],"THEORETICAL_CURVE_BUY_NOT_EXECUTABLE")
        self.assertFalse(out["execution_verified"])

    def test_no_provider_and_no_user_wallet_artifacts(self):
        import inspect
        src=inspect.getsource(q)
        self.assertNotIn("urllib.request",src)
        self.assertNotIn("wallet.writeContract",src)
        self.assertNotIn("private_key",src)
        self.assertEqual(q.CONTRACT,"PUBLIC_PONS_V2_SIZE_FEE_QUOTE_CHALLENGER_V1")

if __name__=="__main__":
    unittest.main()
