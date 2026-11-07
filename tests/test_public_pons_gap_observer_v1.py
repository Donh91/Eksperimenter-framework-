"""Gap Intelligence regression: provenance, UNKNOWN, privacy and no side effects."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from public_pons_shadow import collector as cold
from public_pons_shadow import hot_collector as hot
from public_pons_shadow import gap_observer_v1 as observer
from tests.test_public_pons_hot import FakeRPC, NOW, SHA


class TestGapObserver(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.result_file = self.root / "result.json"
        self.asof = "2026-11-02T08:00:20Z"

    def capture(self, run="101", head=100):
        out = hot.hot_capture(self.root, cold.Client(FakeRPC(head=head)),
                              run, SHA, clock=lambda: NOW, window=12)
        self.result_file.write_text(json.dumps(out))
        return out

    def observe(self, run="101", event="schedule", schedule="2,7,12,17,22,32,37,42,47,52 * * * *", sha=SHA, asof=None):
        return observer.diagnose(
            self.root, self.result_file, run_id=run, checkout_sha=sha,
            event=event, schedule=schedule, as_of_utc=asof or self.asof)

    def test_binds_real_original_receipt_without_live_credit(self):
        self.capture()
        a = self.observe()
        self.assertEqual(a["status"], "BOUND_SOURCE_CAPTURE_NOT_LIVE_ADMISSION")
        self.assertEqual(a["source_last_observed_block"], 98)
        self.assertEqual(a["source_gap_range_count"], 0)
        self.assertIsNone(a["new_missed_hot_blocks"])
        self.assertEqual(a["source_scan_age_seconds"], 20)
        self.assertEqual(len(a["original_receipt_sha256"]), 64)
        self.assertEqual(a["schedule_group"], "HOT_ONLY")
        self.assertIsNone(a["exact_scheduled_slot_utc"])
        self.assertFalse(a["github_schedule_delivery_proven"])
        self.assertFalse(a["first_known_verified"])
        self.assertFalse(a["private_source_admitted"])
        self.assertFalse(a["public_site_admitted"])
        self.assertFalse(a["alert_authority"])
        serialized = json.dumps(a)
        self.assertNotIn("rpc_url", serialized)
        self.assertNotIn("transactionHash", serialized)
        self.assertNotIn("wallet", serialized)

    def test_gap_exact_range_and_no_backdated_first_known(self):
        self.capture(run="101", head=100)
        self.capture(run="102", head=200)
        a = self.observe(run="102")
        self.assertEqual(a["source_gap_range_count"], 1)
        self.assertEqual(a["new_missed_hot_blocks"], 88)
        self.assertEqual(a["source_last_observed_block"], 198)
        self.assertFalse(a["first_known_verified"])
        self.assertNotEqual(a["status"], "COMPLETE_LIVE")

    def test_missing_result_is_unknown_not_zero_launches(self):
        a = self.observe()
        self.assertEqual(a["status"], "UNKNOWN_CAPTURE_NOT_PRESENT")
        self.assertIsNone(a["source_last_observed_block"])
        self.assertIsNone(a["source_gap_range_count"])
        self.assertIsNone(a["original_receipt_sha256"])

    def test_no_new_head_not_marked_as_zero_launches(self):
        self.capture(run="101", head=100)
        self.capture(run="102", head=100)
        a = self.observe(run="102")
        self.assertEqual(a["status"], "NO_NEW_SAFE_BLOCK_NOT_ZERO_LAUNCHES")
        self.assertEqual(a["source_last_observed_block"], 98)
        self.assertIsNone(a["new_missed_hot_blocks"])
        self.assertIsNone(a["original_receipt_sha256"])

    def test_policy_schedule_and_job_binding(self):
        self.capture()
        for event,slot in (("schedule","5 * * * *"),
                           ("push","27 * * * *"),
                           ("pull_request","")):
            with self.subTest(event=event,slot=slot),self.assertRaises(ValueError):
                self.observe(event=event,schedule=slot)
        with self.assertRaises(ValueError):
            self.observe(sha="main")
        with self.assertRaises(ValueError):
            self.observe(run="100000000000000000000")

    def test_source_receipt_tamper_cannot_pass_self_hash(self):
        r = self.capture()
        path = self.root / r["receipt"]
        obj = json.loads(path.read_text())
        obj["event_count"] += 1
        path.write_text(json.dumps(obj))
        with self.assertRaisesRegex(ValueError,"source_receipt_unbound"):
            self.observe()

    def test_tampered_event_file_cannot_pass_digest(self):
        r = self.capture()
        path = (self.root / r["receipt"]).with_name(Path(r["receipt"]).name.replace("_receipt.json","_events.json"))
        events = json.loads(path.read_text())
        events[0]["block"] = 1
        path.write_text(json.dumps(events))
        with self.assertRaisesRegex(ValueError,"source_receipt_unbound"):
            self.observe()

    def test_original_hot_state_cannot_be_rewritten(self):
        self.capture()
        path=self.root/hot.STATE
        b=json.loads(path.read_text())
        b["last_observed_block"] = 300
        path.write_text(json.dumps(b))
        with self.assertRaisesRegex(ValueError,"source_state_not_bound"):
            self.observe()

    def test_symlinked_source_receipt_and_input_refused(self):
        r = self.capture()
        p = self.root / r["receipt"]
        genuine = p.read_bytes()
        p.unlink()
        sibling = self.root / "out-of-band.json"
        sibling.write_bytes(genuine)
        p.symlink_to(sibling)
        with self.assertRaisesRegex(ValueError,"unsafe_source_receipt_path"):
            self.observe()
        p.unlink()
        p.write_bytes(genuine)
        self.result_file.unlink()
        self.result_file.symlink_to(p)
        with self.assertRaisesRegex(ValueError,"missing_or_symlinked_input"):
            self.observe()

    def test_poisoned_result_no_external_read(self):
        self.capture()
        r=json.loads(self.result_file.read_text())
        r["receipt"]="../../../../private/keys"
        self.result_file.write_text(json.dumps(r))
        with self.assertRaisesRegex(ValueError,"untrusted_receipt_reference"):
            self.observe()

    def test_future_or_ambiguous_time_refused(self):
        self.capture()
        for clock in ("2026-11-02T07:59:59Z", "2026-11-02T09:00:00+01:00", "2026-13-02T08:00:00Z"):
            with self.subTest(clock=clock),self.assertRaises(ValueError):
                self.observe(asof=clock)

    def test_duplicate_key_and_poisoned_status_refused(self):
        self.capture()
        self.result_file.write_text('{"status":"NO_NEW_HOT_SAFE_BLOCK","status":"PUBLIC_HOT_CAPTURED_UNPUBLISHED"}')
        with self.assertRaisesRegex(ValueError,"duplicate_json_key"):
            self.observe()
        self.result_file.write_text('{"status":"LIVE_BUY"}')
        with self.assertRaisesRegex(ValueError,"unknown_source_result"):
            self.observe()

    def test_never_changes_existing_research_source_files(self):
        original = self.capture()
        tracked = [self.root/hot.STATE, self.root/original["receipt"]]
        before = [p.read_bytes() for p in tracked]
        self.observe()
        self.assertEqual(before,[p.read_bytes() for p in tracked])

    def test_all_three_supported_cron_groups_are_distinct(self):
        self.capture()
        for schedule,expected in observer.SCHEDULES.items():
            with self.subTest(schedule=schedule):
                a=self.observe(schedule=schedule)
                self.assertEqual(a["schedule_group"],expected)
                self.assertFalse(a["github_schedule_delivery_proven"])


if __name__ == "__main__":
    unittest.main()
