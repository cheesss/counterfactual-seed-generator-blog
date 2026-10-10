from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import site_release_validator as release

from publication_proof_fixtures import append_repair, integrate_proof, invalid_scoped_records, stage_proof


class SiteReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "posts").mkdir()
        post = self.root / "posts/verified.html"
        post.write_text("<article>Fixture</article>\n", encoding="utf-8")
        (self.root / "index.html").write_text("Index", encoding="utf-8")
        (self.root / "publication-data.json").write_text(json.dumps({"articles": [
            {"slug": "verified", "url": "posts/verified.html"},
        ]}), encoding="utf-8")
        digest = release.sha256_file(post)
        self.proof = {
            "schema_version": release.SCHEMA_VERSION, "status": "publication-ready",
            "contract_sha256": "a" * 64, "required_stages": list(release.PUBLIC_STAGES),
            "files": release.artifact_files(self.root),
            "articles": [{"slug": "verified", "report_sha256": "b" * 64, "post_sha256": digest,
                          "stages": [{"stage": stage, "prompt_sha256": "c" * 64,
                                      "raw_sha256": "d" * 64, "section_sha256": "e" * 64}
                                     for stage in release.PUBLIC_STAGES],
                          "quality": {"status": "publishable", "report_sha256": "b" * 64,
                                      "post_sha256": digest, "review_sha256": "f" * 64,
                                      "blocking_issue_count": 0, "link_health_checked": True}}],
        }
        self.seal()

    def seal(self):
        self.proof.pop("proof_sha256", None)
        self.proof["proof_sha256"] = release.canonical_hash(self.proof)
        (self.root / release.RELEASE_FILE).write_text(json.dumps(self.proof), encoding="utf-8")

    def test_verified_inventory_is_accepted(self):
        self.assertEqual(1, release.validate_release(self.root)["articles"])

    def test_absent_proof_cannot_deploy(self):
        (self.root / release.RELEASE_FILE).unlink()
        with self.assertRaises(release.ReleaseFailure):
            release.validate_release(self.root)

    def test_built_html_drift_cannot_reuse_old_approval(self):
        (self.root / "posts/verified.html").write_text("Unreviewed edit", encoding="utf-8")
        with self.assertRaisesRegex(release.ReleaseFailure, "changed after verification"):
            release.validate_release(self.root)

    def test_partial_cycle_cannot_deploy_even_with_valid_proof_digest(self):
        self.proof["articles"][0]["stages"].pop()
        self.seal()
        with self.assertRaisesRegex(release.ReleaseFailure, "incomplete stage"):
            release.validate_release(self.root)

    def test_approval_must_include_live_link_check(self):
        self.proof["articles"][0]["quality"]["link_health_checked"] = False
        self.seal()
        with self.assertRaisesRegex(release.ReleaseFailure, "quality approval"):
            release.validate_release(self.root)

    def test_unknown_or_noninteger_schema_is_rejected(self):
        original = copy.deepcopy(self.proof)
        for version in (True, False, 0, 3, "2", None):
            with self.subTest(version=version):
                self.proof = copy.deepcopy(original)
                self.proof["schema_version"] = version
                self.seal()
                with self.assertRaisesRegex(release.ReleaseFailure, "unsupported publication proof schema"):
                    release.validate_release(self.root)

    def test_stage_order_types_and_digests_remain_required(self):
        original = copy.deepcopy(self.proof)
        stages = original["articles"][0]["stages"]
        variants = [list(reversed(stages)), [None, *stages[1:]]]
        for field in ("prompt_sha256", "raw_sha256", "section_sha256"):
            invalid = copy.deepcopy(stages)
            invalid[0][field] = "not-a-digest"
            variants.append(invalid)
        for stages in variants:
            with self.subTest(stages=stages):
                self.proof = copy.deepcopy(original)
                self.proof["articles"][0]["stages"] = stages
                self.seal()
                with self.assertRaises(release.ReleaseFailure):
                    release.validate_release(self.root)

    def test_quality_report_post_and_review_digest_bindings_are_required(self):
        original = copy.deepcopy(self.proof)
        for field, value in (("status", "needs-research"), ("report_sha256", "0" * 64),
                             ("post_sha256", "0" * 64), ("review_sha256", "bad"),
                             ("blocking_issue_count", 1), ("link_health_checked", 1)):
            with self.subTest(field=field):
                self.proof = copy.deepcopy(original)
                self.proof["articles"][0]["quality"][field] = value
                self.seal()
                with self.assertRaises(release.ReleaseFailure):
                    release.validate_release(self.root)

    def test_unsealed_proof_mutation_is_rejected(self):
        self.proof["articles"][0]["stages"][-1]["raw_sha256"] = "0" * 64
        (self.root / release.RELEASE_FILE).write_text(json.dumps(self.proof), encoding="utf-8")
        with self.assertRaisesRegex(release.ReleaseFailure, "proof hash drift"):
            release.validate_release(self.root)


