"""Validate repairs against an independent static baseline, not archive approval.

``files`` and ``baseline_files`` are path-to-SHA-256 maps. The original
selected-only aggregate bytes are carried in ``selected_artifacts`` as base64,
so the embedded v1 proof can be checked without rewriting its hashes.
Git repository/commit/run authenticity is the caller's separate responsibility.
"""
from __future__ import annotations

import base64
import binascii
import copy
import html
import json
import posixpath
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

try:
    from . import site_release_validator as artifact
except ImportError:
    import site_release_validator as artifact

UPDATE_FILE = ".publication-update.json"
ReleaseFailure = artifact.ReleaseFailure
BASELINE_REPOSITORY = "cheesss/counterfactual-seed-generator-blog"
IMMUTABLE_ROOTS = frozenset({
    "styles.css", "site.js", "rgraph.js", "favicon.svg",
    "about.html", "privacy.html", "disclaimer.html", ".nojekyll",
})
TITLE_ROOTS = frozenset({"index.html", "404.html", "ledger.html", "patterns.html", "feed.xml", "sitemap.xml"})
AGGREGATE_ROOTS = TITLE_ROOTS | {"blog-data.json", "publication-data.json"}
METADATA_ROOTS = frozenset({
    "image-manifest.json", artifact.RELEASE_FILE, UPDATE_FILE, ".generated-site-manifest.json",
})
ALLOWED_ROOTS = artifact.ARTIFACT_ROOT_FILES | METADATA_ROOTS


def _path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ReleaseFailure("invalid static artifact path")
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value
            or any(part in {".", ".."} for part in path.parts)):
        raise ReleaseFailure(f"invalid static artifact path: {value}")
    if len(path.parts) == 1:
        if value not in ALLOWED_ROOTS:
            raise ReleaseFailure(f"unsupported static root file: {value}")
    elif path.parts[0] not in {"posts", "assets"}:
        raise ReleaseFailure(f"unsupported static artifact path: {value}")
    return value


def _digest(value: Any) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ReleaseFailure("invalid static artifact SHA-256")


