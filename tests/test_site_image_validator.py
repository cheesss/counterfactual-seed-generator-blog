from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import site_image_validator


SITE_URL = "https://cheesss.github.io/counterfactual-seed-generator-blog"


def write_png(path: Path, color: str = "#487d6f") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 6), color).save(path, format="PNG")


class SiteImageValidatorTests(unittest.TestCase):
    def test_validates_every_surface_and_writes_a_deterministic_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary)
            write_png(site / "assets" / "local.png")
            (site / "assets" / "nested.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><image href="local.png"/></svg>',
                encoding="utf-8",
            )
            (site / "styles.css").write_text(
                '.hero { background-image: url("assets/local.png"); }\n'
                "@import url('https://fonts.example.test/type.css');",
                encoding="utf-8",
            )
            (site / "index.html").write_text(
                '<link rel="stylesheet" href="styles.css">'
                '<link rel="icon" href="assets/nested.svg">'
                '<link rel="preload" as="image" href="assets/local.png" '
                'imagesrcset="assets/local.png 1x, assets/local.png 2x">'
                f'<meta property="og:image" content="{SITE_URL}/assets/local.png">'
                '<meta name="twitter:image:src" content="assets/local.png">'
                '<img src="assets/local.png" srcset="assets/local.png 1x, assets/local.png 2x">'
                '<picture><source src="assets/local.png" srcset="assets/local.png 2x"></picture>'
                '<video poster="assets/local.png"></video>'
                '<input type="image" src="assets/local.png">'
                '<div style="mask-image:url(assets/local.png)"></div>'
                '<style>.inline { background: url("assets/local.png") }</style>'
                '<svg><image xlink:href="assets/local.png"></image></svg>',
                encoding="utf-8",
            )

            first = site_image_validator.build_manifest(site, SITE_URL)
            second = site_image_validator.build_manifest(site, SITE_URL)
            manifest_path = site / "image-manifest.json"
            site_image_validator.write_manifest(manifest_path, first)
            site_image_validator.verify_manifest(manifest_path, second, site)

            self.assertEqual(first, second)
            self.assertEqual(
                [image["path"] for image in first["images"]],
                ["assets/local.png", "assets/nested.svg"],
            )
            surfaces = {reference["surface"] for reference in first["references"]}
            self.assertTrue(
                {
                    "img[src]",
                    "img[srcset]",
                    "source[src]",
                    "source[srcset]",
                    "video[poster]",
                    "input[type=image][src]",
                    "link[href]",
                    "link[imagesrcset]",
                    "meta[og:image][content]",
                    "meta[twitter:image:src][content]",
                    "inline-style:mask-image:url()",
                    "style:background:url()",
                    "stylesheet:background-image:url()",
                    "svg:image[xlink:href]",
                    "svg:image[href]",
                }.issubset(surfaces),
                surfaces,
            )

    def test_rejects_remote_data_missing_escape_and_undecodable_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary)
            (site / "assets").mkdir()
            (site / "assets" / "corrupt.png").write_bytes(b"not a png")
            (site / "styles.css").write_text(
                '.hero { background: url("https://remote.test/css.png"); }',
                encoding="utf-8",
            )
            (site / "assets" / "nested.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><image href="missing.png"/></svg>',
                encoding="utf-8",
            )
            (site / "index.html").write_text(
                '<link rel="stylesheet" href="styles.css">'
                '<link rel="preload" as="image" href="https://remote.test/preload.png">'
                '<meta property="og:image" content="assets/missing.png">'
                '<img src="https://remote.test/hero.png" srcset="../escape.png 2x">'
                '<source src="data:image/png;base64,AAAA">'
                '<video poster="assets/corrupt.png"></video>'
                '<input type="image">'
                '<svg><image href="https://remote.test/inline.svg"></image></svg>'
                '<img src="assets/nested.svg">',
                encoding="utf-8",
            )

            references, _stylesheets, _bindings, _issues = (
                site_image_validator.collect_references(site)
            )
            surfaces = {reference.surface for reference in references}
            self.assertTrue(
                {
                    "img[src]",
                    "img[srcset]",
                    "source[src]",
                    "video[poster]",
                    "input[type=image][src]",
                    "link[href]",
                    "meta[og:image][content]",
                    "stylesheet:background:url()",
                    "svg:image[href]",
                }.issubset(surfaces),
                surfaces,
            )

            with self.assertRaises(site_image_validator.ValidationFailure) as raised:
                site_image_validator.build_manifest(site, SITE_URL)
            codes = {issue["code"] for issue in raised.exception.issues}
            self.assertTrue(
                {
                    "remote-url",
                    "data-url",
                    "missing-url",
                    "path-escaping",
                    "missing-image",
                    "undecodable-image",
                }.issubset(codes),
                codes,
            )

    def test_manifest_drift_is_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary)
            write_png(site / "assets" / "local.png")
            (site / "index.html").write_text(
                '<img src="assets/local.png">', encoding="utf-8"
            )
            manifest_path = site / "image-manifest.json"
            manifest = site_image_validator.build_manifest(site, SITE_URL)
            site_image_validator.write_manifest(manifest_path, manifest)

            write_png(site / "assets" / "local.png", "#984f45")
            changed = site_image_validator.build_manifest(site, SITE_URL)
            with self.assertRaises(site_image_validator.ValidationFailure) as raised:
                site_image_validator.verify_manifest(manifest_path, changed, site)
            self.assertEqual(raised.exception.issues[0]["code"], "image-manifest-drift")


if __name__ == "__main__":
    unittest.main()
