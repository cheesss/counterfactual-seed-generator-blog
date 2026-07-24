from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


DEPLOYMENT_MANIFEST_NAME = ".generated-site-manifest.json"
DEPLOYMENT_SCHEMA_VERSION = 1
OWNED_ROOT_FILES = {
    ".nojekyll",
    "404.html",
    "about.html",
    "blog-data.json",
    "disclaimer.html",
    "favicon.svg",
    "feed.xml",
    "image-manifest.json",
    "index.html",
    "ledger.html",
    "patterns.html",
    "privacy.html",
    "publication-data.json",
    "rgraph.js",
    "site.js",
    "sitemap.xml",
    "styles.css",
}
OWNED_DIRECTORIES = {"assets", "posts"}


class StagingFailure(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checked_owned_path(value: str) -> PurePosixPath:
    if not value or "\\" in value:
        raise StagingFailure(f"invalid generated-site path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise StagingFailure(f"invalid generated-site path: {value!r}")
    if len(path.parts) == 1:
        if path.name not in OWNED_ROOT_FILES:
            raise StagingFailure(f"path is outside generated-site ownership: {value}")
    elif path.parts[0] not in OWNED_DIRECTORIES:
        raise StagingFailure(f"path is outside generated-site ownership: {value}")
    return path


def load_manifest(site_root: Path) -> dict[str, Any]:
    path = site_root / DEPLOYMENT_MANIFEST_NAME
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StagingFailure(f"missing {DEPLOYMENT_MANIFEST_NAME}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StagingFailure(f"cannot decode {DEPLOYMENT_MANIFEST_NAME}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise StagingFailure("generated-site manifest must be a JSON object")
    if manifest.get("schema_version") != DEPLOYMENT_SCHEMA_VERSION:
        raise StagingFailure("generated-site manifest has an unsupported schema version")
    if manifest.get("generator") != "sync_static_site/v1":
        raise StagingFailure("generated-site manifest has an unexpected generator")
    return manifest


def checked_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    raw_entries = manifest.get("files")
    if not isinstance(raw_entries, list):
        raise StagingFailure("generated-site manifest files must be a list")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw_entries:
        if not isinstance(entry, dict):
            raise StagingFailure("generated-site manifest contains a non-object file entry")
        value = entry.get("path")
        digest = entry.get("sha256")
        size = entry.get("size")
        if not isinstance(value, str):
            raise StagingFailure("generated-site manifest file path must be a string")
        checked_owned_path(value)
        if value in seen:
            raise StagingFailure(f"duplicate generated-site manifest path: {value}")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not isinstance(size, int)
            or size < 0
        ):
            raise StagingFailure(f"invalid generated-site manifest metadata: {value}")
        seen.add(value)
        entries.append(entry)
    for required in {"index.html", "image-manifest.json"}:
        if required not in seen:
            raise StagingFailure(f"generated-site manifest is missing required file: {required}")
    return sorted(entries, key=lambda entry: entry["path"])


def checked_source_file(site_root: Path, relative: PurePosixPath) -> Path:
    source = site_root.joinpath(*relative.parts)
    current = site_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise StagingFailure(
                f"generated file path contains a symbolic link: {relative.as_posix()}"
            )
    resolved_root = site_root.resolve()
    resolved_source = source.resolve()
    try:
        resolved_source.relative_to(resolved_root)
    except ValueError as exc:
        raise StagingFailure(f"generated file escapes repository: {relative.as_posix()}") from exc
    if source.is_symlink() or not source.is_file():
        raise StagingFailure(f"generated file is missing or not regular: {relative.as_posix()}")
    return source


def stage_pages_artifact(site_root: Path, output: Path) -> int:
    site_root = site_root.resolve()
    output = output.resolve()
    if not site_root.is_dir():
        raise StagingFailure(f"site repository does not exist: {site_root}")
    if output.exists():
        raise StagingFailure(f"output path must not already exist: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest(site_root)
    entries = checked_entries(manifest)
    verified: list[tuple[dict[str, Any], Path, PurePosixPath]] = []
    for entry in entries:
        relative = checked_owned_path(entry["path"])
        source = checked_source_file(site_root, relative)
        if source.stat().st_size != entry["size"] or sha256_file(source) != entry["sha256"]:
            raise StagingFailure(f"generated file hash drift: {relative.as_posix()}")
        verified.append((entry, source, relative))

    with tempfile.TemporaryDirectory(prefix="pages-artifact-", dir=output.parent) as temporary:
        stage = Path(temporary) / "site"
        stage.mkdir()
        for _entry, source, relative in verified:
            destination = stage.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        stage.replace(output)
    return len(verified)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage only hash-verified generated-site files for GitHub Pages."
    )
    parser.add_argument("--site-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        count = stage_pages_artifact(args.site_root, args.output)
    except StagingFailure as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Staged {count} hash-verified generated files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
