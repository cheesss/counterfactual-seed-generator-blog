"""Keep one labelled GitHub issue current (notify-only), following forecast_due.py.

The issue is created only when there is something to act on, updated in place
on every run, reopened when items return and closed with a comment when the
check is clean. Nothing else in the repository is changed.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Callable

MAX_BODY = 60000  # GitHub rejects issue bodies above 65536 characters.

Runner = Callable[..., str]


def gh(*args: str, token: str | None = None) -> str:
    env = None if token is None else {**os.environ, "GH_TOKEN": token}
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


def clip(body: str) -> str:
    if len(body) <= MAX_BODY:
        return body
    return body[:MAX_BODY].rsplit("\n", 1)[0] + "\n\n(truncated; rerun the script locally for the full list)\n"


def publish_issue(body: str, *, open_items: bool, title: str, label: str, color: str, description: str,
                  clean_comment: str, runner: Runner = gh) -> str:
    body = clip(body)
    runner("label", "create", label, "--color", color, "--description", description, "--force")
    existing = json.loads(runner("issue", "list", "--label", label, "--state", "all", "--json", "number,state",
                                 "--limit", "1") or "[]")
    if existing:
        number = str(existing[0]["number"])
        runner("issue", "edit", number, "--body", body)
        if open_items and existing[0]["state"] != "OPEN":
            runner("issue", "reopen", number)
        elif not open_items and existing[0]["state"] == "OPEN":
            runner("issue", "close", number, "--comment", clean_comment)
        return number
    if not open_items:
        return ""
    return runner("issue", "create", "--title", title, "--label", label, "--body", body)
