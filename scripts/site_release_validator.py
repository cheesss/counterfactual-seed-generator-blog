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
PROFILE_SCHEMA_VERSION = 2
EXHAUSTIVE_PROFILE = "exhaustive-v1"
INTEGRATED_PROFILE = "integrated-v1"
INTEGRATED_REVIEW_SCOPE = "integrated-independent-source-review"
FORMAT_REPAIR_SCOPE = "artifact-format-validation"
CLAIM_REPAIR_SCOPE = "artifact-affected-claim-source-review"
REPAIR_SCOPES = {FORMAT_REPAIR_SCOPE, CLAIM_REPAIR_SCOPE}
# This deployment policy is deliberately pinned. Its contract-equivalence test
# must change with an explicitly reviewed public-cycle contract update.
PUBLIC_STAGES = (
    "claim-audit", "thesis-deepening", "commercialization-expansion",
    "value-migration-map", "forecast-calibration", "verification-questions",
    "independent-evidence", "adversarial-verification",
    "chart-provenance-repair", "codex-blog-editor",
)
INTEGRATED_STAGES = ("integrated-research", "independent-review-edit")
PROFILE_STAGES = {
    EXHAUSTIVE_PROFILE: PUBLIC_STAGES,
    INTEGRATED_PROFILE: INTEGRATED_STAGES,
}
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


def validate_stage_proof(stages: Any, profile: str, *, label: str) -> None:
    required = PROFILE_STAGES.get(profile) if isinstance(profile, str) else None
    if required is None:
        raise ReleaseFailure(f"unknown publication profile: {profile}")
    if (not isinstance(stages, list) or len(stages) != len(required)
            or any(not isinstance(row, dict) for row in stages)
            or [row.get("stage") for row in stages] != list(required)):
        raise ReleaseFailure(f"article has incomplete stage proof: {label}")
    for stage in stages:
        if "profile" in stage and stage["profile"] != profile:
            raise ReleaseFailure(f"mixed publication profiles: {label}")
        for field in ("prompt_sha256", "raw_sha256", "section_sha256"):
            _digest(stage.get(field), f"{label} {stage['stage']} {field}")


