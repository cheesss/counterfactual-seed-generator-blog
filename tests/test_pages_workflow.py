from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"


class PagesWorkflowTests(unittest.TestCase):
    def test_validation_precedes_upload_and_deploy(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        stage = workflow.index("python scripts/stage_pages_artifact.py")
        validate = workflow.index("python scripts/site_image_validator.py")
        release = workflow.index("python scripts/site_release_validator.py")
        upload = workflow.index("actions/upload-pages-artifact@")
        deploy = workflow.index("actions/deploy-pages@")

        self.assertLess(stage, validate)
        self.assertLess(validate, release)
        self.assertLess(release, upload)
        self.assertLess(upload, deploy)
        self.assertIn("needs: validate", workflow)
        self.assertIn("pull_request:", workflow)
        self.assertIn("if: github.event_name != 'pull_request'", workflow)
        self.assertIn("path: _site", workflow)
        self.assertNotIn("path: .\n", workflow)

    def test_selected_repair_requires_independent_baseline_check(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("actions: read", workflow)
        self.assertIn("GH_TOKEN: ${{ github.token }}", workflow)
        self.assertIn("if [ -f _site/.publication-update.json ]; then", workflow)
        self.assertIn("python scripts/static_update_baseline.py --site-root _site --baseline-repo .", workflow)
        self.assertIn("python scripts/site_release_validator.py --site-root _site", workflow)
        self.assertLess(workflow.index("scripts/static_update_baseline.py"),
                        workflow.index("actions/upload-pages-artifact@"))


if __name__ == "__main__":
    unittest.main()
