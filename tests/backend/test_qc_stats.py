"""Tests for QC stats API endpoint and genotype classification (P1-21).

T1-06: QC metrics computation: call rate, heterozygosity rate on known fixture.
T1-20: QC metrics match hand-calculated values for test fixture.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.analysis.qc import compute_qc_metrics
from backend.api.routes.variants import _classify_genotype
from backend.config import Settings
from backend.db.connection import DBRegistry, reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants, reference_metadata, samples

# Known test variants with deterministic genotype classification:
# het: AG, CT, TC → 3
# hom: AA, GG, CC, CC, AA → 5
# nocall: -- → 1
# haploid (hom): A → 1
# Total: 10, called: 9, nocall: 1
QC_TEST_VARIANTS = [
    {"rsid": "rs100", "chrom": "1", "pos": 50000, "genotype": "AG"},  # het
    {"rsid": "rs101", "chrom": "1", "pos": 100000, "genotype": "AA"},  # hom
    {"rsid": "rs102", "chrom": "1", "pos": 200000, "genotype": "--"},  # nocall
    {"rsid": "rs200", "chrom": "2", "pos": 10000, "genotype": "GG"},  # hom
    {"rsid": "rs201", "chrom": "2", "pos": 20000, "genotype": "CT"},  # het
    {"rsid": "rs1000", "chrom": "10", "pos": 50000, "genotype": "CC"},  # hom
    {"rsid": "rs1900", "chrom": "19", "pos": 44908684, "genotype": "TC"},  # het
    {"rsid": "rsX001", "chrom": "X", "pos": 5000, "genotype": "AA"},  # hom
    {"rsid": "rsX002", "chrom": "X", "pos": 6000, "genotype": "A"},  # haploid (hom)
    {"rsid": "rsMT01", "chrom": "MT", "pos": 1000, "genotype": "CC"},  # hom
]


# ═══════════════════════════════════════════════════════════════════════
# Unit tests for _classify_genotype
# ═══════════════════════════════════════════════════════════════════════


class TestClassifyGenotype:
    """Unit tests for genotype classification logic."""

    def test_nocall_dashes(self):
        assert _classify_genotype("--") == "nocall"

    def test_nocall_empty(self):
        assert _classify_genotype("") == "nocall"

    def test_homozygous_aa(self):
        assert _classify_genotype("AA") == "hom"

    def test_homozygous_gg(self):
        assert _classify_genotype("GG") == "hom"

    def test_homozygous_cc(self):
        assert _classify_genotype("CC") == "hom"

    def test_homozygous_tt(self):
        assert _classify_genotype("TT") == "hom"

    def test_heterozygous_ag(self):
        assert _classify_genotype("AG") == "het"

    def test_heterozygous_ct(self):
        assert _classify_genotype("CT") == "het"

    def test_heterozygous_tc(self):
        assert _classify_genotype("TC") == "het"

    def test_haploid_single_base(self):
        assert _classify_genotype("A") == "hom"

    def test_haploid_single_t(self):
        assert _classify_genotype("T") == "hom"

    def test_deletion_d(self):
        # Single-char haploid call: still hom (not a no-call sentinel).
        assert _classify_genotype("D") == "hom"

    def test_insertion_i(self):
        # Single-char haploid call: still hom (not a no-call sentinel).
        assert _classify_genotype("I") == "hom"

    # ── Step 62 / Plan §11.3 reclassification: indel codes + AncestryDNA "00"
    # ── now bucket into nocall rather than het/hom. This is the single QC
    # ── site held to a non-byte-identical contract.

    def test_indel_di_is_nocall(self):
        # Pre-Step-62: "het". Now bucketed as nocall via is_no_call().
        assert _classify_genotype("DI") == "nocall"

    def test_indel_id_is_nocall(self):
        # Pre-Step-62: "het". Now bucketed as nocall via is_no_call().
        assert _classify_genotype("ID") == "nocall"

    def test_indel_dd_is_nocall(self):
        # Pre-Step-62: "hom". Now bucketed as nocall via is_no_call().
        assert _classify_genotype("DD") == "nocall"

    def test_indel_ii_is_nocall(self):
        # Pre-Step-62: "hom". Now bucketed as nocall via is_no_call().
        assert _classify_genotype("II") == "nocall"

    def test_ancestrydna_double_zero_is_nocall(self):
        # AncestryDNA's no-call leakage sentinel — Pre-Step-62: "het".
        assert _classify_genotype("00") == "nocall"

    def test_single_zero_is_nocall(self):
        # Legacy single-allele zero — Pre-Step-62: "hom" (single char).
        assert _classify_genotype("0") == "nocall"

    def test_single_dash_is_nocall(self):
        # Haploid 23andMe no-call on X/Y for XY individuals — Pre-Step-62: "hom".
        assert _classify_genotype("-") == "nocall"

    def test_merge_ambiguity_sentinel_is_nocall(self):
        # Step 65 flag_only merge sentinel — must be transparent to QC stats.
        assert _classify_genotype("??") == "nocall"

    def test_none_is_nocall(self):
        # Defensive: helper tolerates None even though SELECT shouldn't return it.
        assert _classify_genotype(None) == "nocall"


# ═══════════════════════════════════════════════════════════════════════
# Integration tests for GET /api/variants/qc-stats
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def qc_client(tmp_data_dir: Path):
    """TestClient with a sample pre-loaded with QC_TEST_VARIANTS."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)

    with ref_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name="qc_test",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="qchash",
            )
        )
        sample_id = result.lastrowid
    ref_engine.dispose()

    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)
    with sample_engine.begin() as conn:
        conn.execute(raw_variants.insert(), QC_TEST_VARIANTS)
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.api.routes.variants.get_registry") as mock_reg,
        patch("backend.api.routes.ingest.get_registry") as mock_reg2,
        patch("backend.api.routes.samples.get_registry") as mock_reg3,
    ):
        reset_registry()
        registry = DBRegistry(settings)
        mock_reg.return_value = registry
        mock_reg2.return_value = registry
        mock_reg3.return_value = registry

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc, sample_id

        registry.dispose_all()
        reset_registry()


