"""A corrected gene-health effect summary must reach samples scored before it (#2097).

``effect_summary`` is panel prose copied into the pathway summary's
``snp_details`` and into each SNP finding's text at scoring time, and sample
staleness is keyed on reference bundles, so a panel correction never prompts a
re-analysis. An R702W homozygote analysed before the fix kept being told their
Crohn's risk was "~20-40 fold" — the pooled two-allele estimate — on every
surface that renders the stored rows.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.analysis.gene_health import current_effect_summary, refreshed_gene_health_row
from backend.config import Settings
from backend.db.connection import DBRegistry, reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import findings, reference_metadata, samples
from backend.reports.generator import _load_findings, render_report_html
from backend.reports.variant_card import _load_single_finding

PANEL_PATH = (
    Path(__file__).resolve().parents[2] / "backend" / "data" / "panels" / "gene_health_panel.json"
)

NOD2 = "rs2066844"
# The text every R702W homozygote scored before #2097 has stored.
LEGACY_TT = (
    "Homozygous NOD2 R702W. Severely impaired muramyl dipeptide sensing. Associated "
    "with ~20-40 fold increased risk of Crohn's disease, particularly ileal involvement. "
    "Compound heterozygosity with other NOD2 variants further increases risk."
)
LEGACY_TEXT = f"NOD2 R702W (TT) — {LEGACY_TT}"
PATHWAY = "Autoimmune Conditions"


def _panel_effects(rsid: str) -> dict[str, dict]:
    panel = json.loads(PANEL_PATH.read_text(encoding="utf-8"))
    rows = [snp for pathway in panel["pathways"] for snp in pathway["snps"] if snp["rsid"] == rsid]
    assert len(rows) == 1
    return rows[0]["genotype_effects"]


CURRENT_TT = _panel_effects(NOD2)["TT"]["effect_summary"]
CURRENT_CC = _panel_effects(NOD2)["CC"]["effect_summary"]
# The row's citations before #2097, and the two pooled-estimate sources it added.
LEGACY_PMIDS = ["11385577", "11385576", "11875755"]
POOLED_SOURCES = {"11910337", "15571588"}


def _panel_pmids(rsid: str) -> list[str]:
    panel = json.loads(PANEL_PATH.read_text(encoding="utf-8"))
    rows = [snp for pathway in panel["pathways"] for snp in pathway["snps"] if snp["rsid"] == rsid]
    assert len(rows) == 1
    return rows[0]["pmids"]


CURRENT_PMIDS = _panel_pmids(NOD2)


def test_the_fixture_text_is_what_the_panel_no_longer_says() -> None:
    """Every assertion below is only discriminating while these two differ."""
    assert "20-40 fold" in LEGACY_TT
    assert CURRENT_TT != LEGACY_TT
    assert "fold" not in CURRENT_TT.replace("fold-risk", "")
    assert POOLED_SOURCES <= set(CURRENT_PMIDS)
    assert not POOLED_SOURCES & set(LEGACY_PMIDS)


class TestCurrentEffectSummary:
    def test_legacy_homozygote_summary_is_served_from_the_panel(self) -> None:
        assert current_effect_summary(NOD2, "TT", "Elevated", LEGACY_TT) == CURRENT_TT

    def test_hom_ref_gets_its_own_genotypes_text_not_the_carriers(self) -> None:
        """A non-carrier's entry is refreshed from the CC row, never the TT one."""
        refreshed = current_effect_summary(NOD2, "CC", "Standard", "No NOD2 R702W variant.")
        assert refreshed == CURRENT_CC
        assert "Homozygous" not in (refreshed or "")

    def test_a_moved_category_keeps_the_stored_text(self) -> None:
        """New prose beside an old badge would contradict it; that needs a re-score."""
        assert current_effect_summary(NOD2, "TT", "Moderate", LEGACY_TT) == LEGACY_TT

    def test_not_genotyped_keeps_the_stored_text(self) -> None:
        stored = "Variant not genotyped in this sample."
        assert current_effect_summary(NOD2, None, "Standard", stored) == stored

    def test_an_unmodelled_genotype_keeps_the_stored_text(self) -> None:
        stored = "Genotype CG carries an allele this locus does not model."
        assert current_effect_summary(NOD2, "CG", "Indeterminate", stored) == stored

    def test_a_strand_ambiguous_homozygote_keeps_the_stored_text(self) -> None:
        """FTO AA/TT differ in category, so scoring withholds them in its own words."""
        stored = "AA is a palindromic (A/T or C/G) homozygote: ..."
        assert current_effect_summary("rs9939609", "AA", "Indeterminate", stored) == stored
        # Even a row stored before the strand guard existed is not rewritten.
        assert current_effect_summary("rs9939609", "AA", "Elevated", "old") == "old"

    def test_nothing_stored_stays_nothing(self) -> None:
        assert current_effect_summary(NOD2, "TT", "Elevated", None) is None
        assert current_effect_summary(NOD2, "TT", "Elevated", "") == ""

    def test_an_unknown_rsid_keeps_the_stored_text(self) -> None:
        assert current_effect_summary("rs00000000", "TT", "Elevated", "kept") == "kept"


