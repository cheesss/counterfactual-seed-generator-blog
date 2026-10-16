#!/usr/bin/env python3
"""Weekly, notify-only check that GitHub settings still match the release boundary.

Repository files cannot enforce these settings, so this reads them back:

  Pages            build_type is `workflow` (only the validated artifact deploys)
  main protection  required check "Validate Pages artifact", enforced for
                   admins, force pushes and deletion blocked
  github-pages     deployments limited to protected branches

Pages and environment settings are read with the workflow token. Branch
protection needs an admin-read token in the SETTINGS_AUDIT_TOKEN secret; without
it that part is reported as skipped and the run still succeeds. With --issue it
keeps one GitHub issue current. It never changes a setting.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import issue_notify

DEFAULT_REPO = "cheesss/counterfactual-seed-generator-blog"
REQUIRED_CHECK = "Validate Pages artifact"
ENVIRONMENT = "github-pages"
SECRET = "SETTINGS_AUDIT_TOKEN"
ISSUE_TITLE = "Repository settings drift"
ISSUE_LABEL = "settings-drift"
SECRET_HELP = (
    f"Branch protection was not checked: the `{SECRET}` secret is not configured. To enable it, create a "
    "fine-grained personal access token limited to this repository with **Repository permissions > "
    f"Administration: Read-only** (nothing else), then add it under Settings > Secrets and variables > Actions "
    f"as `{SECRET}`."
)


class ApiError(RuntimeError):
    pass


@dataclass
class Check:
    area: str
    item: str
    expected: str
    actual: str
    ok: bool


Api = Callable[[str, "str | None"], Any]


def api(path: str, token: str | None = None) -> Any:
    env = None if token is None else {**os.environ, "GH_TOKEN": token}
    result = subprocess.run(["gh", "api", path], capture_output=True, text=True, env=env, check=False)
    if result.returncode:
        raise ApiError((result.stderr or result.stdout).strip()[:200] or f"gh api exited {result.returncode}")
    return json.loads(result.stdout or "{}")


def enabled(value: Any) -> Any:
    return value.get("enabled") if isinstance(value, dict) else value


def required_checks(protection: dict) -> list[str]:
    status = protection.get("required_status_checks") or {}
    names = list(status.get("contexts") or [])
    names += [row.get("context") for row in status.get("checks") or [] if isinstance(row, dict)]
    return sorted({name for name in names if name})


def audit(repo: str, *, read: Api | None = None, admin_token: str | None = None) -> tuple[list[Check], list[str]]:
    read = read or api
    checks: list[Check] = []
    notes: list[str] = []

    def fetch(area: str, path: str, token: str | None = None) -> Any:
        try:
            return read(path, token)
        except (ApiError, ValueError) as exc:
            checks.append(Check(area, "readable", "settings can be read", f"could not read: {exc}", False))
            return None

    pages = fetch("Pages", f"repos/{repo}/pages")
    if pages is not None:
        build = pages.get("build_type")
        checks.append(Check("Pages", "build_type", "workflow", str(build), build == "workflow"))

    environment = fetch("github-pages environment", f"repos/{repo}/environments/{ENVIRONMENT}")
    if environment is not None:
        policy = environment.get("deployment_branch_policy") or {}
        protected = policy.get("protected_branches") is True and policy.get("custom_branch_policies") is False
        checks.append(Check("github-pages environment", "deployment branch policy", "protected branches only",
                            json.dumps(policy, sort_keys=True) if policy else "any branch", protected))
        if "can_admins_bypass" in environment:
            bypass = environment["can_admins_bypass"]
            checks.append(Check("github-pages environment", "can_admins_bypass", "false", str(bypass).lower(),
                                bypass is False))

    if not admin_token:
        notes.append(SECRET_HELP)
        return checks, notes
    protection = fetch("main protection", f"repos/{repo}/branches/main/protection", admin_token)
    if protection is not None:
        names = required_checks(protection)
        checks += [
            Check("main protection", "required status check", REQUIRED_CHECK, ", ".join(names) or "none",
                  REQUIRED_CHECK in names),
            Check("main protection", "enforce_admins", "true", str(enabled(protection.get("enforce_admins"))).lower(),
                  enabled(protection.get("enforce_admins")) is True),
            Check("main protection", "allow_force_pushes", "false",
                  str(enabled(protection.get("allow_force_pushes"))).lower(),
                  enabled(protection.get("allow_force_pushes")) is False),
            Check("main protection", "allow_deletions", "false",
                  str(enabled(protection.get("allow_deletions"))).lower(),
                  enabled(protection.get("allow_deletions")) is False),
        ]
    return checks, notes


def markdown(repo: str, checks: list[Check], notes: list[str]) -> str:
    drift = [check for check in checks if not check.ok]
    lines = ["## Repository settings audit", "",
             f"{len(checks)} setting(s) checked for `{repo}`; {len(drift)} drifted or unreadable.", "",
             "| Area | Setting | Expected | Actual | Result |", "| --- | --- | --- | --- | --- |"]
    lines += [f"| {check.area} | {check.item} | {check.expected} | {check.actual.replace('|', '/')} | "
              f"{'ok' if check.ok else '**drift**'} |" for check in checks]
    if notes:
        lines += [""] + notes
    lines += ["", "Notify-only: fix drift in the GitHub settings; this check never changes them."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO)
    parser.add_argument("--summary", type=Path, help="append the report to this file ($GITHUB_STEP_SUMMARY)")
    parser.add_argument("--issue", action="store_true", help="create/update the tracking issue with gh")
    args = parser.parse_args(argv)
    checks, notes = audit(args.repo, admin_token=os.environ.get(SECRET, "").strip() or None)
    body = markdown(args.repo, checks, notes)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write(body)
    else:
        sys.stdout.write(body)
    if args.issue:
        drift = any(not check.ok for check in checks)
        print(issue_notify.publish_issue(
            body, open_items=drift, title=ISSUE_TITLE, label=ISSUE_LABEL, color="b60205",
            description="GitHub settings differ from the Pages release boundary",
            clean_comment="Repository settings match the release boundary again.") or "no issue needed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