class TestQCStatsEndpoint:
    """Integration tests for GET /api/variants/qc-stats."""

    def test_returns_200(self, qc_client):
        client, sid = qc_client
        response = client.get(f"/api/variants/qc-stats?sample_id={sid}")
        assert response.status_code == 200

    def test_total_variants_correct(self, qc_client):
        client, sid = qc_client
        data = client.get(f"/api/variants/qc-stats?sample_id={sid}").json()
        assert data["total_variants"] == 10

    def test_nocall_count(self, qc_client):
        client, sid = qc_client
        data = client.get(f"/api/variants/qc-stats?sample_id={sid}").json()
        assert data["nocall_variants"] == 1

    def test_called_variants(self, qc_client):
        client, sid = qc_client
        data = client.get(f"/api/variants/qc-stats?sample_id={sid}").json()
        # 10 total - 1 nocall = 9 called
        assert data["called_variants"] == 9

    def test_het_count(self, qc_client):
        client, sid = qc_client
        data = client.get(f"/api/variants/qc-stats?sample_id={sid}").json()
        # AG, CT, TC = 3 heterozygous
        assert data["het_count"] == 3

    def test_hom_count(self, qc_client):
        client, sid = qc_client
        data = client.get(f"/api/variants/qc-stats?sample_id={sid}").json()
        # AA, GG, CC, CC, AA, A (haploid) = 6 homozygous
        assert data["hom_count"] == 6

    def test_call_rate(self, qc_client):
        client, sid = qc_client
        data = client.get(f"/api/variants/qc-stats?sample_id={sid}").json()
        # 9 called / 10 total = 0.9
        assert data["call_rate"] == pytest.approx(0.9, abs=0.001)

    def test_heterozygosity_rate(self, qc_client):
        client, sid = qc_client
        data = client.get(f"/api/variants/qc-stats?sample_id={sid}").json()
        # AUTOSOMAL het / autosomal called (#513): the X (AA hom, A haploid-hom) and
        # MT (CC hom) calls are excluded from the rate. Autosomal het = AG(chr1),
        # CT(chr2), TC(chr19) = 3; autosomal hom = AA(chr1), GG(chr2), CC(chr10) = 3;
        # so 3 / (3 + 3) = 0.5 — NOT the sex-biased all-chrom 3/9 = 0.333.
        assert data["heterozygosity_rate"] == pytest.approx(0.5, abs=0.001)

    def test_per_chromosome_count(self, qc_client):
        client, sid = qc_client
        data = client.get(f"/api/variants/qc-stats?sample_id={sid}").json()
        chroms = {c["chrom"] for c in data["per_chromosome"]}
        assert chroms == {"1", "2", "10", "19", "X", "MT"}

    def test_per_chromosome_sorted(self, qc_client):
        client, sid = qc_client
        data = client.get(f"/api/variants/qc-stats?sample_id={sid}").json()
        chrom_list = [c["chrom"] for c in data["per_chromosome"]]
        assert chrom_list == ["1", "2", "10", "19", "X", "MT"]

    def test_chrom1_breakdown(self, qc_client):
        client, sid = qc_client
        data = client.get(f"/api/variants/qc-stats?sample_id={sid}").json()
        chr1 = next(c for c in data["per_chromosome"] if c["chrom"] == "1")
        assert chr1["total"] == 3
        assert chr1["het_count"] == 1  # AG
        assert chr1["hom_count"] == 1  # AA
        assert chr1["nocall_count"] == 1  # --

    def test_404_invalid_sample(self, qc_client):
        client, _ = qc_client
        response = client.get("/api/variants/qc-stats?sample_id=9999")
        assert response.status_code == 404


