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
        upload = workflow.index("actions/upload-pages-artifact@")
        deploy = workflow.index("actions/deploy-pages@")

        self.assertLess(stage, validate)
        self.assertLess(validate, upload)
        self.assertLess(upload, deploy)
        self.assertIn("needs: validate", workflow)
        self.assertIn("pull_request:", workflow)
        self.assertIn("if: github.event_name != 'pull_request'", workflow)
        self.assertIn("path: _site", workflow)
        self.assertNotIn("path: .\n", workflow)


if __name__ == "__main__":
    unittest.main()
