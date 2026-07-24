# Counterfactual Seed Generator Blog

Static GitHub Pages output for the Counterfactual Seed Generator project.

This repository contains the published blog artifact plus the validation and deployment controls used to host it with GitHub Pages.

## Deployment boundary

GitHub Pages must deploy only the artifact uploaded by `.github/workflows/pages.yml`. The workflow stages files listed in `.generated-site-manifest.json`, verifies every file hash, validates `image-manifest.json` against the staged bytes and rendered image surfaces, and only then calls `actions/upload-pages-artifact` and `actions/deploy-pages`.

The branch working directory is never the Pages artifact. `README.md`, `.github/`, `scripts/`, `tests/`, unmanifested files, and stale branch files are excluded by construction.

Image validation covers HTML `img` and `source` URLs and `srcset`, social image metadata, video posters, image inputs, icons and image preloads, inline and linked CSS `url(...)`, and SVG `image`, `use`, and `feImage` hrefs. It rejects external origins other than the configured Pages origin, data URLs, missing URLs/files, paths outside the artifact, symlinks, undecodable images, extension/format mismatches, and manifest drift.

## Source sync

After the first adoption, run the sync from `counterfactual-seed-generator-obsidian-export` after generating `docs/` and passing its publication checks:

```powershell
python scripts/sync_static_site.py --dry-run
python scripts/sync_static_site.py
```

The first adoption of an existing target that has no `.generated-site-manifest.json` is explicit:

```powershell
python scripts/sync_static_site.py --dry-run --bootstrap
python scripts/sync_static_site.py --bootstrap
```

`--bootstrap` may be used only once. It replaces collisions only within the checked generated-site allowlist and does not delete pre-existing unowned files. Later syncs refuse to overwrite or delete a manifest-owned target file whose hash changed locally. The command validates a temporary complete artifact before touching this repository, copies all generated pages and assets, writes deterministic image and ownership manifests, and never commits or pushes.

Local target checks require Pillow:

```powershell
python -m pip install pillow==12.2.0
python -m unittest discover -s tests -p "test_*.py" -v
```

## Required GitHub settings

Repository files cannot enforce these settings. Configure them in GitHub before relying on this boundary:

1. In **Settings > Pages > Build and deployment**, set **Source** to **GitHub Actions**. Do not select a branch/folder source.
2. Protect `main` and require the **Validate Pages artifact** status check before merge. Disable direct pushes and bypasses for normal maintainers where the repository policy permits.
3. In **Settings > Environments > github-pages**, restrict deployment branches to protected branches. Optionally require reviewers when production publication needs an explicit approval.

Until the Pages source is switched to GitHub Actions, GitHub can still publish branch files without this workflow, regardless of the checks in the repository.