class LegacyProfileBoundaryTests(SiteReleaseTests):
    def test_v1_cannot_declare_profile_contracts_or_article_profiles(self):
        original = copy.deepcopy(self.proof)
        for mutate in (
            lambda p: p.update(profile_contracts={release.INTEGRATED_PROFILE: "a" * 64}),
            lambda p: p.update(profile=release.INTEGRATED_PROFILE),
            lambda p: p["articles"][0].update(profile=release.INTEGRATED_PROFILE),
            lambda p: p["articles"][0]["quality"].update(review_scope=release.INTEGRATED_REVIEW_SCOPE),
        ):
            with self.subTest(mutation=mutate):
                self.proof = copy.deepcopy(original)
                mutate(self.proof)
                self.seal()
                with self.assertRaises(release.ReleaseFailure):
                    release.validate_release(self.root)


class ProfileSiteReleaseTests(SiteReleaseTests):
    def setUp(self):
        super().setUp()
        integrate_proof(self.proof)
        self.seal()

    def test_missing_unknown_and_malformed_article_profiles_are_rejected(self):
        original = copy.deepcopy(self.proof)
        for profile in (None, "unknown-v1", "integrated", [], 1):
            with self.subTest(profile=profile):
                self.proof = copy.deepcopy(original)
                record = self.proof["articles"][0]
                if profile is None:
                    record.pop("profile")
                else:
                    record["profile"] = profile
                self.seal()
                with self.assertRaisesRegex(release.ReleaseFailure, "publication profile"):
                    release.validate_release(self.root)

    def test_contract_inventory_must_equal_known_used_profiles(self):
        original = copy.deepcopy(self.proof)
        for contracts in ({}, {"unknown-v1": "a" * 64},
                          {release.INTEGRATED_PROFILE: "bad"},
                          {release.EXHAUSTIVE_PROFILE: "a" * 64},
                          {release.INTEGRATED_PROFILE: "a" * 64, release.EXHAUSTIVE_PROFILE: "b" * 64}):
            with self.subTest(contracts=contracts):
                self.proof = copy.deepcopy(original)
                self.proof["profile_contracts"] = contracts
                self.seal()
                with self.assertRaises(release.ReleaseFailure):
                    release.validate_release(self.root)

    def test_v2_cannot_blend_legacy_schema_fields(self):
        original = copy.deepcopy(self.proof)
        for field, value in (("required_stages", list(release.PUBLIC_STAGES)),
                             ("contract_sha256", "a" * 64), ("profile", release.INTEGRATED_PROFILE)):
            with self.subTest(field=field):
                self.proof = copy.deepcopy(original)
                self.proof[field] = value
                self.seal()
                with self.assertRaises(release.ReleaseFailure):
                    release.validate_release(self.root)

    def test_integrated_proof_cannot_use_exhaustive_stages_or_mixed_tags(self):
        original = copy.deepcopy(self.proof)
        for mutate in (
            lambda r: r.update(stages=[stage_proof(stage) for stage in release.PUBLIC_STAGES]),
            lambda r: r["stages"][0].update(profile=release.EXHAUSTIVE_PROFILE),
        ):
            self.proof = copy.deepcopy(original)
            mutate(self.proof["articles"][0])
            self.seal()
            with self.assertRaises(release.ReleaseFailure):
                release.validate_release(self.root)

    def test_source_review_scope_and_provenance_cannot_be_omitted(self):
        original = copy.deepcopy(self.proof)
        for field in ("review_scope", "review_provenance"):
            with self.subTest(field=field):
                self.proof = copy.deepcopy(original)
                self.proof["articles"][0]["quality"].pop(field)
                self.seal()
                with self.assertRaisesRegex(release.ReleaseFailure, "captured profile reviewer"):
                    release.validate_release(self.root)

    def test_reviewer_scope_stage_report_and_hashes_are_bound(self):
        original = copy.deepcopy(self.proof)
        for field, value in (("scope", "blanket-approval"), ("profile", release.EXHAUSTIVE_PROFILE),
                             ("stage", "integrated-research"), ("report_sha256", "0" * 64),
                             ("prompt_sha256", "0" * 64), ("raw_sha256", "0" * 64),
                             ("section_sha256", "0" * 64)):
            with self.subTest(field=field):
                self.proof = copy.deepcopy(original)
                self.proof["articles"][0]["quality"]["review_provenance"][field] = value
                self.seal()
                with self.assertRaisesRegex(release.ReleaseFailure, "captured profile reviewer"):
                    release.validate_release(self.root)

    def test_research_stage_cannot_substitute_for_independent_review(self):
        record = self.proof["articles"][0]
        # Public proof binds the review stage; worker identity is source-runtime owned.
        record["quality"]["review_provenance"].update(**record["stages"][0])
        self.seal()
        with self.assertRaisesRegex(release.ReleaseFailure, "captured profile reviewer"):
            release.validate_release(self.root)

    def test_full_review_cannot_carry_an_ambiguous_repair_chain(self):
        self.proof["articles"][0]["quality"]["approval_chain"] = []
        self.seal()
        with self.assertRaisesRegex(release.ReleaseFailure, "ambiguous integrated review scope"):
            release.validate_release(self.root)

    def test_separately_complete_integrated_and_exhaustive_articles_can_coexist(self):
        post = self.root / "posts/exhaustive.html"
        post.write_text("<article>Exhaustive fixture</article>\n", encoding="utf-8")
        inventory = json.loads((self.root / "publication-data.json").read_text(encoding="utf-8"))
        inventory["articles"].append({"slug": "exhaustive", "url": "posts/exhaustive.html"})
        (self.root / "publication-data.json").write_text(json.dumps(inventory), encoding="utf-8")
        record = copy.deepcopy(self.proof["articles"][0])
        record.update(slug="exhaustive", profile=release.EXHAUSTIVE_PROFILE,
                      post_sha256=release.sha256_file(post),
                      stages=[stage_proof(stage) for stage in release.PUBLIC_STAGES])
        record["quality"]["post_sha256"] = record["post_sha256"]
        record["quality"].pop("review_scope")
        record["quality"].pop("review_provenance")
        self.proof["articles"].append(record)
        self.proof["profile_contracts"][release.EXHAUSTIVE_PROFILE] = "a" * 64
        self.proof["files"] = release.artifact_files(self.root)
        self.seal()
        self.assertEqual(2, release.validate_release(self.root)["articles"])


