from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
# One pinned major per action across every workflow.
PINNED = {
    "actions/checkout": "v4",
    "actions/setup-python": "v5",
    "actions/configure-pages": "v5",
    "actions/upload-pages-artifact": "v3",
    "actions/deploy-pages": "v4",
}


class WorkflowActionTests(unittest.TestCase):
    def test_every_action_uses_the_pinned_version(self) -> None:
        self.assertGreaterEqual(len(WORKFLOWS), 4)
        for workflow in WORKFLOWS:
            for action, version in re.findall(r"uses: ([\w./-]+)@(\S+)", workflow.read_text(encoding="utf-8")):
                self.assertIn(action, PINNED, f"{workflow.name}: unexpected action {action}")
                self.assertEqual(PINNED[action], version, f"{workflow.name}: {action}@{version}")

    def test_scheduled_workflows_are_notify_only(self) -> None:
        for workflow in WORKFLOWS:
            text = workflow.read_text(encoding="utf-8")
            if "schedule:" not in text:
                continue
            self.assertIn("issues: write", text, workflow.name)
            for forbidden in ("contents: write", "pages: write", "id-token: write", "deploy-pages",
                              "upload-pages-artifact", "git push", "git commit", "gh pr ", "gh workflow run"):
                self.assertNotIn(forbidden, text, f"{workflow.name} must stay notify-only")

    def test_workflows_parse_when_yaml_is_available(self) -> None:
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed")
        for workflow in WORKFLOWS:
            data = yaml.safe_load(workflow.read_text(encoding="utf-8"))
            self.assertIn("jobs", data, workflow.name)
            self.assertIn("on", data, workflow.name)


if __name__ == "__main__":
    unittest.main()
