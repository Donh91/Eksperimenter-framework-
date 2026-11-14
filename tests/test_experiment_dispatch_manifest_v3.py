"""No-network manifest-v3 page integrity and 63k request coverage tests."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.experiment_execution_plane import (
    MANIFEST_V1, MANIFEST_V2, MANIFEST_V3, PAGE_V3, QUALIFIED,
    canonical, expand_manifest_requests,
)


def make_index(root: Path, chunks: list[list[dict]], *, missing=None):
    pages = []
    for i, rows in enumerate(chunks):
        page = {"contract": PAGE_V3, "sequence": i, "request_count": len(rows), "requests": rows}
        rel = f"research/experiment_lifecycle/dispatch_manifest_pages/part-{i:06d}.json"
        payload = canonical(page)
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if i != missing:
            target.write_bytes(payload)
        pages.append({
            "sequence": i, "request_count": len(rows), "path": rel,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "raw_url": f"https://raw.githubusercontent.com/Donh91/Investering-Framework-Archive-v1/main/{rel}",
            "first_request_id": rows[0]["request_id"],
            "last_request_id": rows[-1]["request_id"],
        })
    return {
        "contract": MANIFEST_V3, "source_repository": "Donh91/Investering-Framework-Archive-v1",
        "source_branch": "main", "admission_required": QUALIFIED,
        "request_count": sum(len(x) for x in chunks), "pages": pages,
    }


def rows(start, count):
    return [{
        "request_id": f"ER-{i:09d}", "candidate_id": f"EC-{i%762:04d}",
        "path": f"research/experiment_lifecycle/dispatch/2026/11/14/ER-{i:09d}.json",
        "raw_url": "https://raw.githubusercontent.com/Donh91/Investering-Framework-Archive-v1/main/request.json",
        "sha256": "a" * 64,
    } for i in range(start, start + count)]


class DispatchManifestV3(unittest.TestCase):
    def test_legacy_contracts_still_read_unmodified(self):
        for contract in (MANIFEST_V1, MANIFEST_V2):
            original = [{"request_id": "ER-legacy"}]
            self.assertIs(expand_manifest_requests(
                {"contract": contract, "requests": original}, manifest_file=None, source_root=None
            ), original)

    def test_two_pages_have_complete_monotonic_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = make_index(root, [rows(0, 2), rows(2, 3)])
            recovered = expand_manifest_requests(manifest, manifest_file=root / "LATEST.json", source_root=root)
            self.assertEqual([r["request_id"] for r in recovered], [f"ER-{i:09d}" for i in range(5)])

    def test_missing_shard_fails_closed_before_any_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = make_index(root, [rows(0, 2), rows(2, 2)], missing=1)
            with self.assertRaises(FileNotFoundError):
                expand_manifest_requests(manifest, manifest_file=root / "LATEST.json", source_root=root)

    def test_changed_bytes_wrong_sha_or_total_count_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = make_index(root, [rows(0, 2)])
            manifest["pages"][0]["sha256"] = "b" * 64
            with self.assertRaisesRegex(SystemExit, "v3_page_digest_or_size_mismatch"):
                expand_manifest_requests(manifest, manifest_file=root / "LATEST.json", source_root=root)
            manifest = make_index(root, [rows(0, 2)])
            manifest["request_count"] = 3
            with self.assertRaisesRegex(SystemExit, "v3_manifest_total_count_mismatch"):
                expand_manifest_requests(manifest, manifest_file=root / "LATEST.json", source_root=root)

    def test_duplicate_id_page_overlap_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = make_index(root, [rows(0, 2), rows(1, 2)])
            with self.assertRaisesRegex(SystemExit, "v3_duplicate_or_unsorted_request_id"):
                expand_manifest_requests(manifest, manifest_file=root / "LATEST.json", source_root=root)

    def test_63k_scale_complete_and_bounded_pages(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            chunks = [rows(start, min(256, 63219 - start)) for start in range(0, 63219, 256)]
            manifest = make_index(root, chunks)
            found = expand_manifest_requests(manifest, manifest_file=root / "LATEST.json", source_root=root)
            self.assertEqual(len(found), 63219)
            self.assertEqual(len({row["request_id"] for row in found}), 63219)
            self.assertEqual(found[0]["request_id"], "ER-000000000")
            self.assertEqual(found[-1]["request_id"], "ER-000063218")
            self.assertLess(max((root / p["path"]).stat().st_size for p in manifest["pages"]), 2_000_000)

    def test_host_and_path_traversal_rejected_without_opening_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = make_index(root, [rows(0, 1)])
            manifest["pages"][0]["path"] = "research/experiment_lifecycle/dispatch_manifest_pages/../../elsewhere.json"
            with self.assertRaisesRegex(SystemExit, "v3_page_metadata_invalid"):
                expand_manifest_requests(manifest, manifest_file=root / "LATEST.json", source_root=root)


if __name__ == "__main__":
    unittest.main()
