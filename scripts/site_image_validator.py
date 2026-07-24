from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse


MANIFEST_SCHEMA_VERSION = 2
DEFAULT_SITE_URL = "https://cheesss.github.io/counterfactual-seed-generator-blog"
REPORT_HERO_WIDTH = 1600
REPORT_HERO_HEIGHT = 900
PIXEL_SAMPLE_COLUMNS = 160
PIXEL_SAMPLE_ROWS = 90
MIN_VISIBLE_COLOR_BUCKETS = 4
MAX_DOMINANT_COLOR_PERCENT = 99
MIN_VISIBLE_CHANNEL_SPAN = 24
JSON_IMAGE_EXPORTS = {"blog-data.json", "publication-data.json"}
IMAGE_SUFFIXES = {
    ".avif",
    ".bmp",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".tif",
    ".tiff",
    ".webp",
}
CSS_URL_RE = re.compile(
    r"url\(\s*(?:(?P<quote>['\"])(?P<quoted>.*?)(?P=quote)|(?P<plain>[^)]*))\s*\)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class Reference:
    document: Path
    surface: str
    url: str


@dataclass(frozen=True)
class ReportHeroBinding:
    document: Path
    surface: str
    title: str
    report_url: str
    slug: str
    image_url: str
    expected_sha256: Any = None
    expected_width: Any = None
    expected_height: Any = None
    expected_media_type: Any = None


class ValidationFailure(RuntimeError):
    def __init__(self, issues: Iterable[dict[str, str]]) -> None:
        self.issues = sorted(
            list(issues),
            key=lambda issue: (
                issue.get("document", ""),
                issue.get("surface", ""),
                issue.get("code", ""),
                issue.get("url", ""),
            ),
        )
        super().__init__(f"image validation failed with {len(self.issues)} issue(s)")


def srcset_urls(value: str) -> list[str]:
    """Return srcset candidates while retaining data URLs for rejection."""
    if value.lstrip().casefold().startswith("data:"):
        return [value.strip()]
    urls: list[str] = []
    for candidate in value.split(","):
        url = candidate.strip().split(maxsplit=1)[0].strip("\"'")
        if url:
            urls.append(url)
    return urls


def css_image_references(css: str, document: Path, scope: str) -> list[Reference]:
    """Collect declaration URLs, excluding @import and @font-face src URLs."""
    references: list[Reference] = []
    cleaned = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    for match in CSS_URL_RE.finditer(cleaned):
        prefix_start = max(
            cleaned.rfind(";", 0, match.start()),
            cleaned.rfind("{", 0, match.start()),
        ) + 1
        prefix = cleaned[prefix_start : match.start()]
        property_match = re.match(r"\s*([a-zA-Z-][\w-]*)\s*:", prefix)
        if not property_match:
            continue
        property_name = property_match.group(1).casefold()
        if property_name == "src":
            continue
        url = (
            match.group("quoted")
            if match.group("quote")
            else match.group("plain") or ""
        ).strip()
        if url.startswith("#"):
            continue
        references.append(
            Reference(document, f"{scope}:{property_name}:url()", url)
        )
    return references


class ImageSurfaceHTMLParser(HTMLParser):
    def __init__(self, document: Path) -> None:
        super().__init__(convert_charrefs=True)
        self.document = document
        self.references: list[Reference] = []
        self.stylesheets: list[Reference] = []
        self._style_depth = 0
        self._style_chunks: list[str] = []
        self._svg_depth = 0
        self._report_hero_depth = 0

    def _append(self, element: str, attribute: str, value: str) -> None:
        self.references.append(
            Reference(self.document, f"{element}[{attribute}]", value)
        )

    def _append_srcset(self, element: str, attribute: str, value: str) -> None:
        urls = srcset_urls(value)
        if not urls:
            self._append(element, attribute, "")
        for url in urls:
            self._append(element, attribute, url)

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = {name.casefold(): (value or "") for name, value in attrs}
        tag_name = tag.casefold()
        if tag_name == "svg":
            self._svg_depth += 1
        if (
            tag_name == "figure"
            and "article-hero-media" in values.get("class", "").casefold().split()
        ):
            self._report_hero_depth += 1

        if tag_name == "img":
            element = "report-hero:img" if self._report_hero_depth else "img"
            self._append(element, "src", values.get("src", ""))
            if "srcset" in values:
                self._append_srcset(element, "srcset", values["srcset"])
        elif tag_name == "source":
            if "src" in values:
                self._append("source", "src", values["src"])
            if "srcset" in values:
                self._append_srcset("source", "srcset", values["srcset"])
        elif tag_name == "video" and "poster" in values:
            self._append("video", "poster", values["poster"])
        elif tag_name == "input" and values.get("type", "").casefold() == "image":
            self._append("input[type=image]", "src", values.get("src", ""))
        elif tag_name == "meta":
            meta_name = (values.get("property") or values.get("name") or "").casefold()
            itemprop = values.get("itemprop", "").casefold()
            if meta_name.startswith("og:image") or meta_name.startswith("twitter:image"):
                self._append(f"meta[{meta_name}]", "content", values.get("content", ""))
            elif itemprop == "image":
                self._append("meta[itemprop=image]", "content", values.get("content", ""))
        elif tag_name == "link":
            rels = set(values.get("rel", "").casefold().split())
            is_image_link = bool(
                rels.intersection({"icon", "image_src", "apple-touch-icon", "mask-icon"})
            ) or ("preload" in rels and values.get("as", "").casefold() == "image")
            if is_image_link:
                self._append("link", "href", values.get("href", ""))
                if "imagesrcset" in values:
                    self._append_srcset("link", "imagesrcset", values["imagesrcset"])
            if "stylesheet" in rels:
                self.stylesheets.append(
                    Reference(
                        self.document,
                        "link[rel=stylesheet][href]",
                        values.get("href", ""),
                    )
                )

        if self._svg_depth and tag_name in {"image", "use", "feimage"}:
            for attribute in ("href", "xlink:href"):
                value = values.get(attribute)
                if value is not None and not value.strip().startswith("#"):
                    self._append(f"svg:{tag_name}", attribute, value)

        if "style" in values:
            self.references.extend(
                css_image_references(values["style"], self.document, "inline-style")
            )
        if tag_name == "style":
            self._style_depth += 1

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.casefold()
        if tag_name == "style" and self._style_depth:
            self._style_depth -= 1
            if self._style_depth == 0:
                self.references.extend(
                    css_image_references(
                        "".join(self._style_chunks),
                        self.document,
                        "style",
                    )
                )
                self._style_chunks.clear()
        if tag_name == "svg" and self._svg_depth:
            self._svg_depth -= 1
        if tag_name == "figure" and self._report_hero_depth:
            self._report_hero_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._style_depth:
            self._style_chunks.append(data)


def _issue(
    code: str,
    message: str,
    *,
    root: Path,
    document: Path | None = None,
    surface: str = "",
    url: str = "",
) -> dict[str, str]:
    if document is None:
        document_label = "."
    else:
        try:
            document_label = document.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            document_label = str(document)
    return {
        "code": code,
        "document": document_label,
        "surface": surface,
        "url": url,
        "message": message,
    }


def _read_utf8(path: Path, root: Path, surface: str) -> tuple[str | None, dict[str, str] | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except (OSError, UnicodeError) as exc:
        return None, _issue(
            "unreadable-document",
            f"document cannot be read as UTF-8 ({type(exc).__name__})",
            root=root,
            document=path,
            surface=surface,
        )


def _string_field(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name, "")
    return value.strip() if isinstance(value, str) else ""


def _report_slug(report_url: str) -> str:
    path = unquote(urlparse(report_url).path)
    return Path(path).stem if path else ""


def _image_binding_fields(
    article: dict[str, Any],
) -> tuple[str, Any, Any, Any, Any]:
    image = article.get("image")
    if isinstance(image, str):
        return image.strip(), article.get("image_sha256"), None, None, None
    if not isinstance(image, dict):
        return "", None, None, None, None
    return (
        _string_field(image, "url"),
        image.get("sha256"),
        image.get("width"),
        image.get("height"),
        image.get("media_type"),
    )


def collect_json_export_references(
    root: Path,
) -> tuple[list[Reference], list[ReportHeroBinding], list[dict[str, str]]]:
    references: list[Reference] = []
    bindings: list[ReportHeroBinding] = []
    issues: list[dict[str, str]] = []

    for path in sorted(
        candidate
        for candidate in root.rglob("*.json")
        if candidate.name.casefold() in JSON_IMAGE_EXPORTS
    ):
        text, issue = _read_utf8(path, root, "json-export")
        if issue:
            issues.append(issue)
            continue
        assert text is not None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            issues.append(
                _issue(
                    "invalid-json-export",
                    f"JSON image export cannot be decoded ({exc.__class__.__name__})",
                    root=root,
                    document=path,
                    surface="json-export",
                )
            )
            continue

        if path.name.casefold() == "blog-data.json":
            articles = payload
            base_surface = "$"
        else:
            articles = payload.get("articles") if isinstance(payload, dict) else None
            base_surface = "$.articles"
        if not isinstance(articles, list):
            issues.append(
                _issue(
                    "invalid-json-export",
                    "JSON image export must contain the expected article list",
                    root=root,
                    document=path,
                    surface=base_surface,
                )
            )
            continue

        for index, article in enumerate(articles):
            article_surface = f"{base_surface}[{index}]"
            if not isinstance(article, dict):
                issues.append(
                    _issue(
                        "invalid-json-export",
                        "article entry must be an object",
                        root=root,
                        document=path,
                        surface=article_surface,
                    )
                )
                continue
            image_url, digest, width, height, media_type = _image_binding_fields(
                article
            )
            image_surface = (
                f"{article_surface}.image.url"
                if isinstance(article.get("image"), dict)
                else f"{article_surface}.image"
            )
            references.append(Reference(path, image_surface, image_url))
            report_url = _string_field(article, "url")
            explicit_slug = _string_field(article, "slug")
            bindings.append(
                ReportHeroBinding(
                    document=path,
                    surface=image_surface,
                    title=_string_field(article, "title"),
                    report_url=report_url,
                    slug=explicit_slug or _report_slug(report_url),
                    image_url=image_url,
                    expected_sha256=digest,
                    expected_width=width,
                    expected_height=height,
                    expected_media_type=media_type,
                )
            )

    return references, bindings, issues


def collect_references(
    root: Path,
) -> tuple[
    list[Reference],
    list[Reference],
    list[ReportHeroBinding],
    list[dict[str, str]],
]:
    references: list[Reference] = []
    stylesheets: list[Reference] = []
    report_heroes: list[ReportHeroBinding] = []
    issues: list[dict[str, str]] = []

    for path in sorted(root.rglob("*.html")):
        text, issue = _read_utf8(path, root, "html")
        if issue:
            issues.append(issue)
            continue
        assert text is not None
        parser = ImageSurfaceHTMLParser(path)
        try:
            parser.feed(text)
            parser.close()
        except Exception as exc:
            issues.append(
                _issue(
                    "invalid-html",
                    f"HTML image surfaces cannot be parsed ({type(exc).__name__})",
                    root=root,
                    document=path,
                    surface="html",
                )
            )
            continue
        references.extend(parser.references)
        stylesheets.extend(parser.stylesheets)

    for path in sorted(root.rglob("*.css")):
        text, issue = _read_utf8(path, root, "css")
        if issue:
            issues.append(issue)
            continue
        assert text is not None
        references.extend(css_image_references(text, path, "stylesheet"))

    for path in sorted(root.rglob("*.svg")):
        text, issue = _read_utf8(path, root, "svg")
        if issue:
            issues.append(issue)
            continue
        assert text is not None
        references.extend(css_image_references(text, path, "svg-style"))
        try:
            svg_root = ET.fromstring(text)
        except ET.ParseError as exc:
            issues.append(
                _issue(
                    "undecodable-image",
                    f"SVG is not well-formed XML ({exc.__class__.__name__})",
                    root=root,
                    document=path,
                    surface="svg",
                )
            )
            continue
        for element in svg_root.iter():
            tag_name = str(element.tag).split("}")[-1].casefold()
            if tag_name not in {"image", "use", "feimage"}:
                continue
            for name, value in element.attrib.items():
                attribute = str(name).split("}")[-1].casefold()
                if attribute != "href" or not value.strip() or value.strip().startswith("#"):
                    continue
                rendered_attribute = "xlink:href" if str(name).startswith("{") else "href"
                references.append(
                    Reference(path, f"svg:{tag_name}[{rendered_attribute}]", value.strip())
                )

    json_references, json_report_heroes, json_issues = (
        collect_json_export_references(root)
    )
    references.extend(json_references)
    report_heroes.extend(json_report_heroes)
    issues.extend(json_issues)

    return references, stylesheets, report_heroes, issues


def resolve_local_url(
    root: Path,
    document: Path,
    url: str,
    site_url: str,
) -> tuple[Path | None, str | None]:
    value = html.unescape(url).strip()
    if not value:
        return None, "missing-url"
    if value.casefold().startswith("data:"):
        return None, "data-url"
    if value.startswith("//"):
        return None, "remote-url"
    if "\\" in value or "\x00" in value:
        return None, "path-escaping"

    parsed = urlparse(value)
    canonical = urlparse(site_url.rstrip("/"))
    site_prefix = canonical.path.rstrip("/")
    if parsed.scheme or parsed.netloc:
        if (
            parsed.scheme.casefold() != canonical.scheme.casefold()
            or parsed.netloc.casefold() != canonical.netloc.casefold()
        ):
            return None, "remote-url"
        if not (parsed.path == site_prefix or parsed.path.startswith(site_prefix + "/")):
            return None, "path-escaping"

    path_text = unquote(parsed.path)
    if not path_text:
        return None, "missing-url"
    if "\\" in path_text or "\x00" in path_text:
        return None, "path-escaping"

    if path_text.startswith("/"):
        if path_text == site_prefix:
            relative = ""
        elif path_text.startswith(site_prefix + "/"):
            relative = path_text[len(site_prefix) + 1 :]
        else:
            return None, "path-escaping"
        candidate = root / relative
    else:
        candidate = document.parent / path_text

    candidate = Path(os.path.abspath(candidate))
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError:
        return None, "path-escaping"
    return candidate, None


def has_symlink_component(path: Path, root: Path) -> bool:
    current = root
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_image(path: Path, root: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        if has_symlink_component(path, root):
            return None, "symbolic links are not allowed"
        if not path.is_file():
            return None, "file is missing"
        size = path.stat().st_size
        if size <= 0:
            return None, "file is empty"
    except OSError as exc:
        return None, f"file is unreadable ({type(exc).__name__})"

    relative = path.relative_to(root).as_posix()
    if path.suffix.casefold() == ".svg":
        try:
            svg_root = ET.parse(path).getroot()
        except (OSError, ET.ParseError) as exc:
            return None, f"invalid SVG ({type(exc).__name__})"
        if str(svg_root.tag).split("}")[-1].casefold() != "svg":
            return None, "invalid SVG root"
        return {
            "format": "SVG",
            "height": None,
            "path": relative,
            "sha256": _hash_file(path),
            "size": size,
            "width": None,
        }, None

    try:
        from PIL import Image

        with Image.open(path) as probe:
            verified_format = str(probe.format or "").upper()
            verified_size = tuple(probe.size)
            probe.verify()
        with Image.open(path) as image:
            frame_count = int(getattr(image, "n_frames", 1))
            for frame in range(frame_count):
                image.seek(frame)
                image.load()
            loaded_format = str(image.format or "").upper()
            loaded_size = tuple(image.size)
        if verified_format != loaded_format or verified_size != loaded_size:
            return None, "bitmap metadata changed between verify and full decode"
        if loaded_size[0] <= 0 or loaded_size[1] <= 0:
            return None, "bitmap has invalid dimensions"
        expected_format = str(
            Image.registered_extensions().get(path.suffix.casefold(), "")
        ).upper()
        if not expected_format or expected_format != loaded_format:
            return None, (
                f"extension {path.suffix or '(none)'} does not match decoded "
                f"{loaded_format or 'unknown'}"
            )
    except ImportError:
        return None, "Pillow is required to decode bitmap images"
    except Exception as exc:
        return None, f"invalid bitmap ({type(exc).__name__})"

    return {
        "format": loaded_format,
        "height": loaded_size[1],
        "path": relative,
        "sha256": _hash_file(path),
        "size": size,
        "width": loaded_size[0],
    }, None


def visible_pixel_diversity_problem(image: Any) -> str | None:
    """Return a reason when decoded pixels would render as blank or near-blank."""
    width, height = image.size
    if width <= 0 or height <= 0:
        return "invalid dimensions"
    columns = min(PIXEL_SAMPLE_COLUMNS, width)
    rows = min(PIXEL_SAMPLE_ROWS, height)
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    buckets: dict[tuple[int, int, int], int] = {}
    minimum = [255, 255, 255]
    maximum = [0, 0, 0]

    for sample_y in range(rows):
        y = ((2 * sample_y + 1) * height) // (2 * rows)
        for sample_x in range(columns):
            x = ((2 * sample_x + 1) * width) // (2 * columns)
            red, green, blue, alpha = pixels[x, y]
            visible = (
                (red * alpha + 255 * (255 - alpha) + 127) // 255,
                (green * alpha + 255 * (255 - alpha) + 127) // 255,
                (blue * alpha + 255 * (255 - alpha) + 127) // 255,
            )
            bucket = tuple(channel >> 4 for channel in visible)
            buckets[bucket] = buckets.get(bucket, 0) + 1
            for channel, value in enumerate(visible):
                minimum[channel] = min(minimum[channel], value)
                maximum[channel] = max(maximum[channel], value)

    sample_count = columns * rows
    dominant_count = max(buckets.values())
    if len(buckets) < MIN_VISIBLE_COLOR_BUCKETS:
        return f"only {len(buckets)} coarse visible color buckets"
    if dominant_count * 100 >= sample_count * MAX_DOMINANT_COLOR_PERCENT:
        dominant_percent = 100 * dominant_count / sample_count
        return f"one coarse visible color covers {dominant_percent:.2f}% of samples"
    channel_span = max(high - low for low, high in zip(minimum, maximum))
    if channel_span < MIN_VISIBLE_CHANNEL_SPAN:
        return f"maximum visible channel span is only {channel_span}"
    return None


def validate_report_heroes(
    root: Path,
    site_url: str,
    bindings: list[ReportHeroBinding],
    resolved_references: list[dict[str, str]],
    images: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    issues: list[dict[str, str]] = []
    image_by_path = {image["path"]: image for image in images}
    hero_paths: set[str] = set()
    rendered_by_report: dict[str, list[str]] = {}
    manifest_bindings: dict[str, dict[str, Any]] = {}
    export_slugs: dict[str, set[str]] = {}

    def expected_content_addressed_asset(slug: str, digest: str) -> str:
        return f"assets/heroes/{slug}-{digest[:16]}.png"

    def register_manifest_binding(
        *,
        slug: str,
        report_path: str,
        asset_path: str,
        title: str,
        document: Path,
        surface: str,
    ) -> None:
        source = {
            "document": document.resolve().relative_to(root).as_posix(),
            "surface": surface,
        }
        existing = manifest_bindings.get(slug)
        if existing is None:
            manifest_bindings[slug] = {
                "asset_path": asset_path,
                "report_path": report_path,
                "slug": slug,
                "sources": [source],
                "title": title,
            }
            return
        if (
            existing["asset_path"] != asset_path
            or existing["report_path"] != report_path
            or (existing["title"] and title and existing["title"] != title)
        ):
            issues.append(
                _issue(
                    "report-hero-binding-drift",
                    "report exports or rendered HTML bind the same slug to different content",
                    root=root,
                    document=document,
                    surface=surface,
                    url=asset_path,
                )
            )
            return
        if title and not existing["title"]:
            existing["title"] = title
        if source not in existing["sources"]:
            existing["sources"].append(source)

    for reference in resolved_references:
        if not reference["surface"].startswith("report-hero:img["):
            continue
        hero_paths.add(reference["path"])
        if reference["surface"] != "report-hero:img[src]":
            continue
        rendered_by_report.setdefault(reference["document"], []).append(
            reference["path"]
        )

    for report_path, asset_paths in sorted(rendered_by_report.items()):
        report = Path(report_path)
        slug = report.stem
        rendered_asset = asset_paths[0] if len(asset_paths) == 1 else ""
        rendered_image = image_by_path.get(rendered_asset)
        expected_asset = (
            expected_content_addressed_asset(slug, rendered_image["sha256"])
            if rendered_image is not None
            else ""
        )
        if (
            len(asset_paths) != 1
            or report.parent.as_posix() != "posts"
            or asset_paths[0] != expected_asset
        ):
            issues.append(
                _issue(
                    "report-hero-binding-drift",
                    "rendered report hero must use the slug-and-content-hash local PNG in assets/heroes",
                    root=root,
                    document=root / report,
                    surface="report-hero:img[src]",
                    url=", ".join(asset_paths),
                )
            )
            continue
        register_manifest_binding(
            slug=slug,
            report_path=report_path,
            asset_path=asset_paths[0],
            title="",
            document=root / report,
            surface="report-hero:img[src]",
        )

    for binding in bindings:
        export_slugs.setdefault(binding.document.name.casefold(), set())
        if binding.slug:
            export_slugs[binding.document.name.casefold()].add(binding.slug)
        report_path, report_failure = resolve_local_url(
            root,
            binding.document,
            binding.report_url,
            site_url,
        )
        asset_path, asset_failure = resolve_local_url(
            root,
            binding.document,
            binding.image_url,
            site_url,
        )
        if (
            not binding.title
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", binding.slug)
            or report_failure
            or report_path is None
            or not report_path.is_file()
            or report_path.suffix.casefold() != ".html"
        ):
            issues.append(
                _issue(
                    "report-hero-binding-drift",
                    "article export must identify one local report HTML file with a title and canonical slug",
                    root=root,
                    document=binding.document,
                    surface=binding.surface,
                    url=binding.report_url,
                )
            )
            continue
        if asset_failure or asset_path is None:
            continue

        report_relative = report_path.relative_to(root).as_posix()
        asset_relative = asset_path.relative_to(root).as_posix()
        expected_report = f"posts/{binding.slug}.html"
        image = image_by_path.get(asset_relative)
        digest = (
            binding.expected_sha256
            if isinstance(binding.expected_sha256, str)
            and re.fullmatch(r"[0-9a-f]{64}", binding.expected_sha256)
            else image["sha256"] if image is not None else ""
        )
        expected_asset = (
            expected_content_addressed_asset(binding.slug, digest)
            if digest
            else ""
        )
        hero_paths.add(asset_relative)
        if (
            report_relative != expected_report
            or asset_relative != expected_asset
            or rendered_by_report.get(report_relative) != [asset_relative]
        ):
            issues.append(
                _issue(
                    "report-hero-binding-drift",
                    "article export slug, report URL, image URL, and rendered hero do not bind to one local asset",
                    root=root,
                    document=binding.document,
                    surface=binding.surface,
                    url=binding.image_url,
                )
            )
        register_manifest_binding(
            slug=binding.slug,
            report_path=report_relative,
            asset_path=asset_relative,
            title=binding.title,
            document=binding.document,
            surface=binding.surface,
        )

        if image is None:
            continue
        if binding.expected_sha256 is not None and (
            not isinstance(binding.expected_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", binding.expected_sha256) is None
            or binding.expected_sha256 != image["sha256"]
        ):
            issues.append(
                _issue(
                    "report-hero-hash-drift",
                    "article export hero hash does not match the staged asset bytes",
                    root=root,
                    document=binding.document,
                    surface=binding.surface,
                    url=binding.image_url,
                )
            )
        expected_metadata = (
            binding.expected_width,
            binding.expected_height,
            binding.expected_media_type,
        )
        if any(value is not None for value in expected_metadata) and expected_metadata != (
            image["width"],
            image["height"],
            "image/png",
        ):
            issues.append(
                _issue(
                    "report-hero-metadata-drift",
                    "article export hero dimensions or media type do not match the staged asset",
                    root=root,
                    document=binding.document,
                    surface=binding.surface,
                    url=binding.image_url,
                )
            )

    if set(JSON_IMAGE_EXPORTS).issubset(export_slugs):
        blog_only = export_slugs["blog-data.json"] - export_slugs["publication-data.json"]
        publication_only = (
            export_slugs["publication-data.json"] - export_slugs["blog-data.json"]
        )
        if blog_only or publication_only:
            details = ", ".join(sorted(blog_only | publication_only))
            issues.append(
                _issue(
                    "report-hero-export-drift",
                    "blog-data.json and publication-data.json do not contain the same report hero slugs",
                    root=root,
                    surface="json-export",
                    url=details,
                )
            )

    for asset_relative in sorted(hero_paths):
        image = image_by_path.get(asset_relative)
        if image is None:
            continue
        asset_path = root / Path(asset_relative)
        if asset_path.suffix.casefold() != ".png" or image["format"] != "PNG":
            issues.append(
                _issue(
                    "report-hero-format-mismatch",
                    "local report hero extension and decoded format must both be PNG",
                    root=root,
                    document=asset_path,
                    surface="report-hero:asset",
                )
            )
        if (image["width"], image["height"]) != (
            REPORT_HERO_WIDTH,
            REPORT_HERO_HEIGHT,
        ):
            issues.append(
                _issue(
                    "report-hero-bad-dimensions",
                    "local report hero must decode to exactly 1600x900 pixels",
                    root=root,
                    document=asset_path,
                    surface="report-hero:asset",
                )
            )
        try:
            from PIL import Image

            with Image.open(asset_path) as decoded:
                decoded.seek(0)
                decoded.load()
                diversity_problem = visible_pixel_diversity_problem(decoded)
        except Exception as exc:
            diversity_problem = f"pixel inspection failed ({type(exc).__name__})"
        if diversity_problem:
            issues.append(
                _issue(
                    "report-hero-near-blank",
                    f"local report hero has insufficient visible pixel diversity ({diversity_problem})",
                    root=root,
                    document=asset_path,
                    surface="report-hero:asset",
                )
            )

    manifest_records: list[dict[str, Any]] = []
    for slug, record in sorted(manifest_bindings.items()):
        image = image_by_path.get(record["asset_path"])
        if image is None:
            continue
        record["asset_sha256"] = image["sha256"]
        record["sources"] = sorted(
            record["sources"],
            key=lambda source: (source["document"], source["surface"]),
        )
        manifest_records.append(record)

    return issues, manifest_records


def build_manifest(root: Path, site_url: str = DEFAULT_SITE_URL) -> dict[str, Any]:
    root = root.resolve()
    if not root.is_dir():
        raise ValidationFailure(
            [_issue("site-root-missing", "site root is not a directory", root=root)]
        )

    references, stylesheets, report_hero_bindings, issues = collect_references(root)
    resolved_references: list[dict[str, str]] = []
    image_paths: set[Path] = {
        Path(os.path.abspath(path))
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in IMAGE_SUFFIXES
    }

    for stylesheet in stylesheets:
        path, failure = resolve_local_url(
            root,
            stylesheet.document,
            stylesheet.url,
            site_url,
        )
        if failure:
            issues.append(
                _issue(
                    failure,
                    "linked stylesheet cannot be inspected for image URLs",
                    root=root,
                    document=stylesheet.document,
                    surface=stylesheet.surface,
                    url=stylesheet.url,
                )
            )
        elif path is None or not path.is_file() or path.suffix.casefold() != ".css":
            issues.append(
                _issue(
                    "missing-stylesheet",
                    "linked stylesheet is missing or is not a CSS file",
                    root=root,
                    document=stylesheet.document,
                    surface=stylesheet.surface,
                    url=stylesheet.url,
                )
            )

    for reference in references:
        path, failure = resolve_local_url(
            root,
            reference.document,
            reference.url,
            site_url,
        )
        if failure:
            issues.append(
                _issue(
                    failure,
                    "image surface does not resolve to a local artifact file",
                    root=root,
                    document=reference.document,
                    surface=reference.surface,
                    url=reference.url,
                )
            )
            continue
        assert path is not None
        image_paths.add(path)
        resolved_references.append(
            {
                "document": reference.document.resolve().relative_to(root).as_posix(),
                "path": path.relative_to(root).as_posix(),
                "surface": reference.surface,
                "url": reference.url,
            }
        )

    images: list[dict[str, Any]] = []
    for path in sorted(image_paths):
        try:
            path.relative_to(root)
        except ValueError:
            issues.append(
                _issue(
                    "path-escaping",
                    "image path resolves outside the site root",
                    root=root,
                    document=path,
                    surface="asset",
                )
            )
            continue
        image, problem = inspect_image(path, root)
        if problem:
            issues.append(
                _issue(
                    "missing-image" if problem == "file is missing" else "undecodable-image",
                    problem,
                    root=root,
                    document=path,
                    surface="asset",
                )
            )
        else:
            assert image is not None
            images.append(image)

    hero_issues, report_heroes = validate_report_heroes(
        root,
        site_url,
        report_hero_bindings,
        resolved_references,
        images,
    )
    issues.extend(hero_issues)

    if issues:
        raise ValidationFailure(issues)

    return {
        "generator": "site_image_validator/v2",
        "images": sorted(images, key=lambda item: item["path"]),
        "references": sorted(
            resolved_references,
            key=lambda item: (
                item["document"],
                item["surface"],
                item["url"],
                item["path"],
            ),
        ),
        "report_heroes": report_heroes,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "site_url": site_url.rstrip("/"),
    }


def manifest_text(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(manifest_text(manifest), encoding="utf-8", newline="\n")
    temporary.replace(path)


def verify_manifest(path: Path, actual: dict[str, Any], root: Path) -> None:
    try:
        expected_text = path.read_text(encoding="utf-8")
        expected = json.loads(expected_text)
    except FileNotFoundError:
        raise ValidationFailure(
            [
                _issue(
                    "image-manifest-missing",
                    "image-manifest.json is required",
                    root=root,
                    document=path,
                    surface="manifest",
                )
            ]
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationFailure(
            [
                _issue(
                    "image-manifest-invalid",
                    f"image manifest cannot be decoded ({type(exc).__name__})",
                    root=root,
                    document=path,
                    surface="manifest",
                )
            ]
        )
    if expected != actual or expected_text != manifest_text(actual):
        raise ValidationFailure(
            [
                _issue(
                    "image-manifest-drift",
                    "image manifest does not exactly match image surfaces and bytes",
                    root=root,
                    document=path,
                    surface="manifest",
                )
            ]
        )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate every local image surface and its deterministic manifest."
    )
    parser.add_argument("--site-root", type=Path, default=Path("."))
    parser.add_argument("--site-url", default=DEFAULT_SITE_URL)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="Write the manifest after a clean audit instead of comparing it.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = args.site_root.resolve()
    manifest_path = (args.manifest or (root / "image-manifest.json")).resolve()
    try:
        manifest = build_manifest(root, args.site_url)
        if args.write_manifest:
            write_manifest(manifest_path, manifest)
        else:
            verify_manifest(manifest_path, manifest, root)
    except ValidationFailure as exc:
        for issue in exc.issues:
            print(
                f"ERROR {issue['code']}: {issue['document']} "
                f"{issue['surface']} {issue['url']} - {issue['message']}",
                file=sys.stderr,
            )
        return 1

    print(
        f"Validated {len(manifest['images'])} images and "
        f"{len(manifest['references'])} image references."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
