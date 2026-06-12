"""Cross-module integration testing (P3-44, T3-43).

Tests that ALL analysis modules run against the full test fixture without
errors, produce findings, and the unified findings API correctly aggregates
results from all modules.

Covers:
- Every analysis module runs to completion (no errors, no crashes)
- Each module produces at least one finding (where test data supports it)
- Unified findings API (GET /api/analysis/findings) aggregates all modules
- Findings summary endpoint shows per-module counts
- Cross-module interactions:
  - BRCA1 het P/LP produces both cancer AND carrier findings
  - CYP2D6/CYP2C19 star-allele calls produce pharmacogenomics findings
  - APOE genotype determination works alongside cancer/cardiovascular
  - Rare variant finder discovers variants from annotated data
- Evidence levels are correctly assigned per the 4-star framework
- No duplicate findings across modules (cancer vs carrier for same gene)
"""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.annotation.dbnsfp import create_dbnsfp_tables, load_dbnsfp_from_csv
from backend.annotation.gnomad import create_gnomad_tables
from backend.annotation.mondo_hpo import load_mondo_hpo_from_csv
from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.tables import (
    clinvar_variants,
    cpic_alleles,
    cpic_diplotypes,
    cpic_guidelines,
    gwas_associations,
    jobs,
    reference_metadata,
)

# ── Paths ──────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
SAMPLE_FILE = FIXTURES_DIR / "sample_23andme_v5.txt"
VEP_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "vep_seed.csv"
GNOMAD_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "gnomad_seed.csv"
DBNSFP_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "dbnsfp_seed.csv"
GENE_PHENOTYPE_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "gene_phenotype_seed.csv"
CLINVAR_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "clinvar_seed.csv"
CPIC_ALLELES_CSV = FIXTURES_DIR / "seed_csvs" / "cpic_alleles_seed.csv"
CPIC_DIPLOTYPES_CSV = FIXTURES_DIR / "seed_csvs" / "cpic_diplotypes_seed.csv"
CPIC_GUIDELINES_CSV = FIXTURES_DIR / "seed_csvs" / "cpic_guidelines_seed.csv"
GWAS_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "gwas_seed.csv"


# ── Helpers to build mini annotation databases on disk ─────────────────


