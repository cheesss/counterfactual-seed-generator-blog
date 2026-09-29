from __future__ import annotations

import json
import tempfile
import unittest
import sys
import subprocess
import hashlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import static_update_baseline as baseline


class BaselineTrustTests(unittest.TestCase):
    def setUp(self):
        self.identity = {"repository": baseline.REPOSITORY, "commit": "a" * 40, "run_id": 123}
        self.run = {"head_sha": "a" * 40, "conclusion": "success", "status": "completed",
                    "event": "push", "path": ".github/workflows/pages.yml"}
        self.jobs = {"jobs": [{"name": "Deploy validated Pages artifact", "conclusion": "success"}]}

    def responses(self):
        return [f"https://github.com/{baseline.REPOSITORY}.git\n".encode(),
                json.dumps(self.run).encode(), json.dumps(self.jobs).encode(), b""]

    def test_successful_actual_deployment_is_required(self):
        with mock.patch.object(baseline, "command", side_effect=self.responses()):
            baseline.verify_deployment(Path("."), self.identity)

    def test_foreign_repo_and_failed_or_pr_run_do_not_authorize_archive(self):
        for field, value in (("head_sha", "b" * 40), ("conclusion", "failure"),
                             ("event", "pull_request"), ("path", "other.yml")):
            with self.subTest(field=field):
                original = self.run[field]
                self.run[field] = value
                with mock.patch.object(baseline, "command", side_effect=self.responses()), \
                        self.assertRaises(baseline.artifact.ReleaseFailure):
                    baseline.verify_deployment(Path("."), self.identity)
                self.run[field] = original
        with mock.patch.object(baseline, "command", return_value=b"https://github.com/other/repo.git"), \
                self.assertRaises(baseline.artifact.ReleaseFailure):
            baseline.verify_deployment(Path("."), self.identity)

    def test_success_without_a_deployment_job_is_rejected(self):
        self.jobs = {"jobs": [{"name": "Validate", "conclusion": "success"}]}
        with mock.patch.object(baseline, "command", side_effect=self.responses()), \
                self.assertRaises(baseline.artifact.ReleaseFailure):
            baseline.verify_deployment(Path("."), self.identity)

    def test_no_baseline_path_escape_or_hash_claim_adoption(self):
        for name in ("../escape", "/absolute", "assets/../../escape", "scripts/unsafe.py"):
            manifest = {"generator": "sync_static_site/v1", "schema_version": 1,
                        "files": [{"path": name, "size": 0, "sha256": "0" * 64}]}
            with tempfile.TemporaryDirectory() as temporary, \
                    mock.patch.object(baseline, "verify_deployment"), \
                    mock.patch.object(baseline, "command", return_value=json.dumps(manifest).encode()), \
                    self.assertRaises(baseline.artifact.ReleaseFailure):
                baseline.materialize_baseline(Path("."), self.identity, Path(temporary) / "site")

    def test_git_archive_preserves_committed_bytes_with_windows_autocrlf(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, output = root / "repo", root / "baseline"
            repo.mkdir()
            def git(*args):
                return subprocess.run(["git", *args], cwd=repo, check=True,
                                      capture_output=True).stdout.decode().strip()
            git("init", "-q")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.invalid")
            entries = []
            for name in ("index.html", "styles.css", "publication-data.json", "blog-data.json"):
                raw = b"fixture\nsecond line\n"
                (repo / name).write_bytes(raw)
                entries.append({"path": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
            (repo / baseline.MANIFEST).write_text(json.dumps({"generator": "sync_static_site/v1",
                "schema_version": 1, "files": entries}), encoding="utf-8")
            git("add", ".")
            git("commit", "-qm", "Synthetic baseline only")
            identity = {**self.identity, "commit": git("rev-parse", "HEAD")}
            git("config", "core.autocrlf", "true")
            with mock.patch.object(baseline, "verify_deployment"):
                baseline.materialize_baseline(repo, identity, output)
            for entry in entries:
                self.assertEqual((repo / entry["path"]).read_bytes(), (output / entry["path"]).read_bytes())


if __name__ == "__main__":
    unittest.main()
