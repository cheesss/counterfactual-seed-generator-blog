from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import stage_pages_artifact


def write_deployment_manifest(site: Path, paths: list[str]) -> None:
    files = []
    for value in paths:
        path = site.joinpath(*value.split("/"))
        files.append(
            {
                "path": value,
                "sha256": stage_pages_artifact.sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    manifest = {
        "files": files,
        "generator": "sync_static_site/v1",
        "schema_version": 1,
        "site_url": "https://cheesss.github.io/counterfactual-seed-generator-blog",
    }
    (site / ".generated-site-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class StagePagesArtifactTests(unittest.TestCase):
    def test_stages_only_hash_verified_owned_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = root / "repo"
            site.mkdir()
            (site / "assets").mkdir()
            (site / "index.html").write_text("index", encoding="utf-8")
            (site / "image-manifest.json").write_text("{}\n", encoding="utf-8")
            (site / "assets" / "hero.png").write_bytes(b"image")
            (site / "README.md").write_text("repository control", encoding="utf-8")
            (site / ".github").mkdir()
            (site / ".github" / "control.yml").write_text("control", encoding="utf-8")
            write_deployment_manifest(
                site,
                ["index.html", "image-manifest.json", "assets/hero.png"],
            )

            output = root / "_site"
            count = stage_pages_artifact.stage_pages_artifact(site, output)

            self.assertEqual(count, 3)
            self.assertTrue((output / "assets" / "hero.png").is_file())
            self.assertFalse((output / "README.md").exists())
            self.assertFalse((output / ".github").exists())
            self.assertFalse((output / ".generated-site-manifest.json").exists())

    def test_rejects_hash_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = root / "repo"
            site.mkdir()
            (site / "index.html").write_text("index", encoding="utf-8")
            (site / "image-manifest.json").write_text("{}\n", encoding="utf-8")
            write_deployment_manifest(site, ["index.html", "image-manifest.json"])
            (site / "index.html").write_text("changed", encoding="utf-8")

            with self.assertRaises(stage_pages_artifact.StagingFailure):
                stage_pages_artifact.stage_pages_artifact(site, root / "_site")

    def test_rejects_manifest_paths_outside_generated_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary)
            manifest = {
                "files": [
                    {"path": "index.html", "sha256": "0" * 64, "size": 0},
                    {"path": "image-manifest.json", "sha256": "0" * 64, "size": 0},
                    {"path": ".github/workflows/pages.yml", "sha256": "0" * 64, "size": 0},
                ],
                "generator": "sync_static_site/v1",
                "schema_version": 1,
            }
            (site / ".generated-site-manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            with self.assertRaises(stage_pages_artifact.StagingFailure):
                stage_pages_artifact.stage_pages_artifact(site, site / "_site")


if __name__ == "__main__":
    unittest.main()