class ScopedSiteReleaseTests(SiteReleaseTests):
    def setUp(self):
        super().setUp()
        integrate_proof(self.proof)
        self.seal()

    def test_format_only_and_affected_claim_repairs_validate(self):
        original = copy.deepcopy(self.proof)
        for scope in (release.FORMAT_REPAIR_SCOPE, release.CLAIM_REPAIR_SCOPE):
            with self.subTest(scope=scope):
                self.proof = copy.deepcopy(original)
                append_repair(self.proof["articles"][0], scope)
                self.seal()
                self.assertEqual(1, release.validate_release(self.root)["articles"])

    def test_composed_repair_chain_preserves_original_review(self):
        record = self.proof["articles"][0]
        stages, report_hash = copy.deepcopy(record["stages"]), record["report_sha256"]
        for scope in (release.FORMAT_REPAIR_SCOPE, release.CLAIM_REPAIR_SCOPE, release.FORMAT_REPAIR_SCOPE):
            append_repair(record, scope)
        self.seal()
        self.assertEqual(stages, record["stages"])
        self.assertEqual(report_hash, record["quality"]["approval_chain"][0]["report_sha256"])
        self.assertEqual(1, release.validate_release(self.root)["articles"])

    def test_invalid_scoped_chains_claim_coverage_and_reviewer_rebinding_are_rejected(self):
        record = self.proof["articles"][0]
        append_repair(record, release.FORMAT_REPAIR_SCOPE)
        append_repair(record, release.CLAIM_REPAIR_SCOPE)
        original = copy.deepcopy(self.proof)
        self.seal()
        self.assertEqual(1, release.validate_release(self.root)["articles"])
        for name, invalid in invalid_scoped_records(record):
            with self.subTest(mutation=name):
                self.proof = copy.deepcopy(original)
                self.proof["articles"][0] = invalid
                self.seal()
                with self.assertRaises(release.ReleaseFailure):
                    release.validate_release(self.root)


if __name__ == "__main__":
    unittest.main()
