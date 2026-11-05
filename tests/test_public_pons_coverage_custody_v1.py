"""Adversarial public HOT/COLD source custody tests, no network."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from public_pons_shadow import collector as cold
from public_pons_shadow import coverage_custody_v1 as audit
from tests.test_public_pons_shadow import RPC, NOW, SHA


class TestCoverageCustody(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        cold.collect(self.root,cold.Client(RPC(head=100)),"101",SHA,
                     clock=lambda:NOW,bootstrap=10,max_scan=10,chunk=3)
        cold.collect(self.root,cold.Client(RPC(head=106)),"102",SHA,
                     clock=lambda:NOW,bootstrap=10,max_scan=10,chunk=3)
        self.write_hot()

    def write_hot(self):
        h=cold.signed({
            "contract":"PUBLIC_PONS_V2_HOT_SHADOW_V1",
            "authority":"PUBLIC_HOT_SHADOW_ONLY",
            "chain_id":cold.CHAIN_ID,"factory":cold.FACTORY,"topic0":cold.TOPIC0,
            "last_run_id":"102","last_receipt_sha256":"b"*64,
            "last_observed_block":104,"last_observed_block_hash":"0x"+"b"*64,
            "unobserved_gaps":[
              {"from_block":90,"to_block":95,"reason":"HOT_MISSED_SCHEDULE_UNKNOWN_COLD_COVERAGE"},
              {"from_block":120,"to_block":130,"reason":"HOT_MISSED_SCHEDULE_UNKNOWN_COLD_COVERAGE"},
            ]})
        p=self.root/"public_pons_shadow/hot_state.json"
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(cold.encode(h))

    def receipts(self):
        return sorted(self.root.glob("public_pons_shadow/runs/**/*_receipt.json"))

    def test_contiguous_receipts_verify_late_cold_coverage_not_live_timing(self):
        a=audit.audited(self.root,clock=lambda:NOW)
        self.assertEqual(a["cold_verified_receipt_count"],2)
        self.assertEqual((a["cold_verified_start_block"],a["cold_verified_end_block"]),(89,104))
        self.assertEqual(a["hot_gap_count"],2)
        self.assertEqual(a["hot_gaps_cold_historical_range_covered"],1)
        self.assertEqual(a["hot_gaps_cold_historical_range_uncovered"],1)
        self.assertFalse(a["source_first_known_verified"])
        self.assertFalse(a["hot_real_time_coverage_recovered"])
        self.assertFalse(a["canonical_private_source_admitted"])
        self.assertFalse(a["economic_edge_verified"])
        self.assertFalse(a["portfolio_action"])
        self.assertEqual(a["sha256"],cold.hash_obj({k:v for k,v in a.items() if k!="sha256"}))

    def test_missing_and_tampered_original_receipt_fails_closed(self):
        p=self.receipts()[0]
        original=p.read_bytes()
        p.unlink()
        with self.assertRaises(ValueError):
            audit.audited(self.root,clock=lambda:NOW)
        p.write_bytes(original)
        r=json.loads(p.read_text())
        r["from_block"]=42
        p.write_bytes(cold.encode(r))
        with self.assertRaisesRegex(ValueError,"COVERAGE_COLD_RECEIPT_OR_EVENTS_UNTRUSTED"):
            audit.audited(self.root,clock=lambda:NOW)

    def test_conflicting_but_rehashed_receipt_invalidates_hash_chain(self):
        p=self.receipts()[0]
        r=json.loads(p.read_text())
        r["to_block"]=97
        r["sha256"]=cold.hash_obj({k:v for k,v in r.items() if k!="sha256"})
        p.write_bytes(cold.encode(r))
        with self.assertRaisesRegex(ValueError,"COVERAGE_NONCONTIGUOUS_OR_UNLINKED_RECEIPT"):
            audit.audited(self.root,clock=lambda:NOW)

    def test_event_checksum_cannot_silently_hide_logs(self):
        p=self.receipts()[1]
        ep=p.with_name(p.name.replace("_receipt.json","_events.json"))
        events=json.loads(ep.read_text())
        events.pop()
        ep.write_bytes(cold.encode(events))
        with self.assertRaisesRegex(ValueError,"COVERAGE_COLD_RECEIPT_OR_EVENTS_UNTRUSTED"):
            audit.audited(self.root,clock=lambda:NOW)

    def test_cold_state_rewritten_even_with_self_hash_does_not_relink(self):
        p=self.root/cold.STATE
        x=json.loads(p.read_text())
        x["last_scanned_block"]=88
        x["sha256"]=cold.hash_obj({k:v for k,v in x.items() if k!="sha256"})
        p.write_bytes(cold.encode(x))
        with self.assertRaisesRegex(ValueError,"COVERAGE_FINAL_COLD_STATE_CHAIN_MISMATCH"):
            audit.audited(self.root,clock=lambda:NOW)

    def test_hot_gap_claims_cannot_be_falsified_without_hash(self):
        p=self.root/"public_pons_shadow/hot_state.json"
        x=json.loads(p.read_text())
        x["unobserved_gaps"][0]["from_block"]=999
        p.write_bytes(cold.encode(x))
        with self.assertRaisesRegex(ValueError,"COVERAGE_HOT_STATE_UNTRUSTED"):
            audit.audited(self.root,clock=lambda:NOW)

    def test_no_hot_state_reports_cold_completeness_without_fictitious_hot_success(self):
        (self.root/"public_pons_shadow/hot_state.json").unlink()
        a=audit.audited(self.root,clock=lambda:NOW)
        self.assertEqual(a["hot_gap_count"],0)
        self.assertIsNone(a["hot_final_state_hash"])
        self.assertFalse(a["hot_real_time_coverage_recovered"])

if __name__=="__main__":
    unittest.main()
