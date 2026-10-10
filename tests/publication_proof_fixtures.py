"""Synthetic artifact proofs only; never research receipts or live approvals."""
from __future__ import annotations

import copy
import hashlib

from scripts import site_release_validator as artifact


def stage_proof(stage: str) -> dict:
    return {"stage": stage, **{
        field: hashlib.sha256(f"synthetic:{stage}:{field}".encode()).hexdigest()
        for field in ("prompt_sha256", "raw_sha256", "section_sha256")
    }}


def integrate_proof(proof: dict) -> None:
    proof["schema_version"] = artifact.PROFILE_SCHEMA_VERSION
    proof.pop("required_stages")
    contract = proof.pop("contract_sha256")
    proof["profile_contracts"] = {artifact.INTEGRATED_PROFILE: contract}
    for record in proof["articles"]:
        record["profile"] = artifact.INTEGRATED_PROFILE
        record["stages"] = [stage_proof(stage) for stage in artifact.INTEGRATED_STAGES]
        record["quality"].update(
            review_scope=artifact.INTEGRATED_REVIEW_SCOPE,
            review_provenance={"scope": artifact.INTEGRATED_REVIEW_SCOPE,
                               "profile": artifact.INTEGRATED_PROFILE,
                               "report_sha256": record["report_sha256"],
                               **record["stages"][-1]},
        )


def append_repair(record: dict, scope: str) -> None:
    if scope not in artifact.REPAIR_SCOPES:
        raise ValueError("fixture requires a known repair scope")
    quality = record["quality"]
    chain = quality.setdefault("approval_chain", [{
        "scope": artifact.INTEGRATED_REVIEW_SCOPE,
        "report_sha256": record["report_sha256"],
        "stages": copy.deepcopy(record["stages"]),
    }])
    before = record["report_sha256"]
    after = hashlib.sha256(f"synthetic repair:{before}:{scope}:{len(chain)}".encode()).hexdigest()
    stages = [stage_proof("artifact-format-repair")]
    changes, verified = [], []
    if scope == artifact.CLAIM_REPAIR_SCOPE:
        stages.append(stage_proof("artifact-claim-review"))
        changes = [{"id": "C-001", "before": "Capacity is 10 MW.", "after": "Capacity is 12 MW."}]
        verified = [{"change_id": row["id"], "before": row["before"], "after": row["after"],
                     "source_url": "https://example.invalid/synthetic-capacity",
                     "finding": "Synthetic fixture covers this exact changed claim."} for row in changes]
    chain.append({
        "scope": scope, "before_sha256": before, "after_sha256": after,
        "changed_claims": changes, "verified_claims": verified,
        "grounded_assertions": [{"assertion": "Existing checked summary.",
                                  "support_text": "Existing checked summary.",
                                  "source_url": "https://example.invalid/synthetic-summary"}],
        "stages": stages,
    })
    record["report_sha256"] = after
    quality.update(
        report_sha256=after, review_scope=scope,
        review_provenance={"scope": scope, "profile": artifact.INTEGRATED_PROFILE,
                           "report_sha256": after,
                           "original_report_sha256": chain[0]["report_sha256"],
                           "base_state_sha256": "1" * 64,
                           "repair_chain_sha256": artifact.canonical_hash(chain),
                           **stages[-1]},
    )


