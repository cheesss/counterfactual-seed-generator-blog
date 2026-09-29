"""Resolve an independently confirmed, previously deployed Pages baseline."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tarfile
import hashlib
import tempfile
from pathlib import Path

import site_release_validator as artifact

REPOSITORY = "cheesss/counterfactual-seed-generator-blog"
MANIFEST = ".generated-site-manifest.json"


def command(args: list[str], cwd: Path) -> bytes:
    result = subprocess.run(args, cwd=cwd, capture_output=True, check=False, timeout=120)
    if result.returncode:
        raise artifact.ReleaseFailure(f"baseline command failed: {args[0]} {args[1]}")
    return result.stdout


def verify_deployment(repo: Path, baseline: dict) -> None:
    if (baseline.get("repository") != REPOSITORY
            or not re.fullmatch(r"[0-9a-f]{40}", str(baseline.get("commit", "")))
            or type(baseline.get("run_id")) is not int or baseline["run_id"] <= 0):
        raise artifact.ReleaseFailure("invalid deployed baseline identity")
    origin = command(["git", "remote", "get-url", "origin"], repo).decode().strip()
    if origin not in {f"https://github.com/{REPOSITORY}.git", f"https://github.com/{REPOSITORY}",
                      f"git@github.com:{REPOSITORY}.git"}:
        raise artifact.ReleaseFailure("baseline repository origin differs from configured Pages repository")
    endpoint = f"repos/{REPOSITORY}/actions/runs/{baseline['run_id']}"
    run = json.loads(command(["gh", "api", endpoint], repo))
    jobs = json.loads(command(["gh", "api", endpoint + "/jobs?per_page=100"], repo))
    if (run.get("head_sha") != baseline["commit"] or run.get("conclusion") != "success"
            or run.get("status") != "completed" or run.get("event") not in {"push", "workflow_dispatch"}
            or run.get("path") != ".github/workflows/pages.yml"
            or not any(row.get("name", "").lower().startswith("deploy")
                       and row.get("conclusion") == "success" for row in jobs.get("jobs", []))):
        raise artifact.ReleaseFailure("baseline has no confirmed successful Pages deployment")
    command(["git", "cat-file", "-e", baseline["commit"] + "^{commit}"], repo)


def latest_deployment(repo: Path) -> dict:
    rows = json.loads(command(["gh", "run", "list", "--repo", REPOSITORY,
                              "--workflow", "pages.yml", "--status", "success", "--limit", "20",
                              "--json", "databaseId,headSha,event"], repo))
    for row in rows:
        if row.get("event") in {"push", "workflow_dispatch"}:
            baseline = {"repository": REPOSITORY, "commit": row["headSha"], "run_id": row["databaseId"]}
            verify_deployment(repo, baseline)
            return baseline
    raise artifact.ReleaseFailure("no successfully deployed Pages baseline is available")


def materialize_baseline(repo: Path, baseline: dict, destination: Path) -> None:
    """Read committed blobs, never mutable working-tree files or embedded hash claims."""
    verify_deployment(repo, baseline)
    commit = baseline["commit"]
    manifest = json.loads(command(["git", "show", f"{commit}:{MANIFEST}"], repo))
    if manifest.get("generator") != "sync_static_site/v1" or manifest.get("schema_version") != 1:
        raise artifact.ReleaseFailure("deployed baseline has no recognized ownership manifest")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise artifact.ReleaseFailure("malformed baseline ownership manifest")
    seen = set()
    wanted = {}
    destination.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        name = entry.get("path", "") if isinstance(entry, dict) else ""
        path = Path(name)
        if (not name or "\\" in name or path.is_absolute() or ".." in path.parts
                or path.as_posix() != name or name in seen
                or not (name in artifact.ARTIFACT_ROOT_FILES or name in {
                    "image-manifest.json", artifact.RELEASE_FILE, ".publication-update.json"}
                    or path.parts[0] in {"posts", "assets"})):
            raise artifact.ReleaseFailure("unsafe or duplicate deployed baseline path")
        seen.add(name)
        wanted[name] = entry
    if not {"index.html", "publication-data.json", "blog-data.json", "styles.css"} <= seen:
        raise artifact.ReleaseFailure("deployed baseline is incomplete")
    # Stream committed blobs in one Git call; no tar extraction or working-tree reads.
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf",
                                    "archive", "--format=tar", commit], cwd=repo,
                                   stdout=subprocess.PIPE, stderr=errors)
        copied = set()
        try:
            with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
                for member in archive:
                    if member.name not in wanted:
                        continue
                    entry = wanted[member.name]
                    if not member.isfile() or member.size != entry.get("size") or member.name in copied:
                        raise artifact.ReleaseFailure(
                            f"invalid committed baseline artifact: {member.name} "
                            f"(type={member.type!r}, size={member.size}, expected={entry.get('size')})")
                    source = archive.extractfile(member)
                    if source is None:
                        raise artifact.ReleaseFailure("baseline blob cannot be read")
                    target = destination / member.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    with source, target.open("wb") as output:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            digest.update(chunk)
                            output.write(chunk)
                    if digest.hexdigest() != entry.get("sha256"):
                        raise artifact.ReleaseFailure(f"deployed baseline manifest hash drift: {member.name}")
                    copied.add(member.name)
            if process.wait(timeout=120) or copied != seen:
                raise artifact.ReleaseFailure("committed deployed artifact is incomplete")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if process.stdout is not None:
                process.stdout.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-root", type=Path, required=True)
    parser.add_argument("--baseline-repo", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        import static_update_validator as update
        proof = json.loads((args.site_root / update.UPDATE_FILE).read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="csg-deployed-baseline-") as temporary:
            baseline_root = Path(temporary) / "site"
            materialize_baseline(args.baseline_repo.resolve(), proof["baseline"], baseline_root)
            if latest_deployment(args.baseline_repo.resolve()) != proof["baseline"]:
                raise artifact.ReleaseFailure("a newer Pages deployment exists; rebuild the selected update")
            result = update.validate_update(args.site_root, baseline_root)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (artifact.ReleaseFailure, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Static update held: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
