from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import pr_summary

LEGACY = {"slug": "a", "profile": "legacy-deployed-v1", "attestation": {"public_content": "unchanged-since-deployment"}}
PHOTO = {"slug": "b", "profile": "legacy-deployed-v1",
         "attestation": {"public_content": "text-unchanged-photo-revalidated"}}
INTEGRATED = {"slug": "c", "profile": "integrated-v1"}


def data(*signal_sets):
    return {"articles": [{"slug": f"s{i}", "watch_signals": list(signals)} for i, signals in enumerate(signal_sets)]}


class PrSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.test")
        self.git("config", "user.name", "t")
        self.git("config", "core.autocrlf", "false")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, capture_output=True, text=True,
                              check=True).stdout.strip()

    def write(self, path, value):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def test_release_proof_counts_profiles_legacy_and_photo_revalidated(self):
        self.write(".publication-release.json", {"articles": [LEGACY, PHOTO, INTEGRATED]})
        result = pr_summary.proof(self.repo)
        self.assertEqual("complete release (`.publication-release.json`)", result["kind"])
        self.assertEqual((3, 2, 1), (result["articles"], result["legacy"], result["photo_revalidated"]))
        self.assertEqual({"integrated-v1": 1, "legacy-deployed-v1": 2}, result["profiles"])

    def test_update_proof_and_missing_proof(self):
        self.assertEqual("none", pr_summary.proof(self.repo)["kind"])
        self.write(".publication-update.json", {"selected_release": {"articles": [INTEGRATED]}})
        result = pr_summary.proof(self.repo)
        self.assertIn(".publication-update.json", result["kind"])
        self.assertEqual((1, 0), (result["articles"], result["legacy"]))

    def test_summary_against_base_reports_deltas_and_changed_posts(self):
        self.write(".publication-release.json", {"articles": [LEGACY]})
        self.write("publication-data.json", data([{"state": "open", "resolve_by": "2026-01-01"}]))
        self.write("image-manifest.json", {"images": [{}, {}]})
        self.write("posts/old.html", "old")
        self.write("posts/keep.html", "keep")
        base = self.commit("base")
        (self.repo / ".publication-release.json").unlink()
        self.write(".publication-update.json", {"selected_release": {"articles": [INTEGRATED]}})
        self.write("publication-data.json", data([{"state": "confirmed", "resolve_by": "2026-01-01"}],
                                                 [{"state": "open"}, {"state": "open", "resolve_by": "2027-01-01"}]))
        self.write("image-manifest.json", {"images": [{}, {}, {}]})
        (self.repo / "posts/old.html").unlink()
        self.write("posts/keep.html", "changed")
        self.write("posts/new.html", "new")
        self.write("README.md", "not a post")
        self.commit("head")
        summary = pr_summary.collect(self.repo, base)
        self.assertEqual({("deleted", "posts/old.html"), ("modified", "posts/keep.html"),
                          ("added", "posts/new.html")}, set(summary["posts"]))
        body = pr_summary.markdown(summary)
        self.assertIn("selected update (`.publication-update.json`) (base: complete release", body)
        self.assertIn("| Articles in publication-data.json | 2 (+1) |", body)
        self.assertIn("| Images in image-manifest.json | 3 (+1) |", body)
        self.assertIn("| Open watch signals | 2 (+1) |", body)
        self.assertIn("| Dated watch signals | 2 (+1) |", body)
        self.assertIn("| Open and dated | 1 (+0) |", body)
        self.assertIn("| Changed posts | 3 |", body)
        self.assertIn("- added: `posts/new.html`", body)

    def test_main_appends_to_the_step_summary(self):
        self.write("publication-data.json", data())
        out = self.repo / "summary.md"
        out.write_text("existing\n", encoding="utf-8")
        self.assertEqual(0, pr_summary.main(["--repo", str(self.repo), "--output", str(out)]))
        text = out.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("existing\n## Proposed Pages change"))
        self.assertIn("| Proof type | none |", text)

    def test_pages_workflow_writes_the_summary_without_gating_the_check(self):
        workflow = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        step = workflow[workflow.index("      - name: Summarize the proposed change"):]
        step = step[:step.index("\n\n")] if "\n\n" in step else step
        self.assertIn("if: always() && github.event_name == 'pull_request'", step)
        self.assertIn("continue-on-error: true", step)
        self.assertIn("BASE_SHA: ${{ github.event.pull_request.base.sha }}", step)
        self.assertIn('python scripts/pr_summary.py --base "$BASE_SHA" --output "$GITHUB_STEP_SUMMARY"', step)
        self.assertLess(workflow.index("Summarize the proposed change"), workflow.index("\n  deploy:\n"))


if __name__ == "__main__":
    unittest.main()
