"""Verify a release artifact without access to private research runtime files."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

RELEASE_FILE = ".publication-release.json"
SCHEMA_VERSION = 1
# This deployment policy is deliberately pinned. Its contract-equivalence test
# must change with an explicitly reviewed public-cycle contract update.
PUBLIC_STAGES = (
    "claim-audit", "thesis-deepening", "commercialization-expansion",
    "value-migration-map", "forecast-calibration", "verification-questions",
    "independent-evidence", "adversarial-verification",
    "chart-provenance-repair", "codex-blog-editor",
)
ARTIFACT_ROOT_FILES = frozenset({
    ".nojekyll", "404.html", "about.html", "blog-data.json",
    "disclaimer.html", "favicon.svg", "feed.xml", "index.html",
    "ledger.html", "patterns.html", "privacy.html", "publication-data.json",
    "rgraph.js", "site.js", "sitemap.xml", "styles.css",
})


class ReleaseFailure(ValueError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("utf-8")).hexdigest()


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ReleaseFailure(f"invalid SHA-256: {label}")
    return value


def artifact_files(site_root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in sorted(ARTIFACT_ROOT_FILES):
        path = site_root / name
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise ReleaseFailure(f"artifact is not a regular file: {name}")
            result[name] = sha256_file(path)
    for directory in ("posts", "assets"):
        root = site_root / directory
        if root.is_symlink():
            raise ReleaseFailure(f"artifact directory is a symbolic link: {directory}")
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ReleaseFailure(f"artifact contains a symbolic link: {path.name}")
            if path.is_file():
                result[path.relative_to(site_root).as_posix()] = sha256_file(path)
    return result


def validate_release(site_root: Path) -> dict[str, Any]:
    """Reject absent, partial, stale, or mixed-version publication evidence."""
    site_root = site_root.resolve()
    for name in (RELEASE_FILE, "publication-data.json"):
        if (site_root / name).is_symlink():
            raise ReleaseFailure(f"publication metadata is a symbolic link: {name}")
    try:
        proof = json.loads((site_root / RELEASE_FILE).read_text(encoding="utf-8"))
        data = json.loads((site_root / "publication-data.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReleaseFailure(f"missing or unreadable publication proof: {exc}") from exc
    if not isinstance(proof, dict) or proof.get("schema_version") != SCHEMA_VERSION:
        raise ReleaseFailure("unsupported publication proof schema")
    if proof.get("status") != "publication-ready":
        raise ReleaseFailure("artifact has not completed the publication cycle")
    unsigned = {key: value for key, value in proof.items() if key != "proof_sha256"}
    if proof.get("proof_sha256") != canonical_hash(unsigned):
        raise ReleaseFailure("publication proof hash drift")
    if proof.get("required_stages") != list(PUBLIC_STAGES):
        raise ReleaseFailure("publication proof omits or reorders required stages")
    _digest(proof.get("contract_sha256"), "contract")
    files = artifact_files(site_root)
    if proof.get("files") != files:
        raise ReleaseFailure("publication artifact changed after verification; rebuild and re-review")
    articles = data.get("articles") if isinstance(data, dict) else None
    records = proof.get("articles")
    if not isinstance(articles, list) or not isinstance(records, list) or not articles:
        raise ReleaseFailure("publication proof must cover a nonempty article inventory")
    by_slug: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("slug"), str):
            raise ReleaseFailure("invalid article publication proof")
        if record["slug"] in by_slug:
            raise ReleaseFailure(f"duplicate article proof: {record['slug']}")
        by_slug[record["slug"]] = record
    expected: set[str] = set()
    for article in articles:
        if not isinstance(article, dict) or not isinstance(article.get("slug"), str):
            raise ReleaseFailure("invalid publication inventory article")
        slug = article["slug"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug) or slug in expected:
            raise ReleaseFailure(f"invalid or duplicate publication slug: {slug}")
        expected.add(slug)
        if article.get("url") != f"posts/{slug}.html":
            raise ReleaseFailure(f"publication URL does not match its verified slug: {slug}")
        record = by_slug.get(slug)
        if record is None:
            raise ReleaseFailure(f"article has no completed cycle proof: {slug}")
        post = f"posts/{slug}.html"
        if record.get("post_sha256") != files.get(post) or post not in files:
            raise ReleaseFailure(f"article differs from its verified version: {slug}")
        report_hash = _digest(record.get("report_sha256"), f"report {slug}")
        stages = record.get("stages")
        if (not isinstance(stages, list) or len(stages) != len(PUBLIC_STAGES)
                or any(not isinstance(row, dict) for row in stages)
                or [row.get("stage") for row in stages] != list(PUBLIC_STAGES)):
            raise ReleaseFailure(f"article has incomplete stage proof: {slug}")
        for stage in stages:
            for field in ("prompt_sha256", "raw_sha256", "section_sha256"):
                _digest(stage.get(field), f"{slug} {stage['stage']} {field}")
        quality = record.get("quality")
        if (not isinstance(quality, dict) or quality.get("status") != "publishable"
                or quality.get("report_sha256") != report_hash
                or quality.get("post_sha256") != record["post_sha256"]
                or quality.get("link_health_checked") is not True
                or quality.get("blocking_issue_count") != 0):
            raise ReleaseFailure(f"article has no current final quality approval: {slug}")
        _digest(quality.get("review_sha256"), f"quality review {slug}")
    if set(by_slug) != expected:
        raise ReleaseFailure("publication proof inventory differs from the site inventory")
    actual_posts = {path for path in files if path.startswith("posts/") and path.endswith(".html")}
    if actual_posts != {f"posts/{slug}.html" for slug in expected}:
        raise ReleaseFailure("published HTML inventory differs from the verified article inventory")
    return {"status": "publication-ready", "articles": len(expected),
            "proof_sha256": proof["proof_sha256"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(validate_release(args.site_root), sort_keys=True))
    except ReleaseFailure as exc:
        print(f"Publication held: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
