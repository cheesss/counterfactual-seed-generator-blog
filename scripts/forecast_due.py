#!/usr/bin/env python3
"""List watch signals whose dated checkpoint has passed but that are still open.

Notify-only: it reads the deployed publication-data.json and, with --issue,
creates or updates one GitHub issue. It never researches, settles or edits a
forecast; settlement stays a sourced editorial decision in the source vault.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

SITE_URL = "https://cheesss.github.io/counterfactual-seed-generator-blog"
ISSUE_TITLE = "Forecast checkpoints due"
ISSUE_LABEL = "forecast-due"


def due_signals(data: dict, today: dt.date) -> list[dict]:
    rows = []
    for article in data.get("articles", []):
        for signal in article.get("watch_signals", []):
            resolve_by = signal.get("resolve_by")
            if not resolve_by or signal.get("state", "open") != "open":
                continue
            try:
                deadline = dt.date.fromisoformat(resolve_by)
            except ValueError:
                continue
            if deadline <= today:
                rows.append({"slug": article["slug"], "title": article.get("title", article["slug"]),
                             "url": f"{SITE_URL}/{article.get('url', '')}", "deadline": resolve_by,
                             "phrase": signal.get("resolve_by_text", ""), "signal_id": signal.get("signal_id", ""),
                             "text": signal.get("text", "")})
    return sorted(rows, key=lambda row: (row["deadline"], row["slug"]))


def summary(data: dict, today: dt.date) -> dict:
    signals = [signal for article in data.get("articles", []) for signal in article.get("watch_signals", [])]
    return {"signals": len(signals), "dated": sum(bool(signal.get("resolve_by")) for signal in signals),
            "open": sum(signal.get("state", "open") == "open" for signal in signals), "as_of": today.isoformat()}


def markdown(rows: list[dict], stats: dict) -> str:
    lines = [f"Checked {stats['as_of']}: {stats['signals']} watch signals, {stats['dated']} with a dated "
             f"checkpoint, {stats['open']} still open.", ""]
    if not rows:
        lines.append("No open signal has passed its checkpoint." if stats["dated"] else
                     "No watch signal carries a structured `resolve_by` date yet; the next complete release adds them.")
    else:
        lines += [f"{len(rows)} open signal(s) have passed their checkpoint and need a sourced settlement "
                  "(confirmed, weakened or broken) or an explicit extension in the vault outcome ledger:", ""]
        for row in rows:
            lines.append(f"- [ ] **{row['deadline']}** ({row['phrase']}) [{row['title']}]({row['url']}) - "
                         f"{row['text']} `{row['signal_id']}`")
    lines += ["", "Settle through the vault (`outcome-ledger.json`) with a public source; this issue only notifies."]
    return "\n".join(lines) + "\n"


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout.strip()


def publish_issue(body: str, *, open_items: bool) -> str:
    gh("label", "create", ISSUE_LABEL, "--color", "fbca04", "--description",
       "Watch signals past their dated checkpoint", "--force")
    existing = json.loads(gh("issue", "list", "--label", ISSUE_LABEL, "--state", "all", "--json", "number,state",
                             "--limit", "1") or "[]")
    if existing:
        number = str(existing[0]["number"])
        gh("issue", "edit", number, "--body", body)
        if open_items and existing[0]["state"] != "OPEN":
            gh("issue", "reopen", number)
        elif not open_items and existing[0]["state"] == "OPEN":
            gh("issue", "close", number, "--comment", "No open signal is past its checkpoint.")
        return number
    if not open_items:
        return ""
    return gh("issue", "create", "--title", ISSUE_TITLE, "--label", ISSUE_LABEL, "--body", body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("publication-data.json"))
    parser.add_argument("--today", type=dt.date.fromisoformat, default=dt.datetime.now(dt.timezone.utc).date())
    parser.add_argument("--issue", action="store_true", help="create/update the tracking issue with gh")
    args = parser.parse_args(argv)
    data = json.loads(args.data.read_text(encoding="utf-8"))
    rows = due_signals(data, args.today)
    body = markdown(rows, summary(data, args.today))
    if args.issue:
        print(publish_issue(body, open_items=bool(rows)) or "no issue needed")
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