def invalid_scoped_records(record: dict):
    """Mutate a valid format-then-claim chain without changing artifact bytes."""
    mutations = {
        "missing chain": lambda q: q.pop("approval_chain"),
        "empty chain": lambda q: q.update(approval_chain=[]),
        "original only": lambda q: q.update(approval_chain=q["approval_chain"][:1]),
        "malformed chain row": lambda q: q["approval_chain"].__setitem__(1, None),
        "original scope": lambda q: q["approval_chain"][0].update(scope=artifact.FORMAT_REPAIR_SCOPE),
        "original stages": lambda q: q["approval_chain"][0]["stages"].reverse(),
        "original report binding": lambda q: q["review_provenance"].update(original_report_sha256="0" * 64),
        "base state digest": lambda q: q["review_provenance"].update(base_state_sha256="bad"),
        "repair chain digest": lambda q: q["review_provenance"].update(repair_chain_sha256="bad"),
        "first link": lambda q: q["approval_chain"][1].update(before_sha256="0" * 64),
        "intermediate link": lambda q: q["approval_chain"][1].update(after_sha256="0" * 64),
        "final link": lambda q: q["approval_chain"][-1].update(before_sha256="0" * 64),
        "final report": lambda q: q["approval_chain"][-1].update(after_sha256="0" * 64),
        "unknown scope": lambda q: q["approval_chain"][-1].update(scope="invented-review"),
        "relabelled claim repair": lambda q: q["approval_chain"][-1].update(scope=artifact.FORMAT_REPAIR_SCOPE),
        "changed claim under format approval": lambda q: q["approval_chain"][1].update(
            changed_claims=copy.deepcopy(q["approval_chain"][-1]["changed_claims"])),
        "verified claim under format approval": lambda q: q["approval_chain"][1].update(
            verified_claims=copy.deepcopy(q["approval_chain"][-1]["verified_claims"])),
        "missing changed claims": lambda q: q["approval_chain"][-1].update(changed_claims=[]),
        "malformed changed claims": lambda q: q["approval_chain"][-1].update(changed_claims=None),
        "duplicate changed claim": lambda q: q["approval_chain"][-1]["changed_claims"].append(
            copy.deepcopy(q["approval_chain"][-1]["changed_claims"][0])),
        "empty changed claim": lambda q: q["approval_chain"][-1]["changed_claims"][0].update(before="", after=""),
        "omitted source coverage": lambda q: q["approval_chain"][-1].update(verified_claims=[]),
        "duplicate source coverage": lambda q: q["approval_chain"][-1]["verified_claims"].append(
            copy.deepcopy(q["approval_chain"][-1]["verified_claims"][0])),
        "unknown claim coverage": lambda q: q["approval_chain"][-1]["verified_claims"][0].update(change_id="other"),
        "before text coverage": lambda q: q["approval_chain"][-1]["verified_claims"][0].update(before="Other fact."),
        "after text coverage": lambda q: q["approval_chain"][-1]["verified_claims"][0].update(after="Other fact."),
        "missing source URL": lambda q: q["approval_chain"][-1]["verified_claims"][0].pop("source_url"),
        "unsafe source URL": lambda q: q["approval_chain"][-1]["verified_claims"][0].update(source_url="file:///fixture"),
        "empty source finding": lambda q: q["approval_chain"][-1]["verified_claims"][0].update(finding=" "),
        "missing repair stages": lambda q: q["approval_chain"][-1].update(stages=[]),
        "unknown repair stage": lambda q: q["approval_chain"][-1]["stages"][0].update(stage="invented-stage"),
        "reordered repair stages": lambda q: q["approval_chain"][-1]["stages"].reverse(),
        "missing fresh claim review": lambda q: q["approval_chain"][-1]["stages"].pop(),
        "bad repair stage digest": lambda q: q["approval_chain"][-1]["stages"][-1].update(raw_sha256="bad"),
        "unbound final scope": lambda q: q.update(review_scope=artifact.FORMAT_REPAIR_SCOPE),
        "original reviewer rebound": lambda q: q["review_provenance"].update(**record["stages"][-1]),
        "ungrounded format summary": lambda q: q["approval_chain"][1]["grounded_assertions"][0].update(
            assertion="A new unreviewed summary fact."),
        "unsafe summary source": lambda q: q["approval_chain"][1]["grounded_assertions"][0].update(source_url="file:///fixture"),
    }
    for name, mutate in mutations.items():
        invalid = copy.deepcopy(record)
        mutate(invalid["quality"])
        yield name, invalid