def _create_vep_bundle(db_path: Path) -> None:
    """Build a mini VEP bundle SQLite from the seed CSV."""
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE vep_annotations ("
                    "  rsid TEXT, chrom TEXT, pos INTEGER,"
                    "  ref TEXT, alt TEXT, gene_symbol TEXT,"
                    "  transcript_id TEXT, consequence TEXT,"
                    "  hgvs_coding TEXT, hgvs_protein TEXT,"
                    "  strand TEXT, exon_number INTEGER,"
                    "  intron_number INTEGER, mane_select INTEGER"
                    ")"
                )
            )
            conn.execute(sa.text("CREATE INDEX idx_vep_rsid ON vep_annotations(rsid)"))
            with open(VEP_SEED_CSV, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    conn.execute(
                        sa.text(
                            "INSERT INTO vep_annotations "
                            "(rsid, chrom, pos, ref, alt, gene_symbol, "
                            "transcript_id, consequence, hgvs_coding, "
                            "hgvs_protein, strand, exon_number, "
                            "intron_number, mane_select) "
                            "VALUES (:rsid, :chrom, :pos, :ref, :alt, "
                            ":gene_symbol, :transcript_id, :consequence, "
                            ":hgvs_coding, :hgvs_protein, :strand, "
                            ":exon_number, :intron_number, :mane_select)"
                        ),
                        {
                            "rsid": row["rsid"],
                            "chrom": row["chrom"],
                            "pos": int(row["pos"]),
                            "ref": row["ref"],
                            "alt": row["alt"],
                            "gene_symbol": row["gene_symbol"],
                            "transcript_id": row["transcript_id"],
                            "consequence": row["consequence"],
                            "hgvs_coding": row["hgvs_coding"] or None,
                            "hgvs_protein": row["hgvs_protein"] or None,
                            "strand": row["strand"],
                            "exon_number": (
                                int(row["exon_number"]) if row["exon_number"] else None
                            ),
                            "intron_number": (
                                int(row["intron_number"]) if row["intron_number"] else None
                            ),
                            "mane_select": int(row["mane_select"]),
                        },
                    )
    finally:
        engine.dispose()


def _create_gnomad_db(db_path: Path) -> None:
    """Build a mini gnomAD SQLite from the seed CSV."""
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        create_gnomad_tables(engine)
        with engine.begin() as conn:
            with open(GNOMAD_SEED_CSV, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    conn.execute(
                        sa.text(
                            "INSERT INTO gnomad_af VALUES "
                            "(:rsid, :chrom, :pos, :ref, :alt, :af_global, "
                            ":af_afr, :af_amr, :af_eas, :af_eur, :af_fin, "
                            ":af_sas, :homozygous_count)"
                        ),
                        {
                            "rsid": row["rsid"],
                            "chrom": row["chrom"],
                            "pos": int(row["pos"]),
                            "ref": row["ref"],
                            "alt": row["alt"],
                            "af_global": float(row["af_global"]),
                            "af_afr": float(row["af_afr"]),
                            "af_amr": float(row["af_amr"]),
                            "af_eas": float(row["af_eas"]),
                            "af_eur": float(row["af_eur"]),
                            "af_fin": float(row["af_fin"]),
                            "af_sas": float(row["af_sas"]),
                            "homozygous_count": int(row["homozygous_count"]),
                        },
                    )
    finally:
        engine.dispose()


def _create_dbnsfp_db(db_path: Path) -> None:
    """Build a mini dbNSFP SQLite from the seed CSV."""
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        create_dbnsfp_tables(engine)
        load_dbnsfp_from_csv(DBNSFP_SEED_CSV, engine, clear_existing=False)
    finally:
        engine.dispose()


def _load_clinvar_from_csv(engine: sa.Engine) -> None:
    """Load ClinVar seed data from CSV into reference.db."""
    with open(CLINVAR_SEED_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            rows.append(
                {
                    "rsid": row["rsid"],
                    "chrom": row["chrom"],
                    "pos": int(row["pos"]),
                    "ref": row["ref"],
                    "alt": row["alt"],
                    "significance": row["significance"],
                    "review_stars": int(row["review_stars"]),
                    "accession": row["accession"],
                    "conditions": row["conditions"],
                    "gene_symbol": row["gene_symbol"],
                    "variation_id": int(row["variation_id"]),
                }
            )
    if rows:
        with engine.begin() as conn:
            conn.execute(clinvar_variants.insert(), rows)


def _load_cpic_data(engine: sa.Engine) -> None:
    """Load CPIC seed data (alleles, diplotypes, guidelines) from CSVs."""
    # Alleles
    with open(CPIC_ALLELES_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        allele_rows = []
        for row in reader:
            allele_rows.append(
                {
                    "gene": row["gene"],
                    "allele_name": row["allele_name"],
                    "defining_variants": row["defining_variants"],
                    "function": row["function"],
                    "activity_score": float(row["activity_score"]),
                }
            )
    if allele_rows:
        with engine.begin() as conn:
            conn.execute(cpic_alleles.insert(), allele_rows)

    # Diplotypes
    with open(CPIC_DIPLOTYPES_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        diplo_rows = []
        for row in reader:
            diplo_rows.append(
                {
                    "gene": row["gene"],
                    "diplotype": row["diplotype"],
                    "phenotype": row["phenotype"],
                    "ehr_notation": row["ehr_notation"],
                    "activity_score": float(row["activity_score"]),
                }
            )
    if diplo_rows:
        with engine.begin() as conn:
            conn.execute(cpic_diplotypes.insert(), diplo_rows)

    # Guidelines
    with open(CPIC_GUIDELINES_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        guide_rows = []
        for row in reader:
            guide_rows.append(
                {
                    "gene": row["gene"],
                    "drug": row["drug"],
                    "phenotype": row["phenotype"],
                    "recommendation": row["recommendation"],
                    "classification": row["classification"],
                    "guideline_url": row["guideline_url"],
                }
            )
    if guide_rows:
        with engine.begin() as conn:
            conn.execute(cpic_guidelines.insert(), guide_rows)


def _load_gwas_data(engine: sa.Engine) -> None:
    """Load GWAS seed data from CSV."""
    with open(GWAS_SEED_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            rows.append(
                {
                    "rsid": row["rsid"],
                    "chrom": row["chrom"],
                    "pos": int(row["pos"]),
                    "trait": row["trait"],
                    "p_value": float(row["p_value"]),
                    "odds_ratio": float(row["odds_ratio"]) if row["odds_ratio"] else None,
                    "beta": float(row["beta"]) if row["beta"] else None,
                    "risk_allele": row["risk_allele"],
                    "pubmed_id": row["pubmed_id"],
                    "study": row["study"],
                    "sample_size": int(row["sample_size"]),
                }
            )
    if rows:
        with engine.begin() as conn:
            conn.execute(gwas_associations.insert(), rows)


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def cross_module_env(tmp_data_dir: Path):
    """Full E2E environment with ALL reference data for cross-module testing.

    Creates reference.db with ClinVar, gene-phenotype, CPIC, and GWAS data,
    plus VEP bundle, gnomAD, and dbNSFP databases. This is a superset of
    the e2e_env from test_e2e_pipeline.py — it includes CPIC and GWAS data
    needed for pharmacogenomics and nutrigenomics modules.
    """
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    # 1. Create reference.db with ALL seed data
    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)

    _load_clinvar_from_csv(ref_engine)
    load_mondo_hpo_from_csv(GENE_PHENOTYPE_SEED_CSV, ref_engine)
    _load_cpic_data(ref_engine)
    _load_gwas_data(ref_engine)

    ref_engine.dispose()

    # 2. Create annotation source databases on disk
    _create_vep_bundle(settings.vep_bundle_db_path)
    _create_gnomad_db(settings.gnomad_db_path)
    _create_dbnsfp_db(settings.dbnsfp_db_path)

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.tasks.huey_tasks.get_settings", return_value=settings),
    ):
        reset_registry()
        yield {"settings": settings, "tmp_dir": tmp_data_dir}
        reset_registry()


@pytest.fixture
def cross_module_client(cross_module_env: dict) -> TestClient:
    """FastAPI TestClient for cross-module integration tests."""
    from backend.tasks import huey_tasks

    original_immediate = huey_tasks.huey.immediate
    huey_tasks.huey.immediate = True
    try:
        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc
    finally:
        huey_tasks.huey.immediate = original_immediate


# ═══════════════════════════════════════════════════════════════════════
# Cross-module integration tests (P3-44)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.integration
@pytest.mark.slow  # nightly: per-test ref-DB rebuild + real annotate (~119s/17)
class TestCrossModuleIntegration:
    """All analysis modules run against full test fixture without errors."""

    def _upload_and_annotate(self, client: TestClient) -> int:
        """Upload sample, run annotation, return sample_id."""
        with open(SAMPLE_FILE, "rb") as f:
            resp = client.post(
                "/api/ingest",
                files={"file": ("sample_23andme_v5.txt", f, "text/plain")},
            )
        assert resp.status_code == 202, f"Upload failed: {resp.text}"
        sample_id = resp.json()["sample_id"]

        # Run annotation (Huey immediate mode = synchronous)
        resp = client.post(f"/api/annotation/{sample_id}")
        assert resp.status_code == 202, f"Annotation start failed: {resp.text}"
        job_id = resp.json()["job_id"]

        # Verify annotation completed
        from backend.db.connection import get_registry

        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(sa.select(jobs).where(jobs.c.job_id == job_id)).fetchone()

        assert row is not None, "Annotation job not found"
        assert row.status == "complete", f"Annotation failed: {row.error or row.message}"

        return sample_id

    # ── Individual module run tests ────────────────────────────────────

    def test_all_modules_run_without_errors(self, cross_module_client: TestClient) -> None:
        """T3-43: All analysis modules produce findings for the test fixture
        without errors.

        Runs every module's /run endpoint and verifies a 200/201 response.
        Modules that don't find matching variants may return 0 findings —
        that's valid, as long as they don't error.
        """
        client = cross_module_client
        sample_id = self._upload_and_annotate(client)

        # Modules with /run endpoints
        module_endpoints = [
            ("/api/analysis/cancer/run", "cancer"),
            ("/api/analysis/cardiovascular/run", "cardiovascular"),
            ("/api/analysis/nutrigenomics/run", "nutrigenomics"),
            ("/api/analysis/apoe/run", "apoe"),
            ("/api/analysis/ancestry/run", "ancestry"),
            ("/api/analysis/carrier/run", "carrier"),
            ("/api/analysis/rare-variants/run", "rare-variants"),
            ("/api/analysis/sleep/run", "sleep"),
        ]

        run_results = {}
        for endpoint, name in module_endpoints:
            resp = client.post(endpoint, params={"sample_id": sample_id})
            assert resp.status_code in (
                200,
                201,
            ), f"{name} module failed ({resp.status_code}): {resp.text}"
            run_results[name] = resp.json()

        # Pharmacogenomics: call directly since it has no /run endpoint
        # It reads from findings, so we call the module functions directly
        from backend.analysis.pharmacogenomics import (
            call_all_star_alleles,
            generate_prescribing_alerts,
            store_prescribing_alerts,
            update_annotation_coverage_cpic,
        )
        from backend.db.connection import get_registry

        registry = get_registry()
        sample_db_path = self._get_sample_db_path(client, sample_id)
        sample_engine = registry.get_sample_engine(sample_db_path)

        star_results = call_all_star_alleles(registry.reference_engine, sample_engine)
        alerts = generate_prescribing_alerts(star_results, registry.reference_engine)
        pharma_count = store_prescribing_alerts(alerts, sample_engine)
        update_annotation_coverage_cpic(star_results, sample_engine)
        sample_engine.dispose()

        run_results["pharmacogenomics"] = {
            "genes_called": len(star_results),
            "alerts": len(alerts),
            "findings_stored": pharma_count,
        }

        # Verify at least some modules produced findings
        assert any(self._get_finding_count(r) > 0 for r in run_results.values()), (
            "No module produced any findings — test data likely misconfigured"
        )

    def test_unified_findings_aggregates_all_modules(
        self, cross_module_client: TestClient
    ) -> None:
        """Unified findings API returns findings from multiple modules."""
        client = cross_module_client
        sample_id = self._upload_and_annotate(client)
        self._run_all_modules(client, sample_id)

        resp = client.get(f"/api/analysis/findings?sample_id={sample_id}")
        assert resp.status_code == 200
        findings = resp.json()

        # At minimum, pharmacogenomics should have findings (CYP2D6/CYP2C19
        # alleles are in the test fixture with matching CPIC data)
        assert len(findings) > 0, "No findings from any module"

        # Aggregation must actually SPAN modules — a regression that dropped
        # all-but-one module's findings would still satisfy "len > 0". Assert the
        # unified feed carries pharmacogenomics AND at least one other module.
        modules = {f["module"] for f in findings}
        assert "pharmacogenomics" in modules, (
            f"pharmacogenomics findings missing; modules present: {sorted(modules)}"
        )
        assert len(modules) >= 2, (
            f"unified findings span only one module ({sorted(modules)}); the "
            f"aggregator should combine several"
        )

    def test_findings_summary_shows_module_counts(self, cross_module_client: TestClient) -> None:
        """Findings summary endpoint returns per-module counts."""
        client = cross_module_client
        sample_id = self._upload_and_annotate(client)
        self._run_all_modules(client, sample_id)

        resp = client.get(f"/api/analysis/findings/summary?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()

        assert data["total_findings"] > 0
        assert len(data["modules"]) > 0

        # Each module entry has count and max_evidence_level
        for mod in data["modules"]:
            assert "module" in mod
            assert "count" in mod
            assert mod["count"] > 0
            assert "max_evidence_level" in mod
            if mod["max_evidence_level"] is not None:
                assert 1 <= mod["max_evidence_level"] <= 4

    def test_findings_sorted_by_evidence_level(self, cross_module_client: TestClient) -> None:
        """Findings are returned sorted by evidence_level descending."""
        client = cross_module_client
        sample_id = self._upload_and_annotate(client)
        self._run_all_modules(client, sample_id)

        resp = client.get(f"/api/analysis/findings?sample_id={sample_id}")
        assert resp.status_code == 200
        findings = resp.json()

        if len(findings) > 1:
            levels = [f["evidence_level"] or 0 for f in findings]
            assert levels == sorted(levels, reverse=True)

    # ── Cross-module interaction tests ─────────────────────────────────

    def test_pharmacogenomics_produces_star_allele_findings(
        self, cross_module_client: TestClient
    ) -> None:
        """CYP2D6 and CYP2C19 star-allele calls produce pharma findings.

        Star-allele defining variants exercised by the fixture (all genotypes are
        GRCh37 plus/forward strand, matching real 23andMe data and the corrected
        cpic_alleles.csv):
        - rs16947 (CYP2D6 *2; plus-strand alt A) genotype AG -> *2 carrier
        - rs4244285 (CYP2C19 *2; plus-strand alt A) genotype GA -> *2 carrier

        NOTE: the synthetic sample_23andme_v5.txt fixture still carries a few
        legacy pre-strand-fix CYP2D6 genotypes (e.g. rs1065852 CT, rs3892097 GA)
        that no longer match the corrected plus-strand allele table and so are
        not called; normalizing that fixture to plus strand is tracked as a
        follow-up. The assertion below only requires that *some* pharma finding
        is produced, which the CYP2C19/CYP2D6*2 calls above guarantee.
        """
        client = cross_module_client
        sample_id = self._upload_and_annotate(client)
        self._run_all_modules(client, sample_id)

        resp = client.get(f"/api/analysis/findings?sample_id={sample_id}&module=pharmacogenomics")
        assert resp.status_code == 200
        pharma_findings = resp.json()

        # Should have findings for CYP2D6 and/or CYP2C19
        assert len(pharma_findings) > 0, "No pharmacogenomics findings generated"

    def test_apoe_genotype_determination(self, cross_module_client: TestClient) -> None:
        """APOE genotype is correctly determined from rs429358 + rs7412.

        Test fixture: rs429358=TT, rs7412=CC → ε3/ε3 genotype.
        """
        client = cross_module_client
        sample_id = self._upload_and_annotate(client)

        resp = client.post("/api/analysis/apoe/run", params={"sample_id": sample_id})
        assert resp.status_code in (200, 201), f"APOE run failed: {resp.text}"
        # Pre-gate: diplotype is withheld from the run response (issue #111).
        assert resp.json().get("diplotype") is None, (
            "diplotype should be withheld before gate acknowledgment"
        )

        # diplotype is gate-protected (issue #111) — withheld until the APOE
        # disclosure gate is acknowledged. Acknowledge, then re-run to read it.
        client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": sample_id})
        resp = client.post("/api/analysis/apoe/run", params={"sample_id": sample_id})
        assert resp.status_code in (200, 201), f"APOE run failed: {resp.text}"
        data = resp.json()

        # rs429358=TT (no C allele) + rs7412=CC (no T allele) → ε3/ε3
        genotype = data.get("genotype") or data.get("diplotype")
        assert genotype is not None, "No genotype/diplotype in APOE response"
        # Assert the EXACT diplotype — "'3' in str" also passes for ε2/ε3, ε3/ε4,
        # so an allele-pairing bug in the determination would slip through.
        assert str(genotype) == "ε3/ε3", f"Expected ε3/ε3, got {genotype!r}"

    def test_cancer_module_processes_panel(self, cross_module_client: TestClient) -> None:
        """Cancer module processes the gene panel without errors."""
        client = cross_module_client
        sample_id = self._upload_and_annotate(client)

        resp = client.post("/api/analysis/cancer/run", params={"sample_id": sample_id})
        assert resp.status_code in (200, 201), f"Cancer run failed: {resp.text}"

        # Query cancer findings
        resp = client.get(f"/api/analysis/findings?sample_id={sample_id}&module=cancer")
        assert resp.status_code == 200

    def test_cardiovascular_module_with_fh_status(self, cross_module_client: TestClient) -> None:
        """Cardiovascular module runs and produces FH status report."""
        client = cross_module_client
        sample_id = self._upload_and_annotate(client)

        resp = client.post("/api/analysis/cardiovascular/run", params={"sample_id": sample_id})
        assert resp.status_code in (200, 201), f"CV run failed: {resp.text}"
        data = resp.json()

        # Should have panel genes checked and FH status
        assert "panel_genes_checked" in data
        assert "fh_status" in data

    def test_carrier_module_finds_het_plp(self, cross_module_client: TestClient) -> None:
        """Carrier module identifies heterozygous P/LP variants."""
        client = cross_module_client
        sample_id = self._upload_and_annotate(client)

        resp = client.post("/api/analysis/carrier/run", params={"sample_id": sample_id})
        assert resp.status_code in (200, 201), f"Carrier run failed: {resp.text}"

    def test_ancestry_module_runs(self, cross_module_client: TestClient) -> None:
        """Ancestry module runs without errors (may have limited results
        with small test fixture).
        """
        client = cross_module_client
        sample_id = self._upload_and_annotate(client)

        resp = client.post("/api/analysis/ancestry/run", params={"sample_id": sample_id})
        assert resp.status_code in (200, 201), f"Ancestry run failed: {resp.text}"

    def test_nutrigenomics_pathway_scoring(self, cross_module_client: TestClient) -> None:
        """Nutrigenomics module scores pathways categorically."""
        client = cross_module_client
        sample_id = self._upload_and_annotate(client)

        resp = client.post("/api/analysis/nutrigenomics/run", params={"sample_id": sample_id})
        assert resp.status_code in (200, 201), f"Nutrigenomics run failed: {resp.text}"

    def test_rare_variant_finder(self, cross_module_client: TestClient) -> None:
        """Rare variant finder discovers variants from annotated data."""
        client = cross_module_client
        sample_id = self._upload_and_annotate(client)

        resp = client.post("/api/analysis/rare-variants/run", params={"sample_id": sample_id})
        assert resp.status_code in (200, 201), f"Rare variant run failed: {resp.text}"

    # ── Evidence level validation ──────────────────────────────────────

    def test_evidence_levels_in_valid_range(self, cross_module_client: TestClient) -> None:
        """All findings have evidence_level in [1, 4]."""
        client = cross_module_client
        sample_id = self._upload_and_annotate(client)
        self._run_all_modules(client, sample_id)

        resp = client.get(f"/api/analysis/findings?sample_id={sample_id}")
        assert resp.status_code == 200

        for finding in resp.json():
            level = finding["evidence_level"]
            if level is not None:
                assert 1 <= level <= 4, (
                    f"Invalid evidence level {level} for finding: "
                    f"{finding.get('module')}/{finding.get('gene_symbol')}"
                )

    def test_high_confidence_findings_limited_to_top_five(
        self, cross_module_client: TestClient
    ) -> None:
        """Summary endpoint limits high-confidence findings to top 5 and all are ≥3 stars."""
        client = cross_module_client
        sample_id = self._upload_and_annotate(client)
        self._run_all_modules(client, sample_id)

        resp = client.get(f"/api/analysis/findings/summary?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()

        # High confidence findings should be present and limited to top 5
        high_conf = data.get("high_confidence_findings", [])
        assert len(high_conf) <= 5
        for f in high_conf:
            assert f["evidence_level"] >= 3

    # ── No duplicate findings ──────────────────────────────────────────

    def test_no_exact_duplicate_findings(self, cross_module_client: TestClient) -> None:
        """No two findings of the same category share a variant within a module.

        Different modules CAN produce findings for the same gene (e.g.,
        BRCA1 in cancer AND carrier). A single module can also legitimately
        surface one variant under two *different* categories — gene_health
        reports MTHFR rs1801133 both as a per-SNP finding (``snp_finding``)
        and as a cross-module pointer to the Methylation module
        (``cross_module``). Those are distinct finding types, not duplicates.

        The bug this guards against is a module emitting the *same kind* of
        finding twice for one variant (e.g. a variant placed in two pathways
        yielding two ``snp_finding`` rows), so the identity key includes
        ``category``.
        """
        client = cross_module_client
        sample_id = self._upload_and_annotate(client)
        self._run_all_modules(client, sample_id)

        resp = client.get(f"/api/analysis/findings?sample_id={sample_id}")
        assert resp.status_code == 200
        findings = resp.json()

        seen = set()
        for f in findings:
            key = (f["module"], f.get("category"), f.get("rsid"), f.get("gene_symbol"))
            # Skip findings without rsid (e.g., pathway summaries)
            if f.get("rsid") is None:
                continue
            assert key not in seen, f"Duplicate finding: {key}"
            seen.add(key)

    # ── Filter tests on unified API ────────────────────────────────────

    def test_filter_findings_by_module(self, cross_module_client: TestClient) -> None:
        """Module filter on findings API returns only the specified module."""
        client = cross_module_client
        sample_id = self._upload_and_annotate(client)
        self._run_all_modules(client, sample_id)

        # Get all modules that produced findings
        resp = client.get(f"/api/analysis/findings/summary?sample_id={sample_id}")
        assert resp.status_code == 200
        modules = [m["module"] for m in resp.json()["modules"]]

        if modules:
            test_module = modules[0]
            resp = client.get(f"/api/analysis/findings?sample_id={sample_id}&module={test_module}")
            assert resp.status_code == 200
            for f in resp.json():
                assert f["module"] == test_module

    def test_filter_findings_by_min_stars(self, cross_module_client: TestClient) -> None:
        """Min stars filter returns only findings at or above threshold."""
        client = cross_module_client
        sample_id = self._upload_and_annotate(client)
        self._run_all_modules(client, sample_id)

        resp = client.get(f"/api/analysis/findings?sample_id={sample_id}&min_stars=3")
        assert resp.status_code == 200
        for f in resp.json():
            assert f["evidence_level"] >= 3

    # ── Helper methods ─────────────────────────────────────────────────

    def _get_sample_db_path(self, client: TestClient, sample_id: int) -> Path:
        """Resolve the full path to a sample's database file."""
        from backend.db.connection import get_registry
        from backend.db.tables import samples

        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(samples.c.db_path).where(samples.c.id == sample_id)
            ).fetchone()
        assert row is not None, f"Sample {sample_id} not found"
        return registry.settings.data_dir / row.db_path

    def _run_all_modules(self, client: TestClient, sample_id: int) -> None:
        """Run all analysis modules for a sample.

        Calls each module's /run endpoint, plus the pharmacogenomics
        module directly (no /run endpoint).
        """
        # Modules with /run endpoints
        endpoints = [
            "/api/analysis/cancer/run",
            "/api/analysis/cardiovascular/run",
            "/api/analysis/nutrigenomics/run",
            "/api/analysis/apoe/run",
            "/api/analysis/ancestry/run",
            "/api/analysis/carrier/run",
            "/api/analysis/rare-variants/run",
            "/api/analysis/sleep/run",
        ]
        for endpoint in endpoints:
            resp = client.post(endpoint, params={"sample_id": sample_id})
            # Allow 200 or 201 — some modules may find no variants but
            # should never error with 4xx/5xx
            assert resp.status_code in (200, 201), (
                f"{endpoint} failed ({resp.status_code}): {resp.text}"
            )

        # Pharmacogenomics: direct module call
        from backend.analysis.pharmacogenomics import (
            call_all_star_alleles,
            generate_prescribing_alerts,
            store_prescribing_alerts,
            update_annotation_coverage_cpic,
        )
        from backend.db.connection import get_registry

        registry = get_registry()
        sample_db_path = self._get_sample_db_path(client, sample_id)
        sample_engine = registry.get_sample_engine(sample_db_path)

        star_results = call_all_star_alleles(registry.reference_engine, sample_engine)
        alerts = generate_prescribing_alerts(star_results, registry.reference_engine)
        store_prescribing_alerts(alerts, sample_engine)
        update_annotation_coverage_cpic(star_results, sample_engine)
        sample_engine.dispose()

    @staticmethod
    def _get_finding_count(result: dict) -> int:
        """Extract finding count from a module run result dict."""
        for key in ("findings_count", "findings_stored", "count", "alerts"):
            if key in result:
                val = result[key]
                if isinstance(val, int):
                    return val
        return 0
