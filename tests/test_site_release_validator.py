from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import site_release_validator as release


class SiteReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "posts").mkdir()
        post = self.root / "posts/verified.html"
        post.write_text("<article>Fixture</article>\n", encoding="utf-8")
        (self.root / "index.html").write_text("Index", encoding="utf-8")
        (self.root / "publication-data.json").write_text(json.dumps({"articles": [
            {"slug": "verified", "url": "posts/verified.html"},
        ]}), encoding="utf-8")
        digest = release.sha256_file(post)
        self.proof = {
            "schema_version": release.SCHEMA_VERSION, "status": "publication-ready",
            "contract_sha256": "a" * 64, "required_stages": list(release.PUBLIC_STAGES),
            "files": release.artifact_files(self.root),
            "articles": [{"slug": "verified", "report_sha256": "b" * 64, "post_sha256": digest,
                          "stages": [{"stage": stage, "prompt_sha256": "c" * 64,
                                      "raw_sha256": "d" * 64, "section_sha256": "e" * 64}
                                     for stage in release.PUBLIC_STAGES],
                          "quality": {"status": "publishable", "report_sha256": "b" * 64,
                                      "post_sha256": digest, "review_sha256": "f" * 64,
                                      "blocking_issue_count": 0, "link_health_checked": True}}],
        }
        self.seal()

    def seal(self):
        self.proof.pop("proof_sha256", None)
        self.proof["proof_sha256"] = release.canonical_hash(self.proof)
        (self.root / release.RELEASE_FILE).write_text(json.dumps(self.proof), encoding="utf-8")

    def test_verified_inventory_is_accepted(self):
        self.assertEqual(1, release.validate_release(self.root)["articles"])

    def test_absent_proof_cannot_deploy(self):
        (self.root / release.RELEASE_FILE).unlink()
        with self.assertRaises(release.ReleaseFailure):
            release.validate_release(self.root)

    def test_built_html_drift_cannot_reuse_old_approval(self):
        (self.root / "posts/verified.html").write_text("Unreviewed edit", encoding="utf-8")
        with self.assertRaisesRegex(release.ReleaseFailure, "changed after verification"):
            release.validate_release(self.root)

    def test_partial_cycle_cannot_deploy_even_with_valid_proof_digest(self):
        self.proof["articles"][0]["stages"].pop()
        self.seal()
        with self.assertRaisesRegex(release.ReleaseFailure, "incomplete stage"):
            release.validate_release(self.root)

    def test_approval_must_include_live_link_check(self):
        self.proof["articles"][0]["quality"]["link_health_checked"] = False
        self.seal()
        with self.assertRaisesRegex(release.ReleaseFailure, "quality approval"):
            release.validate_release(self.root)


if __name__ == "__main__":
    unittest.main()
