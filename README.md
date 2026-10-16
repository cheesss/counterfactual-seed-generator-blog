# Counterfactual Seed Generator Blog

Static GitHub Pages output for the Counterfactual Seed Generator project.

This repository contains the published blog artifact plus the validation and deployment controls used to host it with GitHub Pages.

## Deployment boundary

GitHub Pages must deploy only the artifact uploaded by `.github/workflows/pages.yml`. The workflow stages files listed in `.generated-site-manifest.json`, verifies every file hash, checks `.publication-release.json` for the full current research and final-review attestations, validates `image-manifest.json` against the staged bytes and rendered image surfaces, and only then uploads and deploys the Pages artifact.

The branch working directory is never the Pages artifact. `README.md`, `.github/`, `scripts/`, `tests/`, unmanifested files, and stale branch files are excluded by construction.

Image validation covers HTML `img` and `source` URLs and `srcset`, social image metadata, video posters, image inputs, icons and image preloads, inline and linked CSS `url(...)`, and SVG `image`, `use`, and `feImage` hrefs. It rejects external origins other than the configured Pages origin, data URLs, missing URLs/files, paths outside the artifact, symlinks, undecodable images, extension/format mismatches, and manifest drift.

## Automated checks

Every workflow is deterministic. None researches, reviews, publishes, commits or merges.

- **Pull requests:** the `Validate Pages artifact` job also writes an informational step summary (`scripts/pr_summary.py`): proof type, proof articles by profile, carried `legacy-deployed-v1` and photo-revalidated counts, changed posts, image count and open/dated watch-signal deltas against the base. It never gates the check.
- **After a push deploy:** the `verify-live` job (`scripts/verify_live.py`) fetches every manifest text file plus the binaries changed by the push from the deployed URL and compares SHA-256, retrying for up to 10 minutes of CDN lag before failing the run.
- **Weekly, notify-only issues.** Each keeps one labelled issue current, creates it only when something needs action and closes it when clean:
  - `forecast-due.yml` (also on pushes to `main` that change `publication-data.json`): open watch signals past their dated checkpoint, plus cadence against 8 articles per 28 days.
  - `source-links.yml`: article-body source links in `posts/*.html`. Only 404/410 count as dead; 401/403/429/5xx and timeouts are reported as unverified. Requests identify as `BottleneckBriefsBot`, are spaced per host, honour `Retry-After` and are capped.
  - `settings-audit.yml`: Pages `build_type`, `main` protection and the `github-pages` environment policy. Branch protection needs the optional `SETTINGS_AUDIT_TOKEN` secret, a fine-grained token for this repository with **Administration: Read-only** only. Without it, that part is skipped and the step summary says how to add it.

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

## GitHub settings and release flow

These settings live in GitHub, not in repository files. They were applied on 2026-10-12; re-check them with `gh api repos/cheesss/counterfactual-seed-generator-blog/pages` and `.../branches/main/protection` rather than trusting this note:

1. **Pages source:** GitHub Actions (`build_type: workflow`). Branch/folder publishing is off, so only the validated artifact from `.github/workflows/pages.yml` can go live.
2. **`main` protection:** a pull request is required, the **Validate Pages artifact** check must pass, the rule applies to administrators, and force pushes and deletion are blocked.
3. **`github-pages` environment:** deploys only from protected branches; administrators cannot bypass it.

Release changes reach `main` only through a pull request. From the source vault, after `run_publication_cycle.py --publish` has synchronized this working tree:

```powershell
python scripts/release_handoff.py open-pr      # commits the mirror on release/<date>-<vault sha> and opens a PR
# merge the PR in GitHub after "Validate Pages artifact" passes
python scripts/release_handoff.py verify-live  # waits for the deployment and compares live bytes with the manifest
```

`open-pr` refuses an unpushed vault commit or one whose vault CI is not green unless `--allow-unverified-vault "<reason>"` records why in the PR. It never merges.

Reader issue templates (member pack interest, feedback and corrections) live in `.github/ISSUE_TEMPLATE/`; `.github/` is never part of the Pages artifact.

