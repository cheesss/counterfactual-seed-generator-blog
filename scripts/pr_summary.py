#!/usr/bin/env python3
"""Summarize a proposed Pages change for the pull-request step summary.

Reports the proof type (.publication-release.json or .publication-update.json),
proof article counts by profile (including carried legacy-deployed-v1 articles
and photo-revalidated ones), changed posts, image count, and the open/dated
watch-signal deltas against the base commit. Informational only: it never
validates, approves or changes anything; the validators decide.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

RELEASE = ".publication-release.json"
UPDATE = ".publication-update.json"
LEGACY_PROFILE = "legacy-deployed-v1"
PHOTO_REVALIDATED = "text-unchanged-photo-revalidated"
STATUS_NAMES = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "copied", "T": "type changed"}


def read_json(repo: Path, path: str, ref: str | None = None) -> Any:
    """Read JSON from the working tree, or from `ref` when given; None when absent or unreadable."""
    try:
        if ref is None:
            text = (repo / path).read_text(encoding="utf-8")
        else:
            result = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=repo, capture_output=True, check=False)
            if result.returncode:
                return None
            text = result.stdout.decode("utf-8")
        return json.loads(text)
    except (OSError, ValueError):
        return None


def proof(repo: Path, ref: str | None = None) -> dict[str, Any]:
    release, update = read_json(repo, RELEASE, ref), read_json(repo, UPDATE, ref)
    if isinstance(release, dict):
        kind, records = f"complete release (`{RELEASE}`)", release.get("articles") or []
    elif isinstance(update, dict):
        kind, records = f"selected update (`{UPDATE}`)", (update.get("selected_release") or {}).get("articles") or []
    else:
        kind, records = "none", []
    records = [row for row in records if isinstance(row, dict)]
    profiles = Counter(str(row.get("profile", "unknown")) for row in records)
    photo = sum(row.get("profile") == LEGACY_PROFILE
                and (row.get("attestation") or {}).get("public_content") == PHOTO_REVALIDATED for row in records)
    return {"kind": kind, "articles": len(records), "profiles": dict(sorted(profiles.items())),
            "legacy": profiles.get(LEGACY_PROFILE, 0), "photo_revalidated": photo}


def signals(data: Any) -> dict[str, int]:
    articles = data.get("articles", []) if isinstance(data, dict) else []
    rows = [signal for article in articles for signal in article.get("watch_signals", []) or []]
    is_open = [signal.get("state", "open") == "open" for signal in rows]
    dated = [bool(signal.get("resolve_by")) for signal in rows]
    return {"articles": len(articles), "signals": len(rows), "open": sum(is_open), "dated": sum(dated),
            "open_dated": sum(o and d for o, d in zip(is_open, dated))}


def images(manifest: Any) -> int:
    return len(manifest.get("images", [])) if isinstance(manifest, dict) else 0


def changed_posts(repo: Path, base: str, head: str = "HEAD") -> list[tuple[str, str]]:
    result = subprocess.run(["git", "diff", "--name-status", "-z", base, head, "--", "posts/"], cwd=repo,
                            capture_output=True, check=False)
    if result.returncode:
        return []
    parts = [part for part in result.stdout.decode("utf-8").split("\0") if part]
    rows, index = [], 0
    while index < len(parts):
        status = parts[index]
        width = 3 if status[:1] in {"R", "C"} else 2
        rows.append((STATUS_NAMES.get(status[:1], status), parts[index + width - 1]))
        index += width
    return rows


def delta(head: int, base: int | None) -> str:
    if base is None:
        return f"{head}"
    change = head - base
    return f"{head} ({'+' if change >= 0 else ''}{change})"


def collect(repo: Path, base: str | None) -> dict[str, Any]:
    head_data = read_json(repo, "publication-data.json")
    base_data = read_json(repo, "publication-data.json", base) if base else None
    return {
        "proof": proof(repo), "base_proof": proof(repo, base) if base else None,
        "signals": signals(head_data), "base_signals": signals(base_data) if base_data is not None else None,
        "images": images(read_json(repo, "image-manifest.json")),
        "base_images": images(read_json(repo, "image-manifest.json", base)) if base else None,
        "posts": changed_posts(repo, base) if base else [],
    }


def markdown(summary: dict[str, Any]) -> str:
    head, base = summary["proof"], summary["base_proof"]
    sig, base_sig = summary["signals"], summary["base_signals"] or {}
    profiles = ", ".join(f"{name}: {count}" for name, count in head["profiles"].items()) or "none"
    lines = ["## Proposed Pages change", "",
             "| Item | Value |", "| --- | --- |",
             f"| Proof type | {head['kind']}" + (f" (base: {base['kind']})" if base and base["kind"] != head["kind"]
                                                  else "") + " |",
             f"| Proof articles | {head['articles']} ({profiles}) |",
             f"| Carried `{LEGACY_PROFILE}` | {head['legacy']} ({head['photo_revalidated']} photo-revalidated) |",
             f"| Articles in publication-data.json | {delta(sig['articles'], base_sig.get('articles'))} |",
             f"| Images in image-manifest.json | {delta(summary['images'], summary['base_images'])} |",
             f"| Watch signals | {delta(sig['signals'], base_sig.get('signals'))} |",
             f"| Open watch signals | {delta(sig['open'], base_sig.get('open'))} |",
             f"| Dated watch signals | {delta(sig['dated'], base_sig.get('dated'))} |",
             f"| Open and dated | {delta(sig['open_dated'], base_sig.get('open_dated'))} |",
             f"| Changed posts | {len(summary['posts'])} |", ""]
    if summary["posts"]:
        lines += ["<details><summary>Changed posts</summary>", ""]
        lines += [f"- {status}: `{path}`" for status, path in summary["posts"][:200]]
        lines += ["", "</details>", ""]
    lines.append("Informational only; the validation steps above decide whether this change can deploy.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--base", default="", help="base commit (github.event.pull_request.base.sha)")
    parser.add_argument("--output", type=Path, help="append to this file (e.g. $GITHUB_STEP_SUMMARY)")
    args = parser.parse_args(argv)
    body = markdown(collect(args.repo.resolve(), args.base or None))
    if args.output:
        with args.output.open("a", encoding="utf-8") as handle:
            handle.write(body)
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
