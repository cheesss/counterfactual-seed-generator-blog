#!/usr/bin/env python3
"""Compare the deployed Pages site with the manifest of the pushed commit.

Runs after `deploy` on a push to main. It fetches every text file listed in
`.generated-site-manifest.json` plus every binary file changed by the pushed
commit range from the deployed page URL and compares SHA-256 digests with the
manifest. Files that still differ are retried with backoff (CDN propagation)
until the deadline; anything still different then fails the run.

Ported from the vault's `scripts/release_handoff.py` (`live_mismatches`). It
only reads: no commit, deployment or settings change.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Iterable

SITE_URL = "https://cheesss.github.io/counterfactual-seed-generator-blog"
MANIFEST = ".generated-site-manifest.json"
TEXT_SUFFIXES = {".html", ".json", ".xml", ".css", ".js", ".svg", ".txt"}
USER_AGENT = "BottleneckBriefsBot (+https://github.com/cheesss/counterfactual-seed-generator-blog)"
ZERO_SHA = "0" * 40

Fetcher = Callable[[str], bytes]


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "Pragma": "no-cache",
                                                   "User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def is_text(path: str) -> bool:
    return Path(path).suffix.lower() in TEXT_SUFFIXES


def live_url(site_url: str, path: str, nonce: str = "") -> str:
    url = site_url.rstrip("/") + "/" + urllib.parse.quote(path)
    return f"{url}?verify={urllib.parse.quote(nonce)}" if nonce else url


def selected_rows(manifest: dict, changed: Iterable[str]) -> list[dict]:
    """Every manifest text file, plus manifest binaries changed by the push."""
    changed = set(changed)
    return [row for row in manifest["files"] if is_text(row["path"]) or row["path"] in changed]


def live_mismatches(rows: Iterable[dict], *, site_url: str, fetcher: Fetcher = fetch,
                    nonce: str = "") -> dict[str, str]:
    """Return {path: problem} for rows whose live bytes differ from the manifest."""
    problems: dict[str, str] = {}
    for row in rows:
        path = row["path"]
        try:
            digest = hashlib.sha256(fetcher(live_url(site_url, path, nonce))).hexdigest()
        except (urllib.error.URLError, OSError, ValueError) as exc:
            problems[path] = f"fetch failed ({exc})"
            continue
        if digest != row["sha256"]:
            problems[path] = f"live sha256 {digest[:12]} != manifest {row['sha256'][:12]}"
    return problems


def verify(rows: list[dict], *, site_url: str, max_wait: float, fetcher: Fetcher = fetch,
           sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
           nonce: str = "", log: Callable[[str], None] = print) -> dict[str, str]:
    """Retry only the still-mismatched rows with backoff until they match or the deadline passes."""
    deadline = clock() + max_wait
    pending = list(rows)
    delay, attempt = 15.0, 0
    while True:
        attempt += 1
        problems = live_mismatches(pending, site_url=site_url, fetcher=fetcher, nonce=f"{nonce}-{attempt}")
        log(f"attempt {attempt}: {len(pending) - len(problems)}/{len(pending)} pending file(s) match")
        if not problems:
            return {}
        pending = [row for row in pending if row["path"] in problems]
        remaining = deadline - clock()
        if remaining <= 0:
            return problems
        sleep(min(delay, remaining))
        delay = min(delay * 2, 120.0)


def changed_paths(before: str, after: str, repo: Path) -> set[str]:
    """Paths changed by the pushed range; falls back to the last commit when `before` is unusable."""
    base = before if before and before != ZERO_SHA else ""
    if base:
        known = subprocess.run(["git", "cat-file", "-e", f"{base}^{{commit}}"], cwd=repo, capture_output=True)
        if known.returncode:
            base = ""
    if not base:
        base = f"{after}~1"
    result = subprocess.run(["git", "diff", "--name-only", "-z", base, after], cwd=repo, capture_output=True,
                            check=True)
    return {name for name in result.stdout.decode("utf-8").split("\0") if name}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site-url", default="", help="deployed page_url (default: the public site)")
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--before", default="", help="commit before the push (github.event.before)")
    parser.add_argument("--after", default="HEAD", help="pushed commit (github.sha)")
    parser.add_argument("--max-wait", type=float, default=600.0, help="seconds to wait for CDN propagation")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    site_url = (args.site_url or SITE_URL).rstrip("/")
    manifest = json.loads((repo / MANIFEST).read_text(encoding="utf-8"))
    changed = changed_paths(args.before, args.after, repo)
    rows = selected_rows(manifest, changed)
    binaries = sum(not is_text(row["path"]) for row in rows)
    print(f"checking {len(rows) - binaries} text file(s) and {binaries} changed binary file(s) at {site_url}")
    problems = verify(rows, site_url=site_url, max_wait=args.max_wait, nonce=args.after[:12])
    if problems:
        print(f"live site differs from the deployed manifest ({len(problems)} file(s)):", file=sys.stderr)
        for path, problem in sorted(problems.items())[:60]:
            print(f"  {path}: {problem}", file=sys.stderr)
        return 1
    print("live site matches the deployed manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
