# Counterfactual Seed Generator Blog

Static GitHub Pages output for the Counterfactual Seed Generator project.

This repository contains the published blog artifact plus the validation and deployment controls used to host it with GitHub Pages.

## Deployment boundary

GitHub Pages must deploy only the artifact uploaded by `.github/workflows/pages.yml`. The workflow stages files listed in `.generated-site-manifest.json`, verifies every file hash, checks `.publication-release.json` for the full current research and final-review attestations, validates `image-manifest.json` against the staged bytes and rendered image surfaces, and only then uploads and deploys the Pages artifact.

The branch working directory is never the Pages artifact. `README.md`, `.github/`, `scripts/`, `tests/`, unmanifested files, and stale branch files are excluded by construction.

Image validation covers HTML `img` and `source` URLs and `srcset`, social image metadata, video posters, image inputs, icons and image preloads, inline and linked CSS `url(...)`, and SVG `image`, `use`, and `feImage` hrefs. It rejects external origins other than the configured Pages origin, data URLs, missing URLs/files, paths outside the artifact, symlinks, undecodable images, extension/format mismatches, and manifest drift.

## Source sync

The canonical source is the sibling `counterfactual-seed-generator-obsidian-export` repository. Never repair research or edit generated article HTML here. Complete the source pipeline and its release proof first:

```powershell
python scripts/run_publication_cycle.py --all --audit
python scripts/run_publication_cycle.py --report "<existing-report.md>" --publish
python scripts/run_publication_cycle.py --all --publish
```

These commands run from the source repository. `--publish` verifies and promotes local artifacts; it does not push or confirm a remote deployment. A direct renderer build or a selected editor pass is not an approved release.

Selected article repairs use `.publication-update.json`, not a fabricated complete-archive approval. Pages checks the actual successful baseline run and committed files, complete current research/review proof for each repaired article, and unchanged archive content, metadata, shared styles and assets. Only a selected article's exclusively referenced, approved social image may replace an existing image. Aggregate titles and deterministic counts are refreshed from the proved selected rows. This static-only profile cannot authorize member releases. New articles or shared/archive changes require the complete release path.

After the first adoption, synchronize an already approved source artifact:

```powershell
python scripts/sync_static_site.py --dry-run
python scripts/sync_static_site.py
```

Even `--dry-run` requires a valid source release proof. The first adoption of an existing target that has no `.generated-site-manifest.json` is explicit:

```powershell
python scripts/sync_static_site.py --dry-run --bootstrap
python scripts/sync_static_site.py --bootstrap
```

`--bootstrap` may be used only once. It replaces collisions only within the checked generated-site allowlist and does not delete pre-existing unowned files. Later syncs refuse to overwrite or delete a manifest-owned target file whose hash changed locally. The command validates a temporary complete artifact before touching this repository, copies all generated pages and assets, writes deterministic image and ownership manifests, and never commits or pushes.

`--preview-only` may write an unapproved preview only to a separate local directory, never this deployment repository. Research receipts and prompts remain in the source project's external runtime. The public proof contains final hashes and attestations, not internal notes or browser logs.

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

