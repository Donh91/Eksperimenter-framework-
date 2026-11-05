"""Adversarial, offline public-only forward sampler tests. Never request providers."""
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from public_pons_shadow import collector as cold
from public_pons_shadow import forward_outcome as fw

NOW = datetime(2026, 11, 5, 0, 15, tzinfo=timezone.utc)
A = "0x" + "a"*40
B = "0x" + "b"*40
POOL = "0x" + "c"*40
HASH = "0x" + "d"*64
SHA = "f"*40


def fake_source(root, *, seen=NOW-timedelta(minutes=20), poisoned=False):
    day = seen.strftime("%Y/%m/%d")
    folder = root / "public_pons_shadow/hot/runs" / day
    folder.mkdir(parents=True, exist_ok=True)
    events = []
    for idx, token in enumerate([A, B]):
        events.append(dict(block=100+idx, log_index=idx, tx_hash="0x"+f"{idx+1:064x}",
                           block_hash=HASH, data="0x",
                           topics=[cold.TOPIC0, "0x"+"0"*24+token[2:],
                                   "0x"+"0"*24+POOL[2:], "0x"+"0"*24+"e"*40]))
    rp = folder / (seen.strftime("%Y%m%dT%H%M%SZ")+"_123_receipt.json")
    ep = rp.with_name(rp.name.replace("_receipt.json", "_events.json"))
    rec = cold.signed(dict(contract="PUBLIC_PONS_V2_HOT_SHADOW_V1",
        authority="PUBLIC_HOT_SHADOW_ONLY", chain_id=cold.CHAIN_ID,
        factory=cold.FACTORY, topic0=cold.TOPIC0, from_block=100, to_block=102,
        event_count=len(events), events_sha256=cold.hash_obj(events),
        scan_finished_at_utc=cold.iso(seen), real_time_window_candidate=True,
        private_source_admitted=False,canonical_census_complete=False,
        portfolio_action=False,alert_authority=False))
    if poisoned:
        rec["private_source_admitted"] = True
    ep.write_bytes(cold.encode(events))
    rp.write_bytes(cold.encode(rec))
    return rp