def validate_integrated_quality(quality: dict[str, Any], stages: list[dict[str, Any]],
                                report_hash: str, *, label: str) -> None:
    """Validate original full review or its explicit, hash-linked repair extension."""
    provenance = quality.get("review_provenance")
    scope = quality.get("review_scope")
    if (not isinstance(scope, str) or not isinstance(provenance, dict) or provenance.get("scope") != scope
            or provenance.get("profile") != INTEGRATED_PROFILE
            or ("report_sha256" in provenance and provenance["report_sha256"] != report_hash)):
        raise ReleaseFailure(f"quality approval differs from its captured profile reviewer: {label}")
    reviewer = stages[-1]
    if scope in REPAIR_SCOPES:
        chain = quality.get("approval_chain")
        if not isinstance(chain, list) or len(chain) < 2 or not all(isinstance(row, dict) for row in chain):
            raise ReleaseFailure(f"scoped repair lacks its original approval chain: {label}")
        original = chain[0]
        previous = _digest(original.get("report_sha256"), "original repaired report")
        if (original.get("scope") != INTEGRATED_REVIEW_SCOPE or original.get("stages") != stages
                or provenance.get("original_report_sha256") != previous):
            raise ReleaseFailure(f"repair original approval binding changed: {label}")
        for field in ("base_state_sha256", "repair_chain_sha256"):
            _digest(provenance.get(field), f"repair provenance {field}")
        for row in chain[1:]:
            row_scope = row.get("scope")
            changes, verified, grounded = (row.get("changed_claims"), row.get("verified_claims"),
                                           row.get("grounded_assertions"))
            if (not isinstance(row_scope, str) or row_scope not in REPAIR_SCOPES or row.get("before_sha256") != previous
                    or not isinstance(changes, list) or not isinstance(verified, list)
                    or not isinstance(grounded, list)):
                raise ReleaseFailure(f"repair chain or affected-claim scope changed: {label}")
            previous = _digest(row.get("after_sha256"), "repaired report")
            repair_stages = row.get("stages")
            if (not isinstance(repair_stages, list) or not repair_stages
                    or any(not isinstance(stage, dict) or stage.get("stage") not in {
                        "artifact-format-repair", "artifact-claim-review", "targeted-research"} for stage in repair_stages)
                    or repair_stages[0]["stage"] != "artifact-format-repair"):
                raise ReleaseFailure(f"invalid scoped repair stage proof: {label}")
            for stage in repair_stages:
                for field in ("prompt_sha256", "raw_sha256", "section_sha256"):
                    _digest(stage.get(field), f"repair {stage['stage']} {field}")
            if row_scope == FORMAT_REPAIR_SCOPE:
                if changes or verified or repair_stages[-1]["stage"] != "artifact-format-repair":
                    raise ReleaseFailure(f"format-only approval contains changed claims: {label}")
            else:
                if not changes or repair_stages[-1]["stage"] != "artifact-claim-review":
                    raise ReleaseFailure(f"affected claims lack a fresh source review: {label}")
                expected = {}
                for change in changes:
                    if (not isinstance(change, dict) or not isinstance(change.get("id"), str)
                            or not change["id"] or change["id"] in expected
                            or any(not isinstance(change.get(key), str) for key in ("before", "after"))
                            or not (change["before"] or change["after"])):
                        raise ReleaseFailure(f"invalid affected claim unit: {label}")
                    expected[change["id"]] = change
                covered = set()
                for claim in verified:
                    if not isinstance(claim, dict) or not isinstance(claim.get("change_id"), str):
                        raise ReleaseFailure(f"invalid affected-claim coverage: {label}")
                    change = expected.get(claim["change_id"])
                    if (change is None or claim["change_id"] in covered
                            or any(claim.get(key) != change[key] for key in ("before", "after"))
                            or not isinstance(claim.get("source_url"), str)
                            or not re.match(r"https?://\S+", claim["source_url"])
                            or not isinstance(claim.get("finding"), str) or not claim["finding"].strip()):
                        raise ReleaseFailure(f"affected-claim source coverage changed: {label}")
                    covered.add(claim["change_id"])
                if covered != set(expected):
                    raise ReleaseFailure(f"source review omitted affected claims: {label}")
            for fact in grounded:
                if (not isinstance(fact, dict) or any(not isinstance(fact.get(key), str) or not fact[key].strip()
                        for key in ("assertion", "support_text", "source_url"))
                        or " ".join(fact["assertion"].split()) != " ".join(fact["support_text"].split())
                        or not re.match(r"https?://\S+", fact["source_url"])):
                    raise ReleaseFailure(f"format summary lacks exact prior checked support: {label}")
        if previous != report_hash or chain[-1]["scope"] != scope:
            raise ReleaseFailure(f"repair approval does not cover the final report: {label}")
        reviewer = chain[-1]["stages"][-1]
    elif scope != INTEGRATED_REVIEW_SCOPE or "approval_chain" in quality:
        raise ReleaseFailure(f"unknown or ambiguous integrated review scope: {label}")
    if (provenance.get("stage") != reviewer["stage"]
            or any(provenance.get(field) != reviewer[field]
                   for field in ("prompt_sha256", "raw_sha256", "section_sha256"))):
        raise ReleaseFailure(f"quality approval differs from its captured profile reviewer: {label}")


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
    if (not isinstance(proof, dict) or type(proof.get("schema_version")) is not int
            or proof["schema_version"] not in {SCHEMA_VERSION, PROFILE_SCHEMA_VERSION}):
        raise ReleaseFailure("unsupported publication proof schema")
    if proof.get("status") != "publication-ready":
        raise ReleaseFailure("artifact has not completed the publication cycle")
    unsigned = {key: value for key, value in proof.items() if key != "proof_sha256"}
    if proof.get("proof_sha256") != canonical_hash(unsigned):
        raise ReleaseFailure("publication proof hash drift")
    version = proof["schema_version"]
    if version == SCHEMA_VERSION:
        if proof.get("required_stages") != list(PUBLIC_STAGES):
            raise ReleaseFailure("publication proof omits or reorders required stages")
        if "profile_contracts" in proof or "profile" in proof:
            raise ReleaseFailure("legacy publication proof cannot declare profiles")
        _digest(proof.get("contract_sha256"), "contract")
    else:
        contracts = proof.get("profile_contracts")
        if ("required_stages" in proof or "contract_sha256" in proof or "profile" in proof
                or not isinstance(contracts, dict) or not contracts
                or any(profile not in PROFILE_STAGES for profile in contracts)):
            raise ReleaseFailure("invalid publication profile contracts")
        for profile, digest in contracts.items():
            _digest(digest, f"contract {profile}")
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
    used_profiles: set[str] = set()
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
        if version == SCHEMA_VERSION:
            if "profile" in record:
                raise ReleaseFailure(f"legacy article proof cannot declare a profile: {slug}")
            profile = EXHAUSTIVE_PROFILE
        else:
            profile = record.get("profile")
            if not isinstance(profile, str) or profile not in PROFILE_STAGES:
                raise ReleaseFailure(f"missing or unknown article publication profile: {slug}")
            used_profiles.add(profile)
        validate_stage_proof(record.get("stages"), profile, label=slug)
        quality = record.get("quality")
        if (not isinstance(quality, dict) or quality.get("status") != "publishable"
                or quality.get("report_sha256") != report_hash
                or quality.get("post_sha256") != record["post_sha256"]
                or quality.get("link_health_checked") is not True
                or quality.get("blocking_issue_count") != 0):
            raise ReleaseFailure(f"article has no current final quality approval: {slug}")
        _digest(quality.get("review_sha256"), f"quality review {slug}")
        if profile == INTEGRATED_PROFILE or "review_scope" in quality or "review_provenance" in quality:
            if version != PROFILE_SCHEMA_VERSION or profile != INTEGRATED_PROFILE:
                raise ReleaseFailure(f"quality approval differs from its captured profile reviewer: {slug}")
            validate_integrated_quality(quality, record["stages"], report_hash, label=slug)
    if set(by_slug) != expected:
        raise ReleaseFailure("publication proof inventory differs from the site inventory")
    if version == PROFILE_SCHEMA_VERSION and set(proof["profile_contracts"]) != used_profiles:
        raise ReleaseFailure("publication profile contracts differ from the article profiles")
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