class TestRefreshedGeneHealthRow:
    def test_snp_finding_text_and_citations_are_refreshed_together(self) -> None:
        detail = {"variant_name": "R702W", "genotype": "TT"}
        text, out, pmids = refreshed_gene_health_row(
            "gene_health",
            "snp_finding",
            NOD2,
            "NOD2",
            "Elevated",
            LEGACY_TEXT,
            detail,
            LEGACY_PMIDS,
        )
        assert text == f"NOD2 R702W (TT) — {CURRENT_TT}"
        assert out == detail
        # The corrected claim keeps the sources it rests on.
        assert pmids == CURRENT_PMIDS

    def test_a_coverage_note_suffix_is_kept(self) -> None:
        detail = {"variant_name": "R702W", "genotype": "TT", "coverage_note": "Check strand."}
        text, _, _ = refreshed_gene_health_row(
            "gene_health",
            "snp_finding",
            NOD2,
            "NOD2",
            "Elevated",
            f"{LEGACY_TEXT} Note: Check strand.",
            detail,
            LEGACY_PMIDS,
        )
        assert text == f"NOD2 R702W (TT) — {CURRENT_TT} Note: Check strand."

    def test_text_of_an_unrecognised_shape_is_left_alone(self) -> None:
        """The label and genotype are never re-derived from prose."""
        detail = {"variant_name": "R702W", "genotype": "TT"}
        doubled = f"NOD2 NOD2 R702W (TT) — {LEGACY_TT}"
        text, _, pmids = refreshed_gene_health_row(
            "gene_health", "snp_finding", NOD2, "NOD2", "Elevated", doubled, detail, LEGACY_PMIDS
        )
        assert text == doubled
        # Its citations stay with the claim that is still on screen.
        assert pmids == LEGACY_PMIDS

    def test_a_row_whose_category_moved_is_left_alone(self) -> None:
        detail = {"variant_name": "R702W", "genotype": "TT"}
        text, _, pmids = refreshed_gene_health_row(
            "gene_health",
            "snp_finding",
            NOD2,
            "NOD2",
            "Moderate",
            LEGACY_TEXT,
            detail,
            LEGACY_PMIDS,
        )
        assert text == LEGACY_TEXT
        assert pmids == LEGACY_PMIDS

    def test_another_modules_row_is_left_alone(self) -> None:
        detail = {"variant_name": "R702W", "genotype": "TT"}
        assert refreshed_gene_health_row(
            "allergy", "snp_finding", NOD2, "NOD2", "Elevated", LEGACY_TEXT, detail, LEGACY_PMIDS
        ) == (LEGACY_TEXT, detail, LEGACY_PMIDS)

    def test_pathway_summary_entries_are_refreshed_one_by_one(self) -> None:
        unknown = {
            "rsid": "rs00000000",
            "genotype": "TT",
            "category": "Elevated",
            "effect_summary": "kept",
        }
        detail = {
            "pathway_id": "autoimmune",
            "called_snps": 3,
            "snp_details": [
                {
                    "rsid": NOD2,
                    "genotype": "TT",
                    "category": "Elevated",
                    "effect_summary": LEGACY_TT,
                },
                {
                    "rsid": NOD2,
                    "genotype": "CC",
                    "category": "Standard",
                    "effect_summary": CURRENT_CC,
                },
                unknown,
            ],
        }
        text, out, pmids = refreshed_gene_health_row(
            "gene_health",
            "pathway_summary",
            None,
            None,
            "Elevated",
            "Autoimmune",
            detail,
            LEGACY_PMIDS,
        )
        assert text == "Autoimmune"
        assert out is not None
        assert out["pathway_id"] == "autoimmune"
        assert out["called_snps"] == 3
        entries = out["snp_details"]
        assert len(entries) == 3
        assert entries[0]["effect_summary"] == CURRENT_TT
        assert entries[1]["effect_summary"] == CURRENT_CC
        assert entries[2] == unknown
        # The stored blob itself is not mutated.
        assert detail["snp_details"][0]["effect_summary"] == LEGACY_TT
        # A SNP the panel no longer has cannot be re-cited, so the union stays.
        assert pmids == LEGACY_PMIDS

    def test_pathway_citations_are_the_union_of_its_snps_current_ones(self) -> None:
        detail = {
            "snp_details": [
                {
                    "rsid": NOD2,
                    "genotype": "TT",
                    "category": "Elevated",
                    "effect_summary": LEGACY_TT,
                },
                {
                    "rsid": "rs9939609",
                    "genotype": "AT",
                    "category": "Moderate",
                    "effect_summary": "One copy of FTO risk allele.",
                },
            ]
        }
        _, _, pmids = refreshed_gene_health_row(
            "gene_health", "pathway_summary", None, None, "Elevated", "x", detail, LEGACY_PMIDS
        )
        assert pmids == list(dict.fromkeys(CURRENT_PMIDS + _panel_pmids("rs9939609")))

    def test_a_moved_category_in_one_pathway_snp_keeps_the_stored_union(self) -> None:
        detail = {
            "snp_details": [
                {
                    "rsid": NOD2,
                    "genotype": "TT",
                    "category": "Moderate",
                    "effect_summary": LEGACY_TT,
                }
            ]
        }
        _, out, pmids = refreshed_gene_health_row(
            "gene_health", "pathway_summary", None, None, "Moderate", "x", detail, LEGACY_PMIDS
        )
        assert out is not None
        assert out["snp_details"][0]["effect_summary"] == LEGACY_TT
        assert pmids == LEGACY_PMIDS