class TestPublicForward(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reads = []

    def reader(self, token, pair):
        self.reads.append((token, pair))
        return dict(state="PUBLIC_SPOT_OBSERVED_NON_EXECUTABLE",
                    price_usd="0.001", pair=pair or POOL)

    def execute(self, now, run_id):
        return fw.run(self.root, run_id, SHA, clock=lambda: now, reader=self.reader)

    def state(self):
        return json.loads((self.root/fw.STATE).read_text())

    def test_new_public_t0_does_not_backdate_hot_receipt_or_grant_alpha(self):
        fake_source(self.root)
        out = self.execute(NOW, "101")
        self.assertEqual(out["event_count"], 4)
        self.assertEqual(out["source_calls"], 2)
        state = self.state()
        self.assertEqual(len(state["candidates"]), 2)
        self.assertEqual(self.reads, [(A, None), (B, None)])
        for c in state["candidates"]:
            self.assertEqual(c["t0_utc"], cold.iso(NOW))
            self.assertEqual(c["source_machine_observed_utc"], cold.iso(NOW-timedelta(minutes=20)))
            self.assertEqual(c["source_coverage"], "PARTIAL_PUBLIC_HOT")
            self.assertFalse(c["first_known_verified"])
            self.assertEqual(c["observations"]["BASELINE"]["state"], "PUBLIC_SPOT_OBSERVED_NON_EXECUTABLE")
            self.assertIsNone(c["observations"]["BASELINE"]["realizable_net_return"])
        self.assertEqual(self.execute(NOW+timedelta(minutes=30), "102")["status"], "NO_DUE_OR_ELIGIBLE_PUBLIC_SAMPLE")

    def test_due_24_and_72_hours_forward_and_original_t0_immutable(self):
        fake_source(self.root)
        self.execute(NOW, "101")
        orig = self.state()
        at24 = self.execute(NOW+timedelta(hours=24, minutes=13), "102")
        self.assertEqual(at24["source_calls"], 2)
        self.assertEqual(at24["event_count"], 2)
        at72 = self.execute(NOW+timedelta(hours=72, minutes=7), "103")
        self.assertEqual(at72["source_calls"], 2)
        state = self.state()
        self.assertEqual([set(x["observations"]) for x in state["candidates"]],
                         [{"BASELINE","24H","72H"}]*2)
        for i,c in enumerate(state["candidates"]):
            self.assertEqual(c["t0_utc"], orig["candidates"][i]["t0_utc"])
            self.assertEqual(c["observations"]["24H"]["pair"], POOL)
            self.assertFalse(c["observations"]["72H"]["execution_verified"])
        self.assertEqual(self.execute(NOW+timedelta(hours=73), "104")["status"], "NO_DUE_OR_ELIGIBLE_PUBLIC_SAMPLE")

    def test_late_windows_are_missed_not_loss(self):
        fake_source(self.root)
        self.execute(NOW, "101")
        out = self.execute(NOW+timedelta(hours=78), "102")
        self.assertEqual(out["source_calls"], 0)
        for c in self.state()["candidates"]:
            self.assertEqual(c["observations"]["24H"]["state"], "WINDOW_MISSED")
            self.assertEqual(c["observations"]["72H"]["state"], "WINDOW_MISSED")
            self.assertIsNone(c["observations"]["24H"]["price_usd"])

    def test_stale_and_untrusted_public_hot_cannot_seed(self):
        fake_source(self.root, seen=NOW-timedelta(hours=3))
        self.assertEqual(self.execute(NOW, "101")["status"], "NO_DUE_OR_ELIGIBLE_PUBLIC_SAMPLE")
        fake_source(self.root, poisoned=True)
        self.assertEqual(self.execute(NOW, "102")["status"], "NO_DUE_OR_ELIGIBLE_PUBLIC_SAMPLE")

    def test_provider_failure_is_unknown_and_no_future_price_forced(self):
        fake_source(self.root)
        def fail(token,pair):
            raise RuntimeError("HTTP_429")
        out=fw.run(self.root,"101",SHA,clock=lambda:NOW,reader=fail)
        self.assertEqual(out["source_calls"],2)
        self.assertTrue(all(c["observations"]["BASELINE"]["state"]=="UNKNOWN_PROVIDER_ERROR"
                            for c in self.state()["candidates"]))
        out=self.execute(NOW+timedelta(hours=24),"102")
        self.assertEqual(out["source_calls"],0)
        self.assertTrue(all(c["observations"]["24H"]["state"]=="UNKNOWN_NO_BASELINE_PAIR"
                            for c in self.state()["candidates"]))

    def test_corrupted_state_fails_without_changing_bytes(self):
        fake_source(self.root)
        self.execute(NOW, "101")
        f=self.root/fw.STATE
        obj=json.loads(f.read_text())
        obj["candidates"][0]["token"]="0x"+"0"*40
        f.write_text(json.dumps(obj))
        before=f.read_bytes()
        with self.assertRaisesRegex(ValueError, "FORWARD_PREVIOUS_STATE_INVALID"):
            self.execute(NOW+timedelta(hours=24), "102")
        self.assertEqual(f.read_bytes(),before)

    def test_duplicate_already_tracked_tokens_never_reselected_on_next_day(self):
        fake_source(self.root)
        self.execute(NOW,"101")
        fake_source(self.root,seen=NOW+timedelta(hours=24))
        self.assertEqual(self.execute(NOW+timedelta(hours=24,minutes=20),"102")["event_count"],2)
        self.assertEqual(len(self.state()["candidates"]),2)


    def test_same_run_new_hot_receipt_is_not_mistaken_for_published_source(self):
        original=fake_source(self.root,seen=NOW-timedelta(minutes=20))
        latest=fake_source(self.root,seen=NOW-timedelta(minutes=1))
        receipt=json.loads(latest.read_text())
        receipt["github_run_id"]="101"  # current run has not published HOT yet
        receipt["sha256"]=cold.hash_obj({k:v for k,v in receipt.items() if k!="sha256"})
        latest.write_bytes(cold.encode(receipt))
        self.execute(NOW,"101")
        for c in self.state()["candidates"]:
            self.assertEqual(c["source_receipt"],original.relative_to(self.root).as_posix())
            self.assertEqual(c["source_machine_observed_utc"],cold.iso(NOW-timedelta(minutes=20)))

    def test_post_window_provider_response_is_censored_at_completion_clock(self):
        fake_source(self.root)
        clock={"value":NOW}
        def timed():
            return clock["value"]
        def slow_provider(token,pair):
            clock["value"]=NOW+timedelta(hours=5)
            return {"state":"PUBLIC_SPOT_OBSERVED_NON_EXECUTABLE",
                    "price_usd":"999","pair":POOL}
        fw.run(self.root,"101",SHA,clock=timed,reader=slow_provider)
        for c in self.state()["candidates"]:
            self.assertEqual(c["observations"]["BASELINE"]["state"],"WINDOW_MISSED")
            self.assertIsNone(c["observations"]["BASELINE"]["price_usd"])

    def test_no_user_wallet_fields_ever_in_public_state(self):
        fake_source(self.root)
        self.execute(NOW,"101")
        txt=(self.root/fw.STATE).read_text().lower()
        for word in ("user_wallet", "position_size", "user_trade", "signing_key","private_key"):
            self.assertNotIn(word,txt)

if __name__=="__main__":
    unittest.main()
