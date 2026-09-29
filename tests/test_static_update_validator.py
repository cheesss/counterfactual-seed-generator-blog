"""Synthetic static-update boundary tests; no real research approvals."""
from __future__ import annotations

import base64
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts import site_release_validator as artifact
from scripts import static_update_validator as validator


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def seal(value: dict) -> dict:
    value.pop("proof_sha256", None)
    value["proof_sha256"] = artifact.canonical_hash(value)
    return value


class StaticUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.baseline, self.site, self.selected = [self.root / name for name in ("baseline", "candidate", "selected")]
        self.baseline.mkdir()
        (self.baseline / "posts").mkdir()
        (self.baseline / "assets/heroes").mkdir(parents=True)
        self.slugs = ["repair-one", "repair-two", *[f"old-{index}" for index in range(64)]]
        rows = [{"slug": slug, "url": f"posts/{slug}.html", "title": f"Old {slug}",
                 "report_stage": {"coverage_status": "legacy-unverified"}} for slug in self.slugs]
        blog = [{"url": row["url"], "title": row["title"], "excerpt": "baseline"} for row in rows]
        for row in rows:
            (self.baseline / row["url"]).write_bytes(f"<article>{row['title']}</article>\r\n".encode())
        for name in validator.IMMUTABLE_ROOTS:
            (self.baseline / name).write_bytes(b"Immutable fixture\r\n")
        for name in validator.TITLE_ROOTS:
            (self.baseline / name).write_bytes(b"Old repair-one; Old repair-two; Old old-0\r\n")
        (self.baseline / "assets/heroes/old-0-photo.png").write_bytes(b"synthetic old asset")
        (self.baseline / "image-manifest.json").write_bytes(b"{}\n")
        write_json(self.baseline / "publication-data.json", {"schema_version": 3, "summary": {"articles": 66}, "articles": rows})
        write_json(self.baseline / "blog-data.json", blog)
        shutil.copytree(self.baseline, self.site)
        self.selected.mkdir()
        (self.selected / "posts").mkdir()
        (self.selected / "assets/heroes").mkdir(parents=True)
        selected_rows = copy.deepcopy(rows[:2])
        selected_blog = copy.deepcopy(blog[:2])
        for row, blog_row in zip(selected_rows, selected_blog):
            row["title"] = f"Reviewed {row['slug']}"
            row["report_stage"] = {"coverage_status": "current"}
            blog_row["title"] = row["title"]
            blog_row["excerpt"] = "reviewed selected fixture"
            (self.selected / row["url"]).write_bytes(f"<article>{row['title']}</article>\n".encode())
            asset = f"assets/heroes/{row['slug']}-photo.png"
            (self.selected / asset).write_bytes(b"synthetic selected asset")
            shutil.copyfile(self.selected / row["url"], self.site / row["url"])
            shutil.copyfile(self.selected / asset, self.site / asset)
        for name in validator.IMMUTABLE_ROOTS:
            shutil.copyfile(self.baseline / name, self.selected / name)
        (self.selected / "404.html").write_bytes(b"Selected-only index aggregate\n")
        write_json(self.selected / "publication-data.json", {"schema_version": 3, "summary": {"articles": 2}, "articles": selected_rows})
        write_json(self.selected / "blog-data.json", selected_blog)
        write_json(self.site / "publication-data.json", {"schema_version": 3, "summary": {"articles": 66}, "articles": selected_rows + rows[2:]})
        write_json(self.site / "blog-data.json", selected_blog + blog[2:])
        records = []
        for row in selected_rows:
            post_hash = artifact.sha256_file(self.selected / row["url"])
            records.append({"slug": row["slug"], "report_sha256": "a" * 64, "post_sha256": post_hash,
                            "stages": [{"stage": stage, "prompt_sha256": "c" * 64,
                                        "raw_sha256": "d" * 64, "section_sha256": "e" * 64}
                                       for stage in artifact.PUBLIC_STAGES],
                            "quality": {"status": "publishable", "report_sha256": "a" * 64,
                                        "post_sha256": post_hash, "review_sha256": "f" * 64,
                                        "blocking_issue_count": 0, "link_health_checked": True}})
        proof = seal({"schema_version": 1, "status": "publication-ready",
                      "required_stages": list(artifact.PUBLIC_STAGES), "contract_sha256": "b" * 64,
                      "articles": records, "files": artifact.artifact_files(self.selected)})
        self.update = {"schema_version": 1, "kind": "static-selected-update-v1", "status": "selected-update-ready",
                       "baseline": {"repository": validator.BASELINE_REPOSITORY, "commit": "1" * 40, "run_id": 123},
                       "selected_release": proof,
                       "selected_artifacts": {name: base64.b64encode((self.selected / name).read_bytes()).decode("ascii")
                                              for name in proof["files"] if name in validator.AGGREGATE_ROOTS}}
        self.refresh()

    def refresh(self) -> None:
        self.update["files"] = validator._files(self.site)
        self.update["baseline_files"] = validator._files(self.baseline)
        write_json(self.site / validator.UPDATE_FILE, seal(self.update))

    def validate(self) -> dict:
        return validator.validate_update(self.site, self.baseline)

    def test_two_verified_repairs_preserve_64_without_archive_approval(self) -> None:
        self.assertEqual({"status": "selected-update-ready", "verified_updates": 2, "preserved_articles": 64}, self.validate())
        with self.assertRaises(artifact.ReleaseFailure):
            artifact.validate_release(self.site)

    def test_exact_aggregate_title_replacements_are_allowed(self) -> None:
        for name in validator.TITLE_ROOTS:
            before = (self.baseline / name).read_bytes()
            (self.site / name).write_bytes(before.replace(b"Old repair-one", b"Reviewed repair-one").replace(b"Old repair-two", b"Reviewed repair-two"))
        self.refresh()
        self.assertEqual(2, self.validate()["verified_updates"])

    def test_preserved_html_drift_even_with_resealed_manifest_is_rejected(self) -> None:
        (self.site / "posts/old-0.html").write_bytes(b"changed")
        self.refresh()
        with self.assertRaisesRegex(validator.ReleaseFailure, "preserved article"):
            self.validate()

    def test_preserved_metadata_drift_is_rejected(self) -> None:
        data = json.loads((self.site / "publication-data.json").read_text())
        data["articles"][2]["report_stage"] = {"coverage_status": "current"}
        write_json(self.site / "publication-data.json", data)
        self.refresh()
        with self.assertRaisesRegex(validator.ReleaseFailure, "preserved article"):
            self.validate()

    def test_preserved_blog_row_drift_is_rejected(self) -> None:
        data = json.loads((self.site / "blog-data.json").read_text())
        data[2]["excerpt"] = "unreviewed change"
        write_json(self.site / "blog-data.json", data)
        self.refresh()
        with self.assertRaisesRegex(validator.ReleaseFailure, "preserved article"):
            self.validate()

    def test_shared_css_and_javascript_cannot_change(self) -> None:
        for name in ("styles.css", "site.js"):
            with self.subTest(name=name):
                original = (self.site / name).read_bytes()
                (self.site / name).write_bytes(b"new shared rendering")
                self.refresh()
                with self.assertRaisesRegex(validator.ReleaseFailure, "selected proof file|immutable shared"):
                    self.validate()
                (self.site / name).write_bytes(original)

    def test_baseline_assets_cannot_change_or_disappear(self) -> None:
        path = self.site / "assets/heroes/old-0-photo.png"
        for change in (lambda: path.write_bytes(b"replacement"), path.unlink):
            change()
            self.refresh()
            with self.assertRaisesRegex(validator.ReleaseFailure, "baseline asset"):
                self.validate()

    def test_extra_unverified_post_cannot_be_hidden_in_full_manifest(self) -> None:
        (self.site / "posts/unverified.html").write_bytes(b"unverified")
        self.refresh()
        with self.assertRaisesRegex(validator.ReleaseFailure, "post inventory"):
            self.validate()

    def test_new_assets_require_selected_prefix_and_v1_proof(self) -> None:
        for name in ("old-0-new.png", "repair-one-unproved.png"):
            with self.subTest(name=name):
                path = self.site / "assets/heroes" / name
                path.write_bytes(b"unproved")
                self.refresh()
                with self.assertRaisesRegex(validator.ReleaseFailure, "new asset"):
                    self.validate()
                path.unlink()

    def test_selected_html_and_quality_proof_drift_rejected(self) -> None:
        (self.site / "posts/repair-one.html").write_bytes(b"unreviewed selected edit")
        self.refresh()
        with self.assertRaisesRegex(validator.ReleaseFailure, "selected proof file"):
            self.validate()
        shutil.copyfile(self.selected / "posts/repair-one.html", self.site / "posts/repair-one.html")
        self.update["selected_release"]["articles"][0]["quality"]["link_health_checked"] = False
        seal(self.update["selected_release"])
        self.refresh()
        with self.assertRaisesRegex(validator.ReleaseFailure, "quality approval"):
            self.validate()

    def test_embedded_aggregate_bytes_are_bound_to_original_v1_proof(self) -> None:
        self.update["selected_artifacts"]["publication-data.json"] = base64.b64encode(b"{}").decode()
        self.refresh()
        with self.assertRaisesRegex(validator.ReleaseFailure, "aggregate hash drift"):
            self.validate()

    def test_selected_metadata_must_equal_original_subset(self) -> None:
        data = json.loads((self.site / "publication-data.json").read_text())
        data["articles"][0]["unreviewed"] = True
        write_json(self.site / "publication-data.json", data)
        self.refresh()
        with self.assertRaisesRegex(validator.ReleaseFailure, "selected metadata"):
            self.validate()

    def test_nonarticle_summary_fields_cannot_change(self) -> None:
        data = json.loads((self.site / "publication-data.json").read_text())
        data["summary"]["articles"] = 2
        write_json(self.site / "publication-data.json", data)
        self.refresh()
        with self.assertRaisesRegex(validator.ReleaseFailure, "non-articles"):
            self.validate()

    def test_expected_recomputed_summary_counts_are_allowed(self) -> None:
        old = json.loads((self.baseline / "publication-data.json").read_text())
        template = {"articles": 66, "data_series": 0, "calendar_year_card_candidates_excluded": 0,
                    "watch": {"tracked_articles": 0, "signals": 0, "resolved": 0,
                              "states": {"open": 0, "confirmed": 0}, "extraction_modes": {"explicit_bullet": 0},
                              "unstructured_watch_sections": 0, "unpublished_prose_candidates": 0,
                              "unknown_watch": "preserved"},
                    "report_stage": {"complete_by_sections": 0, "complete_by_run_manifest": 0,
                                     "coverage_statuses": {"legacy-unverified": 64, "current": 2}},
                    "taxonomy": {"sectors": {"defense": 0}, "analysis_lenses": {"constraint": 0},
                                 "system_patterns": {"permits": 0}},
                    "outcome_ledger": {"entries": 4, "retired": 4}, "unknown": {"keep": True}}
        old["summary"] = validator.summary_for_rows(template, old["articles"])
        write_json(self.baseline / "publication-data.json", old)
        new = json.loads((self.site / "publication-data.json").read_text())
        row = new["articles"][0]
        row.update({"data_series": [{"headline": {"is_calendar_year": True}}, {"headline": {}}],
                    "watch_signals": [{"state": "open", "extraction_mode": "explicit_bullet"},
                                      {"state": "confirmed", "extraction_mode": "explicit_bullet"}],
                    "watch_extraction": {"unpublished_candidate_count": 3},
                    "sector": {"key": "defense"}, "analysis_lens": {"key": "constraint"},
                    "system_pattern": {"key": "permits"},
                    "report_stage": {"coverage_status": "current", "public_cycle_complete_by_sections": True,
                                     "public_cycle_complete_by_run_manifest": True}})
        new["summary"] = validator.summary_for_rows(old["summary"], new["articles"])
        self.assertEqual(2, new["summary"]["data_series"])
        self.assertEqual(2, new["summary"]["watch"]["signals"])
        self.assertEqual(1, new["summary"]["watch"]["resolved"])
        self.assertEqual(1, new["summary"]["report_stage"]["complete_by_run_manifest"])
        self.assertEqual({"defense": 1}, new["summary"]["taxonomy"]["sectors"])
        self.assertEqual(old["summary"]["outcome_ledger"], new["summary"]["outcome_ledger"])
        self.assertEqual("preserved", new["summary"]["watch"]["unknown_watch"])
        write_json(self.site / "publication-data.json", new)
        subset = json.loads((self.selected / "publication-data.json").read_text())
        subset["articles"][0] = row
        write_json(self.selected / "publication-data.json", subset)
        self.update["selected_release"]["files"]["publication-data.json"] = artifact.sha256_file(self.selected / "publication-data.json")
        seal(self.update["selected_release"])
        self.update["selected_artifacts"]["publication-data.json"] = base64.b64encode((self.selected / "publication-data.json").read_bytes()).decode()
        self.refresh()
        self.assertEqual(2, self.validate()["verified_updates"])
        new["summary"]["watch"]["signals"] = 999
        write_json(self.site / "publication-data.json", new)
        self.refresh()
        with self.assertRaisesRegex(validator.ReleaseFailure, "non-articles"):
            self.validate()

    def test_summary_histograms_include_observed_buckets_and_preserve_old_zeros(self) -> None:
        summary = {"report_stage": {"coverage_statuses": {"old-status": 9}},
                   "watch": {"states": {"broken": 3}, "extraction_modes": {"old-mode": 4}},
                   "taxonomy": {"system_patterns": {"old-pattern": 2}}}
        rows = [{"report_stage": {"coverage_status": "complete"},
                 "watch_signals": [{"state": "open", "extraction_mode": "explicit_bullet"}],
                 "system_pattern": {"key": "permits"}} for _ in range(66)]
        result = validator.summary_for_rows(summary, rows)
        self.assertEqual({"old-status": 0, "complete": 66}, result["report_stage"]["coverage_statuses"])
        self.assertEqual(66, sum(result["report_stage"]["coverage_statuses"].values()))
        self.assertEqual({"broken": 0, "open": 66}, result["watch"]["states"])
        self.assertEqual({"old-mode": 0, "explicit_bullet": 66}, result["watch"]["extraction_modes"])
        self.assertEqual({"old-pattern": 0, "permits": 66}, result["taxonomy"]["system_patterns"])
        self.assertEqual(9, summary["report_stage"]["coverage_statuses"]["old-status"])

    def test_arbitrary_aggregate_edit_is_rejected(self) -> None:
        (self.site / "index.html").write_bytes(b"new unreviewed index")
        self.refresh()
        with self.assertRaisesRegex(validator.ReleaseFailure, "exact title replacements"):
            self.validate()

    def test_malformed_proof_types_and_identity_are_rejected(self) -> None:
        original = copy.deepcopy(self.update)
        for field, value in (("schema_version", True), ("articles", [None]), ("files", [])):
            with self.subTest(field=field):
                self.update = copy.deepcopy(original)
                self.update["selected_release"][field] = value
                seal(self.update["selected_release"])
                self.refresh()
                with self.assertRaises(validator.ReleaseFailure):
                    self.validate()
        self.update = copy.deepcopy(original)
        self.update["selected_release"]["articles"][0]["quality"]["blocking_issue_count"] = False
        seal(self.update["selected_release"])
        self.refresh()
        with self.assertRaisesRegex(validator.ReleaseFailure, "quality types"):
            self.validate()
        self.update = copy.deepcopy(original)
        self.update["selected_release"]["articles"][0]["slug"] = "../escape"
        seal(self.update["selected_release"])
        self.refresh()
        with self.assertRaises(validator.ReleaseFailure):
            self.validate()

    def test_selected_proof_hash_cannot_drift(self) -> None:
        self.update["selected_release"]["proof_sha256"] = "0" * 64
        self.refresh()
        with self.assertRaisesRegex(validator.ReleaseFailure, "publication proof hash"):
            self.validate()

    def test_selected_jpg_under_images_directory_is_accepted(self) -> None:
        name = "assets/images/repair-one-photo.jpg"
        for root in (self.selected, self.site):
            path = root / name
            path.parent.mkdir(parents=True)
            path.write_bytes(b"synthetic jpg bytes; decoder validation belongs to image gate")
        self.update["selected_release"]["files"][name] = artifact.sha256_file(self.site / name)
        seal(self.update["selected_release"])
        self.refresh()
        self.assertEqual(2, self.validate()["verified_updates"])

    def test_existing_shared_jpg_can_be_carried_in_selected_subset(self) -> None:
        name = "assets/images/shared-photo.jpg"
        for root in (self.baseline, self.selected, self.site):
            path = root / name
            path.parent.mkdir(parents=True)
            path.write_bytes(b"synthetic shared asset")
        self.update["selected_release"]["files"][name] = artifact.sha256_file(self.site / name)
        seal(self.update["selected_release"])
        self.refresh()
        self.assertEqual(2, self.validate()["verified_updates"])

    def install_replaced_og(self) -> str:
        name = "assets/og/repair-one.png"
        for root in (self.baseline, self.site, self.selected):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"old OG" if root == self.baseline else b"approved selected OG")
        self.update["selected_release"]["files"][name] = artifact.sha256_file(self.selected / name)
        seal(self.update["selected_release"])
        self.refresh()
        return name

    def test_exclusive_approved_selected_og_replacement_is_allowed(self) -> None:
        name = self.install_replaced_og()
        (self.baseline / "posts/repair-one.html").write_text(f'<meta property="og:image" content="../{name}">')
        self.refresh()
        self.assertEqual(2, self.validate()["verified_updates"])

    def test_selected_og_replacement_rejects_preserved_html_and_root_references(self) -> None:
        name = self.install_replaced_og()
        for page, url in (("posts/old-0.html", "../" + name),
                          ("index.html", "/counterfactual-seed-generator-blog/" + name),
                          ("404.html", "https://example.test/" + name.replace("/", "%2F")),
                          ("posts/old-0.html", "../assets/og/../og/repair-one.png")):
            with self.subTest(page=page, url=url):
                originals = [(root / page).read_bytes() for root in (self.baseline, self.site)]
                for root in (self.baseline, self.site):
                    (root / page).write_text(f'<meta content="{url}">', encoding="utf-8")
                self.refresh()
                with self.assertRaisesRegex(validator.ReleaseFailure, "exclusive approved selected OG"):
                    self.validate()
                for root, raw in zip((self.baseline, self.site), originals):
                    (root / page).write_bytes(raw)

    def test_selected_og_replacement_rejects_nonselected_export_reference(self) -> None:
        name = self.install_replaced_og()
        for filename, field in (("publication-data.json", "nested"), ("blog-data.json", "image")):
            with self.subTest(filename=filename):
                originals = [(root / filename).read_bytes() for root in (self.baseline, self.site)]
                for root in (self.baseline, self.site):
                    data = json.loads((root / filename).read_text())
                    row = data["articles"][2] if isinstance(data, dict) else data[2]
                    row[field] = {"url": "../" + name} if field == "nested" else "/" + name
                    write_json(root / filename, data)
                self.refresh()
                with self.assertRaisesRegex(validator.ReleaseFailure, "exclusive approved selected OG"):
                    self.validate()
                for root, raw in zip((self.baseline, self.site), originals):
                    (root / filename).write_bytes(raw)

    def test_selected_og_deletion_is_rejected(self) -> None:
        name = self.install_replaced_og()
        (self.site / name).unlink()
        self.update["selected_release"]["files"].pop(name)
        seal(self.update["selected_release"])
        self.refresh()
        with self.assertRaisesRegex(validator.ReleaseFailure, "deleted"):
            self.validate()

    def test_unselected_og_replacement_remains_rejected_even_with_proof_digest(self) -> None:
        name = "assets/og/old-0.png"
        for root in (self.baseline, self.site, self.selected):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"old OG" if root == self.baseline else b"unselected replacement")
        self.update["selected_release"]["files"][name] = artifact.sha256_file(self.selected / name)
        seal(self.update["selected_release"])
        self.refresh()
        with self.assertRaisesRegex(validator.ReleaseFailure, "exclusive approved selected OG"):
            self.validate()

    def test_metadata_exclusion_matches_v1_artifact_inventory(self) -> None:
        (self.site / "image-manifest.json").write_bytes(b"regenerated separately")
        (self.site / ".generated-site-manifest.json").write_bytes(b"ownership metadata")
        self.refresh()
        self.assertEqual(artifact.artifact_files(self.site), validator.artifact_files(self.site))
        self.assertEqual(artifact.artifact_files(self.baseline), self.update["baseline_files"])
        self.assertEqual(2, self.validate()["verified_updates"])

    def test_new_slug_cannot_enter_a_repair_only_update(self) -> None:
        data = json.loads((self.site / "publication-data.json").read_text())
        data["articles"].append({"slug": "new-article", "url": "posts/new-article.html", "title": "New"})
        write_json(self.site / "publication-data.json", data)
        (self.site / "posts/new-article.html").write_bytes(b"new")
        self.refresh()
        with self.assertRaisesRegex(validator.ReleaseFailure, "preserve all baseline slugs"):
            self.validate()

    def test_404_arbitrary_edit_is_rejected(self) -> None:
        (self.site / "404.html").write_bytes(b"unreviewed 404 content")
        self.refresh()
        with self.assertRaisesRegex(validator.ReleaseFailure, "exact title replacements"):
            self.validate()

    def test_mixed_update_cannot_claim_full_v1_approval(self) -> None:
        write_json(self.site / artifact.RELEASE_FILE, self.update["selected_release"])
        self.refresh()
        with self.assertRaisesRegex(validator.ReleaseFailure, "full publication release"):
            self.validate()

    def test_baseline_manifest_is_not_trusted_without_actual_tree(self) -> None:
        (self.baseline / "posts/old-0.html").write_bytes(b"different independent baseline")
        with self.assertRaisesRegex(validator.ReleaseFailure, "independent baseline"):
            self.validate()

    def test_full_candidate_hash_drift_and_proof_hash_drift_rejected(self) -> None:
        (self.site / "index.html").write_bytes(b"changed")
        with self.assertRaisesRegex(validator.ReleaseFailure, "candidate file"):
            self.validate()
        self.update["proof_sha256"] = "0" * 64
        write_json(self.site / validator.UPDATE_FILE, self.update)
        with self.assertRaisesRegex(validator.ReleaseFailure, "proof hash"):
            self.validate()

    def test_malformed_binding_path_and_duplicate_json_rejected(self) -> None:
        original = copy.deepcopy(self.update)
        for field, value in (("commit", "../main"), ("run_id", True), ("repository", "other/repo")):
            self.update = copy.deepcopy(original)
            self.update["baseline"][field] = value
            self.refresh()
            with self.assertRaisesRegex(validator.ReleaseFailure, "binding"):
                self.validate()
        self.update = copy.deepcopy(original)
        self.update["selected_release"]["files"]["../escape"] = "a" * 64
        self.refresh()
        with self.assertRaisesRegex(validator.ReleaseFailure, "path"):
            self.validate()
        (self.site / validator.UPDATE_FILE).write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(validator.ReleaseFailure, "duplicate JSON"):
            self.validate()

    def test_independent_root_required(self) -> None:
        with self.assertRaisesRegex(validator.ReleaseFailure, "independent"):
            validator.validate_update(self.site, self.site)

    def test_symlinks_rejected(self) -> None:
        path = self.site / "assets/heroes/repair-one-link.png"
        try:
            path.symlink_to(self.baseline / "assets/heroes/old-0-photo.png")
        except OSError:
            self.skipTest("host does not permit creating symlinks")
        with self.assertRaisesRegex(validator.ReleaseFailure, "symbolic link"):
            self.validate()


if __name__ == "__main__":
    unittest.main()