# ── Every surface that renders the stored rows ────────────────────────────

LEGACY_FINDINGS = [
    {
        "module": "gene_health",
        "category": "pathway_summary",
        # Below the SNP finding so the summary endpoint previews the SNP row.
        "evidence_level": 3,
        "finding_text": f"{PATHWAY} — Elevated consideration",
        "pathway": PATHWAY,
        "pathway_level": "Elevated",
        "pmid_citations": json.dumps(LEGACY_PMIDS),
        "detail_json": json.dumps(
            {
                "pathway_id": "autoimmune",
                "called_snps": 1,
                "total_snps": 1,
                "missing_snps": [],
                "snp_details": [
                    {
                        "rsid": NOD2,
                        "gene": "NOD2",
                        "variant_name": "R702W",
                        "genotype": "TT",
                        "category": "Elevated",
                        "effect_summary": LEGACY_TT,
                        "evidence_level": 4,
                    }
                ],
            }
        ),
    },
    {
        "module": "gene_health",
        "category": "snp_finding",
        "evidence_level": 4,
        "gene_symbol": "NOD2",
        "rsid": NOD2,
        "finding_text": LEGACY_TEXT,
        "pathway": PATHWAY,
        "pathway_level": "Elevated",
        "pmid_citations": json.dumps(LEGACY_PMIDS),
        "detail_json": json.dumps({"variant_name": "R702W", "genotype": "TT"}),
    },
]


