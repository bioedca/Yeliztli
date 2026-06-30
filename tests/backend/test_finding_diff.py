"""Unit tests for the finding-level change diff (SW-A4b / #8).

Covers the pure diff (added / removed / changed / unchanged, stable-key matching,
collision pairing, release-delta labelling, empty-prior → empty diff) and the
snapshot → store → read → dismiss round-trip against a real sample DB.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.finding_diff import (
    compute_and_store_finding_diff,
    compute_finding_diff,
    dismiss_finding_diff,
    has_changes,
    read_finding_diff,
    snapshot_findings,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import database_versions, findings, reference_metadata


def _record(**overrides) -> dict:
    """A finding snapshot record with sensible defaults (overridable)."""
    base = {
        "module": "cancer",
        "category": "monogenic_variant",
        "gene_symbol": "BRCA1",
        "rsid": "rs80357906",
        "drug": None,
        "diplotype": None,
        "pathway": None,
        "finding_text": "BRCA1 variant",
        "clinvar_significance": "Uncertain_significance",
        "evidence_level": 2,
        "metabolizer_status": None,
        "pathway_level": None,
        "release_versions": {},
    }
    base.update(overrides)
    return base


# ── Pure diff ──────────────────────────────────────────────────────────────


class TestComputeFindingDiff:
    def test_changed_meaning_field(self) -> None:
        prior = [_record(clinvar_significance="Uncertain_significance")]
        current = [_record(clinvar_significance="Pathogenic")]
        diff = compute_finding_diff(prior, current, after_releases={})

        assert diff["counts"] == {"changed": 1, "added": 0, "removed": 0}
        (entry,) = diff["changed"]
        assert entry["gene_symbol"] == "BRCA1"
        assert entry["changes"] == [
            {
                "field": "clinvar_significance",
                "before": "Uncertain_significance",
                "after": "Pathogenic",
            }
        ]

    def test_identical_findings_are_unchanged(self) -> None:
        prior = [_record()]
        current = [_record()]
        diff = compute_finding_diff(prior, current, after_releases={})
        assert diff["counts"] == {"changed": 0, "added": 0, "removed": 0}

    def test_added_and_removed_by_identity_key(self) -> None:
        prior = [_record(rsid="rs1", finding_text="gone")]
        current = [_record(rsid="rs2", finding_text="new")]
        diff = compute_finding_diff(prior, current, after_releases={})

        assert diff["counts"] == {"changed": 0, "added": 1, "removed": 1}
        assert diff["added"][0]["rsid"] == "rs2"
        assert diff["removed"][0]["rsid"] == "rs1"

    def test_evidence_level_change_is_stringified(self) -> None:
        prior = [_record(evidence_level=2)]
        current = [_record(evidence_level=3)]
        diff = compute_finding_diff(prior, current, after_releases={})
        (entry,) = diff["changed"]
        assert entry["changes"] == [{"field": "evidence_level", "before": "2", "after": "3"}]

    def test_multiple_meaning_fields_change(self) -> None:
        prior = [
            _record(
                module="pgx",
                category="pgx",
                gene_symbol="CYP2C19",
                rsid=None,
                drug="clopidogrel",
                diplotype="*1/*2",
                clinvar_significance=None,
                evidence_level=3,
                metabolizer_status="Intermediate",
                pathway_level=None,
            )
        ]
        current = [
            _record(
                module="pgx",
                category="pgx",
                gene_symbol="CYP2C19",
                rsid=None,
                drug="clopidogrel",
                diplotype="*1/*2",
                clinvar_significance=None,
                evidence_level=4,
                metabolizer_status="Poor",
                pathway_level=None,
            )
        ]
        diff = compute_finding_diff(prior, current, after_releases={})
        (entry,) = diff["changed"]
        fields = {c["field"] for c in entry["changes"]}
        assert fields == {"evidence_level", "metabolizer_status"}

    def test_collision_same_key_pairs_by_finding_text(self) -> None:
        # Two findings collapse to the same identity key (e.g. ancestry summaries
        # with NULL identity columns). Stable finding_text pairing must isolate
        # the one whose meaning shifted, not flood added/removed.
        key = {
            "module": "ancestry",
            "category": "biogeographic",
            "gene_symbol": None,
            "rsid": None,
            "drug": None,
            "diplotype": None,
            "clinvar_significance": None,
        }
        prior = [
            _record(finding_text="A", evidence_level=1, **key),
            _record(finding_text="B", evidence_level=2, **key),
        ]
        current = [
            _record(finding_text="A", evidence_level=1, **key),
            _record(finding_text="B", evidence_level=3, **key),
        ]
        diff = compute_finding_diff(prior, current, after_releases={})
        assert diff["counts"] == {"changed": 1, "added": 0, "removed": 0}
        assert diff["changed"][0]["finding_text"] == "B"

    def test_collision_shrink_does_not_fabricate_a_change(self) -> None:
        # A collision group loses one member (alpha removed; beta unchanged).
        # Positional pairing would mis-pair alpha→beta and report a false
        # "VUS → Pathogenic" change plus double-count beta as removed. Meaning-
        # aware matching must report exactly: alpha removed, nothing changed.
        key = {
            "module": "ancestry",
            "category": "biogeographic",
            "gene_symbol": None,
            "rsid": None,
            "drug": None,
            "diplotype": None,
        }
        prior = [
            _record(finding_text="alpha", clinvar_significance="Uncertain_significance", **key),
            _record(finding_text="beta", clinvar_significance="Pathogenic", **key),
        ]
        current = [_record(finding_text="beta", clinvar_significance="Pathogenic", **key)]
        diff = compute_finding_diff(prior, current, after_releases={})
        assert diff["counts"] == {"changed": 0, "added": 0, "removed": 1}
        assert diff["removed"][0]["finding_text"] == "alpha"

    def test_collision_reword_and_reclassify_attributes_correctly(self) -> None:
        # Within a collision group a reword flips finding_text sort order while one
        # member is reclassified and another is stably benign. Positional pairing
        # would emit two bogus changes; meaning-aware matching emits exactly one
        # (VUS → Pathogenic) and treats the stable-benign row as unchanged.
        key = {
            "module": "ancestry",
            "category": "biogeographic",
            "gene_symbol": None,
            "rsid": None,
            "drug": None,
            "diplotype": None,
        }
        prior = [
            _record(finding_text="m", clinvar_significance="Uncertain_significance", **key),
            _record(finding_text="a", clinvar_significance="Benign", **key),
        ]
        current = [
            _record(finding_text="a2", clinvar_significance="Pathogenic", **key),
            _record(finding_text="z", clinvar_significance="Benign", **key),
        ]
        diff = compute_finding_diff(prior, current, after_releases={})
        assert diff["counts"] == {"changed": 1, "added": 0, "removed": 0}
        (entry,) = diff["changed"]
        (c,) = [c for c in entry["changes"] if c["field"] == "clinvar_significance"]
        assert (c["before"], c["after"]) == ("Uncertain_significance", "Pathogenic")

    def test_reword_with_same_meaning_is_unchanged(self) -> None:
        # finding_text is not a meaning field, so a pure reword is not a change.
        prior = [_record(finding_text="BRCA1 likely pathogenic variant")]
        current = [_record(finding_text="BRCA1 variant (pathogenic)")]
        diff = compute_finding_diff(prior, current, after_releases={})
        assert diff["counts"] == {"changed": 0, "added": 0, "removed": 0}

    def test_no_prior_snapshot_yields_empty_diff(self) -> None:
        current = [_record(), _record(rsid="rs2")]
        for empty in (None, []):
            diff = compute_finding_diff(empty, current, after_releases={"clinvar": "x"})
            assert diff["counts"] == {"changed": 0, "added": 0, "removed": 0}
            assert diff["added"] == []
            assert diff["release_deltas"] == []
            assert diff["before_releases"] == {}
            # after_releases is still surfaced for context.
            assert diff["after_releases"] == {"clinvar": "x"}

    def test_release_deltas_from_provenance(self) -> None:
        prior = [_record(release_versions={"clinvar": "2024-01", "gnomad": "r2.1.1"})]
        current = [_record(clinvar_significance="Pathogenic")]
        after = {"clinvar": "2024-06", "gnomad": "r2.1.1"}
        diff = compute_finding_diff(prior, current, after)

        # gnomad is unchanged → not a delta; clinvar advanced → a delta.
        assert diff["before_releases"] == {"clinvar": "2024-01", "gnomad": "r2.1.1"}
        assert diff["release_deltas"] == [
            {"db_name": "clinvar", "before": "2024-01", "after": "2024-06"}
        ]

    def test_before_releases_union_avoids_spurious_delta(self) -> None:
        # Prior findings carry heterogeneous release sets (a partial first record).
        # Union — not first-wins — must recover gnomad so it is not reported as a
        # spurious None → r2.1.1 delta.
        prior = [
            _record(rsid="rs1", release_versions={"clinvar": "2024-01"}),
            _record(rsid="rs2", release_versions={"clinvar": "2024-01", "gnomad": "r2.1.1"}),
        ]
        current = [_record(rsid="rs1"), _record(rsid="rs2")]
        after = {"clinvar": "2024-06", "gnomad": "r2.1.1"}
        diff = compute_finding_diff(prior, current, after)

        assert diff["before_releases"] == {"clinvar": "2024-01", "gnomad": "r2.1.1"}
        assert diff["release_deltas"] == [
            {"db_name": "clinvar", "before": "2024-01", "after": "2024-06"}
        ]


def _pw(pathway: str, level: str, module: str = "methylation") -> dict:
    """A categorical ``pathway_summary`` finding: NULL gene/rsid/drug/diplotype,
    distinguished only by ``pathway``; its meaning field is ``pathway_level``."""
    return _record(
        module=module,
        category="pathway_summary",
        gene_symbol=None,
        rsid=None,
        drug=None,
        diplotype=None,
        pathway=pathway,
        pathway_level=level,
        clinvar_significance=None,
        metabolizer_status=None,
        finding_text=f"{pathway} — {level} consideration",
    )


class TestPathwaySummaryDiff:
    """Pathway summaries share ``category='pathway_summary'`` with NULL
    gene/rsid/drug/diplotype, so only ``pathway`` distinguishes them. The identity
    key must include ``pathway`` (#575) — otherwise simultaneous per-pathway
    changes in one module mis-pair (wrong "before") or cancel out and are missed.
    """

    def test_offsetting_swap_detects_both_changes(self) -> None:
        # Folate Standard→Elevated AND Methionine Elevated→Standard. The multiset of
        # pathway_level values is unchanged, so without ``pathway`` in the identity
        # key both real changes were silently lost (#575 Case 1).
        prior = [_pw("Folate & MTHFR", "Standard"), _pw("Methionine Cycle", "Elevated")]
        current = [_pw("Folate & MTHFR", "Elevated"), _pw("Methionine Cycle", "Standard")]
        diff = compute_finding_diff(prior, current, after_releases={})
        assert diff["counts"] == {"changed": 2, "added": 0, "removed": 0}
        by_pathway = {e["pathway"]: e for e in diff["changed"]}
        assert by_pathway["Folate & MTHFR"]["changes"] == [
            {"field": "pathway_level", "before": "Standard", "after": "Elevated"}
        ]
        assert by_pathway["Methionine Cycle"]["changes"] == [
            {"field": "pathway_level", "before": "Elevated", "after": "Standard"}
        ]

    def test_simultaneous_changes_attribute_correct_before_value(self) -> None:
        # Folate Moderate→Standard, Methionine Standard→Elevated. Without ``pathway``
        # these mis-paired, reporting Methionine's "before" as Moderate (Folate's old
        # value) and dropping Folate's change entirely (#575 Case 2).
        prior = [_pw("Folate & MTHFR", "Moderate"), _pw("Methionine Cycle", "Standard")]
        current = [_pw("Folate & MTHFR", "Standard"), _pw("Methionine Cycle", "Elevated")]
        diff = compute_finding_diff(prior, current, after_releases={})
        assert diff["counts"] == {"changed": 2, "added": 0, "removed": 0}
        by_pathway = {e["pathway"]: e for e in diff["changed"]}
        assert by_pathway["Methionine Cycle"]["changes"] == [
            {"field": "pathway_level", "before": "Standard", "after": "Elevated"}
        ]
        assert by_pathway["Folate & MTHFR"]["changes"] == [
            {"field": "pathway_level", "before": "Moderate", "after": "Standard"}
        ]

    def test_one_pathway_changes_others_stable(self) -> None:
        prior = [_pw("Folate & MTHFR", "Moderate"), _pw("Transsulfuration", "Standard")]
        current = [_pw("Folate & MTHFR", "Elevated"), _pw("Transsulfuration", "Standard")]
        diff = compute_finding_diff(prior, current, after_releases={})
        assert diff["counts"] == {"changed": 1, "added": 0, "removed": 0}
        assert diff["changed"][0]["pathway"] == "Folate & MTHFR"

    def test_distinct_pathways_are_separate_identities(self) -> None:
        # A pathway only in current is "added", only in prior is "removed" — not
        # silently matched to a different pathway with the same level (which the
        # pre-#575 collapsed key did, reporting no change at all).
        prior = [_pw("Folate & MTHFR", "Moderate")]
        current = [_pw("Choline & Betaine", "Moderate")]
        diff = compute_finding_diff(prior, current, after_releases={})
        assert diff["counts"] == {"changed": 0, "added": 1, "removed": 1}
        assert diff["added"][0]["pathway"] == "Choline & Betaine"
        assert diff["removed"][0]["pathway"] == "Folate & MTHFR"

    def test_same_pathway_same_module_is_unchanged(self) -> None:
        # Control: an unchanged pathway summary stays unchanged.
        prior = [_pw("Folate & MTHFR", "Moderate", module="methylation")]
        current = [_pw("Folate & MTHFR", "Moderate", module="methylation")]
        diff = compute_finding_diff(prior, current, after_releases={})
        assert diff["counts"] == {"changed": 0, "added": 0, "removed": 0}

    def test_same_pathway_different_modules_do_not_match(self) -> None:
        # ``module`` is part of the identity key, so the same pathway name + level
        # in different modules must not cross-match — it is a removal + an addition.
        prior = [_pw("Folate & MTHFR", "Moderate", module="methylation")]
        current = [_pw("Folate & MTHFR", "Moderate", module="nutrigenomics")]
        diff = compute_finding_diff(prior, current, after_releases={})
        assert diff["counts"] == {"changed": 0, "added": 1, "removed": 1}
        assert diff["added"][0]["module"] == "nutrigenomics"
        assert diff["removed"][0]["module"] == "methylation"


def _prs(trait: str, pct: str, module: str = "cancer") -> dict:
    """A PRS finding: ``category='prs'`` with NULL gene/rsid/drug/diplotype/pathway,
    distinguished only by ``trait`` (which snapshot_findings reads from detail_json).
    Its meaning field here is ``evidence_level``."""
    return _record(
        module=module,
        category="prs",
        gene_symbol=None,
        rsid=None,
        drug=None,
        diplotype=None,
        pathway=None,
        trait=trait,
        clinvar_significance=None,
        evidence_level=1,
        metabolizer_status=None,
        pathway_level=None,
        finding_text=f"{trait}: {pct} percentile",
    )


class TestPRSTraitDiff:
    """PRS findings share ``category='prs'`` with NULL gene/rsid/drug/diplotype/
    pathway, so only ``trait`` distinguishes them. The identity key must include
    ``trait`` (#1283 — the PRS analog of the #575 ``pathway`` collision); otherwise
    added/removed per-trait PRS findings cancel out or are attributed to the wrong
    trait.
    """

    def test_added_and_removed_trait_do_not_cancel(self) -> None:
        # Breast removed, Melanoma added, Prostate unchanged. Without ``trait`` in
        # the identity key the add cancelled the remove → "nothing changed" (#1283).
        prior = [_prs("breast_cancer", "95th"), _prs("prostate_cancer", "50th")]
        current = [_prs("prostate_cancer", "50th"), _prs("melanoma", "88th")]
        diff = compute_finding_diff(prior, current, after_releases={})
        assert diff["counts"] == {"changed": 0, "added": 1, "removed": 1}
        assert diff["added"][0]["trait"] == "melanoma"
        assert diff["removed"][0]["trait"] == "breast_cancer"

    def test_lone_removal_names_the_removed_trait(self) -> None:
        # Breast removed, Prostate unchanged. Without ``trait`` the diff named
        # Prostate (the still-present finding) as removed (#1283).
        prior = [_prs("breast_cancer", "95th"), _prs("prostate_cancer", "50th")]
        current = [_prs("prostate_cancer", "50th")]
        diff = compute_finding_diff(prior, current, after_releases={})
        assert diff["counts"] == {"changed": 0, "added": 0, "removed": 1}
        assert diff["removed"][0]["trait"] == "breast_cancer"

    def test_same_trait_meaning_shift_is_a_change_not_add_remove(self) -> None:
        # A per-trait PRS whose meaning field shifts is one *changed* finding (stable
        # identity), not a remove+add.
        prior = [_prs("breast_cancer", "95th")]
        current = [_prs("breast_cancer", "95th")]
        current[0]["evidence_level"] = 2
        diff = compute_finding_diff(prior, current, after_releases={})
        assert diff["counts"] == {"changed": 1, "added": 0, "removed": 0}
        assert diff["changed"][0]["trait"] == "breast_cancer"

    def test_unchanged_trait_set_is_unchanged(self) -> None:
        prior = [_prs("breast_cancer", "95th"), _prs("prostate_cancer", "50th")]
        current = [_prs("prostate_cancer", "50th"), _prs("breast_cancer", "95th")]
        diff = compute_finding_diff(prior, current, after_releases={})
        assert diff["counts"] == {"changed": 0, "added": 0, "removed": 0}

    def test_same_trait_different_modules_do_not_match(self) -> None:
        # ``module`` is part of identity, so a trait name reused across modules
        # (cancer vs traits) is a removal + an addition, never a cross-match.
        prior = [_prs("cognitive_ability", "70th", module="traits")]
        current = [_prs("cognitive_ability", "70th", module="cancer")]
        diff = compute_finding_diff(prior, current, after_releases={})
        assert diff["counts"] == {"changed": 0, "added": 1, "removed": 1}
        assert diff["added"][0]["module"] == "cancer"
        assert diff["removed"][0]["module"] == "traits"


class TestHasChanges:
    def test_empty_diff_has_no_changes(self) -> None:
        assert has_changes(compute_finding_diff(None, [], after_releases={})) is False

    def test_diff_with_added_has_changes(self) -> None:
        diff = compute_finding_diff([_record(rsid="rs1")], [_record(rsid="rs2")], {})
        assert has_changes(diff) is True


# ── Storage round-trip against a real sample DB ─────────────────────────────


@pytest.fixture
def reference_engine(tmp_path: Path) -> sa.Engine:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            database_versions.insert(),
            [{"db_name": "clinvar", "version": "2024-06", "genome_build": "GRCh37"}],
        )
    return engine


@pytest.fixture
def sample_engine(tmp_path: Path) -> sa.Engine:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'sample_1.db'}")
    create_sample_tables(engine)
    return engine


def _insert_findings(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        for row in rows:
            conn.execute(findings.insert().values(**row))


class TestSnapshotFindings:
    def test_extracts_release_versions_from_provenance(self, sample_engine: sa.Engine) -> None:
        provenance = json.dumps(
            {"sources": {"clinvar": {"version": "2024-06", "genome_build": "GRCh37"}}}
        )
        _insert_findings(
            sample_engine,
            [
                {
                    "module": "cancer",
                    "finding_text": "BRCA1 Pathogenic",
                    "rsid": "rs80357906",
                    "clinvar_significance": "Pathogenic",
                    "provenance": provenance,
                },
                {"module": "ancestry", "finding_text": "82% European"},
            ],
        )
        records = snapshot_findings(sample_engine)
        by_rsid = {r["rsid"]: r for r in records}
        assert by_rsid["rs80357906"]["release_versions"] == {"clinvar": "2024-06"}
        assert by_rsid["rs80357906"]["clinvar_significance"] == "Pathogenic"
        # No provenance → empty release_versions, not an error.
        assert by_rsid[None]["release_versions"] == {}

    def test_prs_trait_read_from_detail_json(self, sample_engine: sa.Engine) -> None:
        # PRS findings carry their distinguishing trait only in detail_json, so
        # snapshot_findings must surface it into the identity record — otherwise
        # per-trait PRS findings collide on one key (#1283). Non-PRS rows never take
        # a trait from detail_json (their identity is pinned by the table columns).
        _insert_findings(
            sample_engine,
            [
                {
                    "module": "cancer",
                    "category": "prs",
                    "evidence_level": 1,
                    "finding_text": "Breast cancer: 95th percentile",
                    "detail_json": json.dumps({"trait": "breast_cancer", "name": "Breast cancer"}),
                },
                {
                    "module": "cancer",
                    "category": "monogenic_variant",
                    "gene_symbol": "BRCA1",
                    "finding_text": "BRCA1 Pathogenic",
                    # A non-PRS detail_json that happens to carry a trait must be ignored.
                    "detail_json": json.dumps({"trait": "should_be_ignored"}),
                },
                {
                    "module": "cancer",
                    "category": "prs",
                    "evidence_level": 1,
                    "finding_text": "Malformed trait PRS",
                    # A non-string trait is treated as absent, not coerced into the key.
                    "detail_json": json.dumps({"trait": 42}),
                },
            ],
        )
        records = snapshot_findings(sample_engine)
        by_text = {r["finding_text"]: r for r in records}
        assert by_text["Breast cancer: 95th percentile"]["trait"] == "breast_cancer"
        assert by_text["BRCA1 Pathogenic"]["trait"] is None
        assert by_text["Malformed trait PRS"]["trait"] is None


class TestComputeAndStoreRoundTrip:
    def test_store_read_dismiss(
        self, sample_engine: sa.Engine, reference_engine: sa.Engine
    ) -> None:
        # Current findings live in the sample DB.
        _insert_findings(
            sample_engine,
            [
                {
                    "module": "cancer",
                    "category": "monogenic_variant",
                    "gene_symbol": "BRCA1",
                    "rsid": "rs80357906",
                    "finding_text": "BRCA1 Pathogenic",
                    "clinvar_significance": "Pathogenic",
                    "evidence_level": 4,
                }
            ],
        )
        # Prior run carried the same finding as a VUS under an older ClinVar.
        prior = [
            _record(
                gene_symbol="BRCA1",
                rsid="rs80357906",
                clinvar_significance="Uncertain_significance",
                evidence_level=2,
                release_versions={"clinvar": "2024-01"},
            )
        ]
        stored = compute_and_store_finding_diff(sample_engine, reference_engine, prior)
        assert stored["counts"]["changed"] == 1
        assert stored["dismissed"] is False
        assert stored["generated_at"]

        loaded = read_finding_diff(sample_engine)
        assert loaded is not None
        assert loaded["counts"]["changed"] == 1
        assert loaded["release_deltas"] == [
            {"db_name": "clinvar", "before": "2024-01", "after": "2024-06"}
        ]
        assert has_changes(loaded) is True

        # Dismiss hides it without deleting the record.
        assert dismiss_finding_diff(sample_engine) is True
        after = read_finding_diff(sample_engine)
        assert after is not None
        assert after["dismissed"] is True

    def test_empty_prior_stores_empty_diff(
        self, sample_engine: sa.Engine, reference_engine: sa.Engine
    ) -> None:
        _insert_findings(
            sample_engine,
            [{"module": "cancer", "finding_text": "BRCA1 Pathogenic", "rsid": "rs1"}],
        )
        stored = compute_and_store_finding_diff(sample_engine, reference_engine, None)
        assert stored["counts"] == {"changed": 0, "added": 0, "removed": 0}
        assert has_changes(stored) is False

    def test_dismiss_without_stored_diff_returns_false(self, sample_engine: sa.Engine) -> None:
        assert dismiss_finding_diff(sample_engine) is False
        assert read_finding_diff(sample_engine) is None