def _manifest(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise ReleaseFailure("static file manifest must be a nonempty object")
    for name, digest in value.items():
        _path(name)
        _digest(digest)
    return value


def _root(value: Path) -> Path:
    path = Path(value).absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise ReleaseFailure("static root contains a symbolic link or junction")
    if not path.is_dir():
        raise ReleaseFailure("static root must be an existing directory")
    return path.resolve()


def _files(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ReleaseFailure("static artifact contains a symbolic link or junction")
        if path.is_dir():
            relative = path.relative_to(root).as_posix()
            if relative.split("/")[0] not in {"posts", "assets"}:
                raise ReleaseFailure(f"unsupported static directory: {relative}")
            continue
        if not path.is_file():
            raise ReleaseFailure("static artifact contains a nonregular file")
        name = path.relative_to(root).as_posix()
        _path(name)
        if name in METADATA_ROOTS:
            continue
        result[name] = artifact.sha256_file(path)
    return result


def artifact_files(site_root: Path) -> dict[str, str]:
    """V1 artifact_files semantics, after rejecting unsafe/unowned filesystem paths."""
    return _files(_root(site_root))


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseFailure(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _json_bytes(value: bytes) -> Any:
    try:
        return json.loads(value.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda _: _invalid_json())
    except (ValueError, UnicodeError) as exc:
        raise ReleaseFailure(f"invalid static JSON: {exc}") from exc


def _invalid_json() -> None:
    raise ReleaseFailure("nonfinite JSON number")


def _json(path: Path) -> Any:
    return _json_bytes(path.read_bytes())


def _articles(data: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(data, dict) or not isinstance(data.get("articles"), list) or not data["articles"]:
        raise ReleaseFailure("publication inventory must be a nonempty article list")
    result = {}
    for row in data["articles"]:
        if not isinstance(row, dict):
            raise ReleaseFailure("invalid publication article")
        slug = row.get("slug")
        if (not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug)
                or slug in result or row.get("url") != f"posts/{slug}.html"
                or not isinstance(row.get("title"), str) or not row["title"]):
            raise ReleaseFailure("invalid or duplicate publication article identity")
        result[slug] = row
    return result


def _blog(data: Any, articles: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not isinstance(data, list):
        raise ReleaseFailure("blog-data must be a list")
    urls = {row["url"]: slug for slug, row in articles.items()}
    result = {}
    for row in data:
        if not isinstance(row, dict) or not isinstance(row.get("url"), str):
            raise ReleaseFailure("invalid blog-data row")
        slug = urls.get(row["url"])
        if slug is None or slug in result or row.get("title") != articles[slug]["title"]:
            raise ReleaseFailure("blog-data inventory or title differs from publication inventory")
        result[slug] = row
    if set(result) != set(articles):
        raise ReleaseFailure("blog-data inventory differs from publication inventory")
    return result


def _same_json(left: Any, right: Any) -> bool:
    return artifact.canonical_hash(left) == artifact.canonical_hash(right)


def summary_for_rows(old_summary: dict, merged_rows: list[dict]) -> dict:
    """Recount existing public fields; never infer approval or new ledger outcomes."""
    if not isinstance(old_summary, dict) or not isinstance(merged_rows, list):
        raise ReleaseFailure("invalid summary or merged rows")
    summary = copy.deepcopy(old_summary)
    series, signals, extractions, stages = [], [], [], []
    for row in merged_rows:
        if not isinstance(row, dict):
            raise ReleaseFailure("invalid summary article row")
        figures, watch = row.get("data_series", []), row.get("watch_signals", [])
        extraction, stage = row.get("watch_extraction", {}), row.get("report_stage", {})
        if (not isinstance(figures, list) or not isinstance(watch, list)
                or not all(isinstance(item, dict) for item in figures + watch)
                or not isinstance(extraction, dict) or not isinstance(stage, dict)):
            raise ReleaseFailure("invalid summary article count fields")
        count = extraction.get("unpublished_candidate_count", 0)
        if type(count) is not int or count < 0:
            raise ReleaseFailure("invalid unpublished candidate count")
        series.extend(figures)
        signals.extend(watch)
        extractions.append(extraction)
        stages.append(stage)

    def update_fields(target: dict, counts: dict) -> None:
        for key, value in counts.items():
            if key in target:
                target[key] = value

    def histogram(target: Any, values: list[str]) -> dict:
        if not isinstance(target, dict):
            raise ReleaseFailure("invalid summary histogram")
        keys = dict.fromkeys([*target, *[value for value in values if value]])
        return {key: values.count(key) for key in keys}

    calendar_count = 0
    for figure in series:
        headline = figure.get("headline", {})
        if not isinstance(headline, dict):
            raise ReleaseFailure("invalid data-series headline")
        calendar_count += int(headline.get("is_calendar_year") is True)
    update_fields(summary, {"articles": len(merged_rows), "data_series": len(series),
                            "calendar_year_card_candidates_excluded": calendar_count})
    if "watch" in summary:
        watch = summary["watch"]
        if not isinstance(watch, dict):
            raise ReleaseFailure("invalid watch summary")
        states = [str(signal.get("state", "open")) for signal in signals]
        update_fields(watch, {"tracked_articles": sum(bool(row.get("watch_signals")) for row in merged_rows),
                              "signals": len(signals),
                              "resolved": sum(state in {"confirmed", "weakened", "broken"} for state in states),
                              "unstructured_watch_sections": sum(item.get("unpublished_candidate_count", 0) > 0 for item in extractions),
                              "unpublished_prose_candidates": sum(item.get("unpublished_candidate_count", 0) for item in extractions)})
        for key, values in (("states", states), ("extraction_modes", [str(item.get("extraction_mode", "")) for item in signals])):
            if key in watch:
                watch[key] = histogram(watch[key], values)
    if "report_stage" in summary:
        stage_summary = summary["report_stage"]
        if not isinstance(stage_summary, dict):
            raise ReleaseFailure("invalid report-stage summary")
        update_fields(stage_summary, {"complete_by_sections": sum(item.get("public_cycle_complete_by_sections") is True for item in stages),
                                      "complete_by_run_manifest": sum(item.get("public_cycle_complete_by_run_manifest") is True for item in stages)})
        if "coverage_statuses" in stage_summary:
            stage_summary["coverage_statuses"] = histogram(stage_summary["coverage_statuses"], [str(item.get("coverage_status", "")) for item in stages])
    if "taxonomy" in summary:
        taxonomy = summary["taxonomy"]
        if not isinstance(taxonomy, dict):
            raise ReleaseFailure("invalid taxonomy summary")
        for bucket, field in (("analysis_lenses", "analysis_lens"), ("sectors", "sector"), ("system_patterns", "system_pattern")):
            if bucket not in taxonomy:
                continue
            values = []
            for row in merged_rows:
                value = row.get(field, row.get("archetype", {}) if field == "system_pattern" else {})
                if not isinstance(value, dict):
                    raise ReleaseFailure("invalid article taxonomy")
                values.append(str(value.get("key", "")))
            taxonomy[bucket] = histogram(taxonomy[bucket], values)
    return summary


def _selected_proof(site: Path, update: dict[str, Any], files: dict[str, str],
                    baseline_files: dict[str, str]) -> dict[str, dict[str, Any]]:
    proof = update.get("selected_release")
    if (not isinstance(proof, dict) or type(proof.get("schema_version")) is not int
            or proof["schema_version"] != 1):
        raise ReleaseFailure("selected_release must be a v1 proof object")
    proof_files = _manifest(proof.get("files"))
    records = proof.get("articles")
    if not isinstance(records, list) or not records:
        raise ReleaseFailure("selected proof must have nonempty article records")
    for record in records:
        if (not isinstance(record, dict) or not isinstance(record.get("quality"), dict)
                or type(record["quality"].get("blocking_issue_count")) is not int):
            raise ReleaseFailure("malformed selected proof quality types")
    if artifact.RELEASE_FILE in proof_files or "image-manifest.json" in proof_files:
        raise ReleaseFailure("selected proof contains unsupported v1 file inventory")
    encoded = update.get("selected_artifacts")
    expected = set(proof_files) & AGGREGATE_ROOTS
    if not isinstance(encoded, dict) or set(encoded) != expected:
        raise ReleaseFailure("selected_artifacts must contain exactly the proved aggregate bytes")
    if not {"publication-data.json", "blog-data.json"} <= expected:
        raise ReleaseFailure("selected proof must include publication-data and blog-data")
    with tempfile.TemporaryDirectory(prefix="csg-selected-proof-") as temporary:
        subset = Path(temporary)
        for name, digest in proof_files.items():
            target = subset / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if name in AGGREGATE_ROOTS:
                if not isinstance(encoded[name], str):
                    raise ReleaseFailure("selected artifact must be base64 text")
                try:
                    raw = base64.b64decode(encoded[name], validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise ReleaseFailure("invalid selected artifact base64") from exc
                target.write_bytes(raw)
                if artifact.sha256_file(target) != digest:
                    raise ReleaseFailure(f"selected aggregate hash drift: {name}")
            else:
                if files.get(name) != digest:
                    raise ReleaseFailure(f"selected proof file differs from candidate: {name}")
                shutil.copyfile(site / name, target)
            if name in IMMUTABLE_ROOTS - AGGREGATE_ROOTS and digest != baseline_files.get(name):
                raise ReleaseFailure(f"selected shared control differs from baseline: {name}")
        (subset / artifact.RELEASE_FILE).write_text(json.dumps(proof), encoding="utf-8")
        artifact.validate_release(subset)
        rows = _articles(_json(subset / "publication-data.json"))
        selected_blog = _blog(_json(subset / "blog-data.json"), rows)
    return {slug: {"article": row, "blog": selected_blog[slug]} for slug, row in rows.items()}


def _title_bytes(raw: bytes, replacements: dict[bytes, bytes]) -> bytes:
    if not replacements:
        return raw
    pattern = re.compile(b"|".join(re.escape(key) for key in sorted(replacements, key=len, reverse=True)))
    return pattern.sub(lambda match: replacements[match.group()], raw)


def _references_asset(value: Any, asset: str) -> bool:
    if isinstance(value, dict):
        return any(_references_asset(item, asset) for item in value.values())
    if isinstance(value, list):
        return any(_references_asset(item, asset) for item in value)
    if not isinstance(value, str):
        return False
    text = html.unescape(value)
    for _ in range(3):
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    text = text.replace("\\", "/")
    if re.search(r'''(?:^|[/=\s"'(])''' + re.escape(asset) + r'''(?=$|[?#\s"'<>);,])''', text):
        return True
    # Scan all URL-like text, including CSS and metadata, not just img src.
    for token in re.findall(r'''[^\s"'<>`]+''', text):
        token = token.strip("()[]{};,=")
        try:
            path = posixpath.normpath(urlsplit(token).path)
        except ValueError:
            continue
        if path == asset or path.endswith("/" + asset):
            return True
    return False


def _exclusive_selected_og(name: str, selected: dict, proof_files: dict,
                           digest: str | None, baseline: Path, site: Path,
                           old: dict, new: dict, old_blog: dict, new_blog: dict) -> bool:
    if (name not in {f"assets/og/{slug}.png" for slug in selected}
            or digest is None or proof_files.get(name) != digest):
        return False
    for root in (baseline, site):
        for page in root.rglob("*.html"):
            relative = page.relative_to(root).as_posix()
            if relative in {f"posts/{slug}.html" for slug in selected}:
                continue
            if _references_asset(page.read_text(encoding="utf-8"), name):
                return False
    for rows in (old, new, old_blog, new_blog):
        if any(_references_asset(row, name) for slug, row in rows.items() if slug not in selected):
            return False
    return True


def validate_update(site_root: Path, baseline_root: Path) -> dict[str, Any]:
    """Verify selected repairs; preserved articles receive no research attestation."""
    try:
        site, baseline = _root(site_root), _root(baseline_root)
        if site == baseline or site in baseline.parents or baseline in site.parents:
            raise ReleaseFailure("baseline must be an independent artifact tree")
        files, baseline_files = _files(site), _files(baseline)
        if (site / artifact.RELEASE_FILE).exists():
            raise ReleaseFailure("mixed static update cannot carry a full publication release proof")
        update = _json(site / UPDATE_FILE)
        if (not isinstance(update, dict) or type(update.get("schema_version")) is not int
                or update["schema_version"] != 1 or update.get("kind") != "static-selected-update-v1"
                or update.get("status") != "selected-update-ready"):
            raise ReleaseFailure("unsupported static update profile")
        unsigned = {key: value for key, value in update.items() if key != "proof_sha256"}
        if update.get("proof_sha256") != artifact.canonical_hash(unsigned):
            raise ReleaseFailure("static update proof hash drift")
        binding = update.get("baseline")
        if (not isinstance(binding, dict) or binding.get("repository") != BASELINE_REPOSITORY
                or not isinstance(binding.get("commit"), str)
                or not re.fullmatch(r"[0-9a-f]{40}", binding["commit"])
                or type(binding.get("run_id")) is not int or binding["run_id"] <= 0):
            raise ReleaseFailure("invalid baseline repository/commit/run binding")
        if _manifest(update.get("files")) != files:
            raise ReleaseFailure("candidate file inventory or hashes drifted")
        if _manifest(update.get("baseline_files")) != baseline_files:
            raise ReleaseFailure("independent baseline file inventory or hashes drifted")
        selected = _selected_proof(site, update, files, baseline_files)
        old_data, new_data = _json(baseline / "publication-data.json"), _json(site / "publication-data.json")
        old, new = _articles(old_data), _articles(new_data)
        if set(old) != set(new) or not set(selected) <= set(old):
            raise ReleaseFailure("repair-only update must preserve all baseline slugs")
        expected_data = dict(old_data)
        if "summary" in old_data:
            expected_data["summary"] = summary_for_rows(old_data["summary"], new_data["articles"])
        if not _same_json({k: v for k, v in expected_data.items() if k != "articles"},
                          {k: v for k, v in new_data.items() if k != "articles"}):
            raise ReleaseFailure("publication non-articles fields changed")
        if [row["slug"] for row in old_data["articles"]] != [row["slug"] for row in new_data["articles"]]:
            raise ReleaseFailure("publication article order changed")
        old_blog = _blog(_json(baseline / "blog-data.json"), old)
        new_blog = _blog(_json(site / "blog-data.json"), new)
        if [row["url"] for row in _json(baseline / "blog-data.json")] != [row["url"] for row in _json(site / "blog-data.json")]:
            raise ReleaseFailure("blog-data article order changed")
        for root, mapping in ((site, files), (baseline, baseline_files)):
            posts = {name for name in mapping if name.startswith("posts/")}
            if posts != {f"posts/{slug}.html" for slug in old}:
                raise ReleaseFailure(f"unexpected post inventory in {root.name}")
        for slug in old:
            if slug in selected:
                if (not _same_json(new[slug], selected[slug]["article"])
                        or not _same_json(new_blog[slug], selected[slug]["blog"])):
                    raise ReleaseFailure(f"selected metadata differs from proved metadata: {slug}")
            elif (not _same_json(old[slug], new[slug]) or not _same_json(old_blog[slug], new_blog[slug])
                  or baseline_files[f"posts/{slug}.html"] != files[f"posts/{slug}.html"]):
                raise ReleaseFailure(f"preserved article changed: {slug}")
        baseline_inventory = set(baseline_files) - {artifact.RELEASE_FILE}
        if set(files) - {name for name in files if name.startswith("assets/")} != baseline_inventory - {name for name in baseline_inventory if name.startswith("assets/")}:
            raise ReleaseFailure("static root/post inventory changed")
        for name in IMMUTABLE_ROOTS:
            if name not in baseline_files or files.get(name) != baseline_files[name]:
                raise ReleaseFailure(f"immutable shared file changed or missing: {name}")
        for name, digest in baseline_files.items():
            if name.startswith("assets/") and files.get(name) != digest:
                if not _exclusive_selected_og(
                        name, selected, update["selected_release"]["files"], files.get(name),
                        baseline, site, old, new, old_blog, new_blog):
                    raise ReleaseFailure(f"baseline asset changed, deleted, or not exclusive approved selected OG: {name}")
        for name in set(files) - set(baseline_files):
            if (not name.startswith("assets/")
                    or not any(PurePosixPath(name).name.startswith(slug + "-")
                               or PurePosixPath(name).name == slug + PurePosixPath(name).suffix
                               for slug in selected)
                    or update["selected_release"]["files"].get(name) != files[name]):
                raise ReleaseFailure(f"new asset is not proved and selected-slug scoped: {name}")
        replacements = {}
        for slug in selected:
            before, after = html.escape(old[slug]["title"]).encode(), html.escape(new[slug]["title"]).encode()
            if before in replacements and replacements[before] != after:
                raise ReleaseFailure("ambiguous selected title replacement")
            replacements[before] = after
            if old[slug]["title"] != new[slug]["title"] and any(
                    row["title"] == old[slug]["title"] for key, row in old.items() if key not in selected):
                raise ReleaseFailure("selected title collides with preserved article title")
        for name in TITLE_ROOTS & set(baseline_files):
            before, after = (baseline / name).read_bytes(), (site / name).read_bytes()
            if after not in (before, _title_bytes(before, replacements)):
                raise ReleaseFailure(f"aggregate changed beyond exact title replacements: {name}")
        mutable = AGGREGATE_ROOTS | {"image-manifest.json"}
        for name in baseline_inventory - mutable - IMMUTABLE_ROOTS:
            if not name.startswith(("posts/", "assets/")) and files[name] != baseline_files[name]:
                raise ReleaseFailure(f"unexpected static root change: {name}")
        return {"status": "selected-update-ready", "verified_updates": len(selected),
                "preserved_articles": len(old) - len(selected)}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, ReleaseFailure):
            raise
        raise ReleaseFailure(f"invalid static update: {exc}") from exc