@pytest.fixture
def legacy_sample(tmp_path: Path) -> Generator[tuple[TestClient, int, sa.Engine], None, None]:
    data_dir = tmp_path / "data"
    (data_dir / "samples").mkdir(parents=True)
    settings = Settings(data_dir=data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        sample_id = conn.execute(
            samples.insert().values(
                name="legacy",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="hash_legacy_nod2",
            )
        ).lastrowid
    ref_engine.dispose()

    sample_engine = sa.create_engine(f"sqlite:///{data_dir / 'samples' / 'sample_1.db'}")
    create_sample_tables(sample_engine)
    with sample_engine.begin() as conn:
        for finding in LEGACY_FINDINGS:
            conn.execute(findings.insert().values(**finding))

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        DBRegistry(settings)
        from backend.main import create_app

        with TestClient(create_app()) as client:
            yield client, sample_id, sample_engine

        reset_registry()
    sample_engine.dispose()


class TestEverySurfaceServesTheCorrection:
    def test_pathway_detail(self, legacy_sample: tuple[TestClient, int, sa.Engine]) -> None:
        client, sample_id, _ = legacy_sample
        resp = client.get(f"/api/analysis/gene_health/pathway/autoimmune?sample_id={sample_id}")
        assert resp.status_code == 200
        snps = [s for s in resp.json()["snp_details"] if s["rsid"] == NOD2]
        assert len(snps) == 1
        assert snps[0]["effect_summary"] == CURRENT_TT
        assert snps[0]["category"] == "Elevated"
        assert snps[0]["pmids"] == CURRENT_PMIDS
        assert resp.json()["pmids"] == CURRENT_PMIDS

    def test_pathway_list(self, legacy_sample: tuple[TestClient, int, sa.Engine]) -> None:
        client, sample_id, _ = legacy_sample
        resp = client.get(f"/api/analysis/gene_health/pathways?sample_id={sample_id}")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["pmids"] == CURRENT_PMIDS

    def test_generic_findings_list(self, legacy_sample: tuple[TestClient, int, sa.Engine]) -> None:
        client, sample_id, _ = legacy_sample
        resp = client.get(f"/api/analysis/findings?sample_id={sample_id}&module=gene_health")
        assert resp.status_code == 200
        rows = {r["category"]: r for r in resp.json()}
        assert set(rows) == {"pathway_summary", "snp_finding"}
        assert rows["snp_finding"]["finding_text"] == f"NOD2 R702W (TT) — {CURRENT_TT}"
        entries = rows["pathway_summary"]["detail"]["snp_details"]
        assert len(entries) == 1
        assert entries[0]["effect_summary"] == CURRENT_TT
        assert rows["snp_finding"]["pmid_citations"] == CURRENT_PMIDS
        assert rows["pathway_summary"]["pmid_citations"] == CURRENT_PMIDS

    def test_findings_summary_preview(
        self, legacy_sample: tuple[TestClient, int, sa.Engine]
    ) -> None:
        client, sample_id, _ = legacy_sample
        resp = client.get(f"/api/analysis/findings/summary?sample_id={sample_id}")
        assert resp.status_code == 200
        modules = {m["module"]: m for m in resp.json()["modules"]}
        preview = modules["gene_health"]["top_finding_text"]
        assert preview == f"NOD2 R702W (TT) — {CURRENT_TT}"

    def test_report(self, legacy_sample: tuple[TestClient, int, sa.Engine]) -> None:
        _, sample_id, sample_engine = legacy_sample
        rows = [
            r for r in _load_findings(sample_engine, modules=["gene_health"]) if r["rsid"] == NOD2
        ]
        assert len(rows) == 1
        assert rows[0]["finding_text"] == f"NOD2 R702W (TT) — {CURRENT_TT}"
        assert rows[0]["pmid_citations"] == CURRENT_PMIDS
        # The rendered document is what the user keeps.
        html = render_report_html(sample_id=sample_id, modules=["gene_health"])
        assert "rather than estimating R702W homozygotes separately" in html
        assert "20-40 fold" not in html
        for pmid in POOLED_SOURCES:
            assert pmid in html

    def test_variant_card(self, legacy_sample: tuple[TestClient, int, sa.Engine]) -> None:
        _, _, sample_engine = legacy_sample
        with sample_engine.connect() as conn:
            fid = conn.execute(
                sa.select(findings.c.id).where(findings.c.category == "snp_finding")
            ).scalar_one()
        card = _load_single_finding(sample_engine, fid)
        assert card["finding_text"] == f"NOD2 R702W (TT) — {CURRENT_TT}"
        assert card["pmid_citations"] == CURRENT_PMIDS
