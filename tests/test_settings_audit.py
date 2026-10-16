from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import issue_notify
import settings_audit

REPO = "owner/repo"
GOOD = {
    f"repos/{REPO}/pages": {"build_type": "workflow"},
    f"repos/{REPO}/environments/github-pages": {
        "can_admins_bypass": False,
        "deployment_branch_policy": {"protected_branches": True, "custom_branch_policies": False}},
    f"repos/{REPO}/branches/main/protection": {
        "required_status_checks": {"contexts": ["Validate Pages artifact"],
                                   "checks": [{"context": "Validate Pages artifact", "app_id": 15368}]},
        "enforce_admins": {"enabled": True},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False}},
}


def reader(responses, calls=None):
    def read(path, token):
        if calls is not None:
            calls.append((path, token))
        value = responses[path]
        if isinstance(value, Exception):
            raise value
        return value
    return read


class SettingsAuditTests(unittest.TestCase):
    def test_matching_settings_pass_and_protection_uses_the_admin_token(self):
        calls = []
        checks, notes = settings_audit.audit(REPO, read=reader(GOOD, calls), admin_token="secret")
        self.assertEqual([], [check for check in checks if not check.ok])
        self.assertEqual(7, len(checks))
        self.assertEqual([], notes)
        self.assertEqual({f"repos/{REPO}/pages": None, f"repos/{REPO}/environments/github-pages": None,
                          f"repos/{REPO}/branches/main/protection": "secret"}, dict(calls))

    def test_each_drift_is_reported(self):
        bad = dict(GOOD)
        bad[f"repos/{REPO}/pages"] = {"build_type": "legacy"}
        bad[f"repos/{REPO}/environments/github-pages"] = {"can_admins_bypass": True,
                                                         "deployment_branch_policy": None}
        bad[f"repos/{REPO}/branches/main/protection"] = {
            "required_status_checks": {"contexts": ["other"]}, "enforce_admins": {"enabled": False},
            "allow_force_pushes": {"enabled": True}, "allow_deletions": {"enabled": True}}
        checks, _ = settings_audit.audit(REPO, read=reader(bad), admin_token="secret")
        drift = {check.item for check in checks if not check.ok}
        self.assertEqual({"build_type", "deployment branch policy", "can_admins_bypass", "required status check",
                          "enforce_admins", "allow_force_pushes", "allow_deletions"}, drift)
        body = settings_audit.markdown(REPO, checks, [])
        self.assertIn("7 drifted or unreadable", body)
        self.assertIn("| main protection | required status check | Validate Pages artifact | other | **drift** |",
                      body)

    def test_missing_secret_skips_protection_and_explains_how_to_add_it(self):
        calls = []
        checks, notes = settings_audit.audit(REPO, read=reader(GOOD, calls), admin_token=None)
        self.assertNotIn(f"repos/{REPO}/branches/main/protection", [path for path, _ in calls])
        self.assertTrue(all(check.ok for check in checks))
        self.assertIn("SETTINGS_AUDIT_TOKEN", notes[0])
        self.assertIn("Administration: Read-only", notes[0])

    def test_unreadable_settings_are_reported_not_raised(self):
        responses = dict(GOOD)
        responses[f"repos/{REPO}/branches/main/protection"] = settings_audit.ApiError("HTTP 403")
        checks, _ = settings_audit.audit(REPO, read=reader(responses), admin_token="expired")
        failed = [check for check in checks if not check.ok]
        self.assertEqual(1, len(failed))
        self.assertIn("could not read: HTTP 403", failed[0].actual)

    def test_main_succeeds_without_the_secret_and_writes_the_step_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            summary = Path(temp) / "summary.md"
            with mock.patch.dict(settings_audit.os.environ, {"SETTINGS_AUDIT_TOKEN": ""}), \
                    mock.patch.object(settings_audit, "api", side_effect=reader(GOOD)), \
                    mock.patch.object(issue_notify, "publish_issue", return_value="") as publish, \
                    mock.patch("sys.stdout"):
                self.assertEqual(0, settings_audit.main(["--repo", REPO, "--summary", str(summary), "--issue"]))
            text = summary.read_text(encoding="utf-8")
        self.assertIn("Branch protection was not checked", text)
        self.assertFalse(publish.call_args.kwargs["open_items"])
        self.assertEqual("settings-drift", publish.call_args.kwargs["label"])

    def test_workflow_is_weekly_notify_only_with_an_optional_secret(self):
        workflow = (ROOT / ".github/workflows/settings-audit.yml").read_text(encoding="utf-8")
        for needle in ("schedule:", "workflow_dispatch:", "issues: write", "pages: read",
                       "SETTINGS_AUDIT_TOKEN: ${{ secrets.SETTINGS_AUDIT_TOKEN }}",
                       'python scripts/settings_audit.py --issue --summary "$GITHUB_STEP_SUMMARY"'):
            self.assertIn(needle, workflow)
        for forbidden in ("contents: write", "pages: write", "administration", "pull_request", "git push"):
            self.assertNotIn(forbidden, workflow)


if __name__ == "__main__":
    unittest.main()