# A MALE sample: autosomal het+hom plus hemizygous single-char X/Y calls (which
# _classify_genotype buckets as "hom"). The hemizygous calls depress an all-chrom
# heterozygosity rate but must NOT touch the autosomal QC rate (#513).
MALE_QC_VARIANTS = [
    {"rsid": "rs1", "chrom": "1", "pos": 1000, "genotype": "AG"},  # autosomal het
    {"rsid": "rs2", "chrom": "1", "pos": 2000, "genotype": "AA"},  # autosomal hom
    {"rsid": "rs3", "chrom": "2", "pos": 3000, "genotype": "CT"},  # autosomal het
    {"rsid": "rs4", "chrom": "2", "pos": 4000, "genotype": "GG"},  # autosomal hom
    # Hemizygous single-char X (male) → bucketed hom; depresses all-chrom rate.
    {"rsid": "rsX1", "chrom": "X", "pos": 10, "genotype": "A"},
    {"rsid": "rsX2", "chrom": "X", "pos": 20, "genotype": "G"},
    {"rsid": "rsX3", "chrom": "X", "pos": 30, "genotype": "C"},
    {"rsid": "rsX4", "chrom": "X", "pos": 40, "genotype": "T"},
    {"rsid": "rsX5", "chrom": "X", "pos": 50, "genotype": "A"},
    {"rsid": "rsX6", "chrom": "X", "pos": 60, "genotype": "G"},
    {"rsid": "rsY1", "chrom": "Y", "pos": 10, "genotype": "A"},
    {"rsid": "rsY2", "chrom": "Y", "pos": 20, "genotype": "C"},
]


class TestHeterozygosityRateIsAutosomal:
    """#513: the dashboard qc-stats heterozygosity_rate must be autosomal-only, so
    it equals the canonical compute_qc_metrics value (used for outlier detection)
    and is not depressed by a male's hemizygous X/Y calls."""

    def test_qc_stats_het_rate_equals_compute_qc_metrics_for_male(
        self, tmp_data_dir: Path
    ) -> None:
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

        ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
        reference_metadata.create_all(ref_engine)
        with ref_engine.begin() as conn:
            sample_id = conn.execute(
                samples.insert().values(
                    name="male_qc",
                    db_path="samples/sample_1.db",
                    file_format="23andme_v5",
                    file_hash="malehash",
                )
            ).lastrowid
        ref_engine.dispose()

        sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
        sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
        create_sample_tables(sample_engine)
        with sample_engine.begin() as conn:
            conn.execute(raw_variants.insert(), MALE_QC_VARIANTS)

        # Canonical autosomal rate (the value het_outlier_zscore flags against).
        canonical = compute_qc_metrics(sample_engine).heterozygosity_rate
        sample_engine.dispose()
        assert canonical == pytest.approx(0.5, abs=0.001)  # 2 het / (2 het + 2 hom)

        with (
            patch("backend.main.get_settings", return_value=settings),
            patch("backend.db.connection.get_settings", return_value=settings),
            patch("backend.api.routes.variants.get_registry") as mock_reg,
        ):
            reset_registry()
            registry = DBRegistry(settings)
            mock_reg.return_value = registry
            from backend.main import create_app

            with TestClient(create_app()) as tc:
                data = tc.get(f"/api/variants/qc-stats?sample_id={sample_id}").json()
            registry.dispose_all()
            reset_registry()

        # The dashboard value now matches the canonical autosomal rate exactly...
        assert data["heterozygosity_rate"] == pytest.approx(canonical, abs=0.0001)
        # ...and is NOT the sex-biased all-chrom rate (2 het / 12 called = 0.1667),
        # which the hemizygous X/Y homs would otherwise produce.
        all_chrom_rate = data["het_count"] / data["called_variants"]
        assert all_chrom_rate == pytest.approx(2 / 12, abs=0.001)
        assert data["heterozygosity_rate"] != pytest.approx(all_chrom_rate, abs=0.01)
