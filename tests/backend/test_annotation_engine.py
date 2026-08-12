"""Tests for the annotation engine orchestrator (P2-04, P2-09, P2-12, P2-13, P2-15).

Covers:
- T2-04: Annotation engine processes 1000 variants end-to-end, all fields
  populated in annotated_variants
- P2-09: gnomAD annotation lookup integrated into engine — rsid primary,
  position-based fallback, correct rare/ultra-rare thresholds
- P2-12: dbNSFP annotation integrated into engine — rsid primary,
  position-based fallback, all 14 score fields, deleterious_count
- P2-15: Gene-phenotype annotation via MONDO/HPO lookup, joined by gene symbol
- Concurrent lookup orchestration across VEP, ClinVar, gnomAD, dbNSFP
- Bitmask computation (annotation_coverage)
- Crash recovery via F28 atomic staging-swap (annotate into staging clone, swap at end)
- Graceful degradation when sources are unavailable
- Progress callback
- Merge logic across sources
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from backend.annotation.alphamissense import create_alphamissense_table
from backend.annotation.dbnsfp import (
    DbNSFPAnnotation,
    _create_dbnsfp_indexes,
    _create_dbnsfp_table,
    load_dbnsfp_from_csv,
    lookup_dbnsfp_by_rsids,
)
from backend.annotation.engine import (
    ALPHAMISSENSE_BIT,
    CLINVAR_BIT,
    DBNSFP_BIT,
    GENE_PHENOTYPE_BIT,
    GNOMAD_BIT,
    GNOMAD_SOURCE_OBSERVED,
    GNOMAD_SOURCE_UNCOVERED,
    VEP_BIT,
    AnnotationEngineResult,
    _annot_to_dict,
    _bulk_upsert,
    _dbnsfp_annot_to_dict,
    _lookup_alphamissense,
    _lookup_clinvar,
    _lookup_dbnsfp,
    _lookup_gene_phenotype,
    _lookup_gnomad,
    _lookup_vep,
    _merge_annotations,
    _read_bundle_version,
    run_annotation,
)
from backend.annotation.gnomad import (
    RARE_AF_THRESHOLD,
    ULTRA_RARE_AF_THRESHOLD,
    GnomADAnnotation,
    _create_gnomad_indexes,
    _create_gnomad_table,
    lookup_gnomad_by_rsids,
)
from backend.annotation.mondo_hpo import (
    MONDO_HPO_INGESTION_REVISION,
    load_mondo_hpo_from_csv,
    record_mondo_hpo_version,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    annotation_state,
    clinvar_variants,
    database_versions,
    dbsnp_merges,
    gene_phenotype,
    raw_variants,
    reference_metadata,
    sample_metadata_table,
    update_history,
)
from tests.backend.vep_bundle_test_utils import seed_embedded_vep_bundle_version

# ── Fixtures ────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
VEP_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "vep_seed.csv"
GNOMAD_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "gnomad_seed.csv"
DBNSFP_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "dbnsfp_seed.csv"
GENE_PHENOTYPE_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "gene_phenotype_seed.csv"

SEED_CLINVAR = [
    {
        "rsid": "rs429358",
        "chrom": "19",
        "pos": 44908684,
        "ref": "T",
        "alt": "C",
        "significance": "risk_factor",
        "review_stars": 3,
        "accession": "VCV000017864",
        "conditions": "Alzheimer disease",
        "gene_symbol": "APOE",
        "variation_id": 17864,
    },
    {
        "rsid": "rs1801133",
        "chrom": "1",
        "pos": 11856378,
        "ref": "G",
        "alt": "A",
        "significance": "drug_response",
        "review_stars": 2,
        "accession": "VCV000003520",
        "conditions": "Homocysteinemia",
        "gene_symbol": "MTHFR",
        "variation_id": 3520,
    },
    {
        "rsid": "rs80357906",
        "chrom": "17",
        "pos": 41209080,
        "ref": "GGG",
        "alt": "GGGG",
        "significance": "Pathogenic",
        "review_stars": 3,
        "accession": "VCV000017677",
        "conditions": "Hereditary breast and ovarian cancer syndrome",
        "gene_symbol": "BRCA1",
        "variation_id": 17677,
    },
]

SEED_RAW_VARIANTS = [
    {"rsid": "rs429358", "chrom": "19", "pos": 44908684, "genotype": "TC"},
    {"rsid": "rs7412", "chrom": "19", "pos": 44908822, "genotype": "CC"},
    {"rsid": "rs1801133", "chrom": "1", "pos": 11856378, "genotype": "AG"},
    {"rsid": "rs4680", "chrom": "22", "pos": 19951271, "genotype": "AG"},
    # Generic indel token: preserve the raw call without claiming carriage.
    {"rsid": "rs80357906", "chrom": "17", "pos": 41209080, "genotype": "DI"},
    {"rsid": "rs12913832", "chrom": "15", "pos": 28365618, "genotype": "GG"},
    {"rsid": "rs7903146", "chrom": "10", "pos": 114758349, "genotype": "CT"},
    {"rsid": "rs_nomatch", "chrom": "99", "pos": 1, "genotype": "AA"},
]


@pytest.fixture
def sample_engine() -> sa.Engine:
    """In-memory sample engine with tables created."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_sample_tables(engine)
    return engine


@pytest.fixture
def sample_with_variants(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine pre-loaded with known raw variants."""
    with sample_engine.begin() as conn:
        conn.execute(raw_variants.insert(), SEED_RAW_VARIANTS)
    return sample_engine


@pytest.fixture
def vep_engine_inmemory() -> sa.Engine:
    """In-memory VEP bundle loaded from seed CSV."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
                        "exon_number": (int(row["exon_number"]) if row["exon_number"] else None),
                        "intron_number": (
                            int(row["intron_number"]) if row["intron_number"] else None
                        ),
                        "mane_select": int(row["mane_select"]),
                    },
                )
    return engine


@pytest.fixture
def reference_engine() -> sa.Engine:
    """In-memory reference engine with ClinVar and gene-phenotype data."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    reference_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(clinvar_variants.insert(), SEED_CLINVAR)
    # Load gene-phenotype seed data, stamped as a real install is. Without the
    # `database_versions` row a disease-scope lookup correctly withholds the
    # mondo_hpo rows as unproven -- and rows-without-a-stamp is a state
    # production cannot produce, since `load_mondo_hpo` always records one.
    load_mondo_hpo_from_csv(GENE_PHENOTYPE_SEED_CSV, engine)
    record_mondo_hpo_version(engine, version=f"20260801+{MONDO_HPO_INGESTION_REVISION}")
    return engine


@pytest.fixture
def gnomad_engine() -> sa.Engine:
    """In-memory gnomAD engine loaded from seed CSV with proper indexes."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Create the table then its indexes for proper table + indexes setup.
    _create_gnomad_table(engine)
    _create_gnomad_indexes(engine)
    with engine.begin() as conn:
        with open(GNOMAD_SEED_CSV, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                conn.execute(
                    sa.text(
                        "INSERT INTO gnomad_af "
                        "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, "
                        "af_asj, af_eas, af_eur, af_fin, af_sas, homozygous_count) "
                        "VALUES "
                        "(:rsid, :chrom, :pos, :ref, :alt, :af_global, "
                        ":af_afr, :af_amr, :af_asj, :af_eas, :af_eur, :af_fin, "
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
                        "af_asj": float(row["af_asj"]) if row["af_asj"] else None,
                        "af_eas": float(row["af_eas"]),
                        "af_eur": float(row["af_eur"]),
                        "af_fin": float(row["af_fin"]),
                        "af_sas": float(row["af_sas"]),
                        "homozygous_count": int(row["homozygous_count"]),
                    },
                )
    return engine


@pytest.fixture
def dbnsfp_engine() -> sa.Engine:
    """In-memory dbNSFP engine loaded from seed CSV using dbnsfp.py functions."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _create_dbnsfp_table(engine)
    _create_dbnsfp_indexes(engine)
    load_dbnsfp_from_csv(DBNSFP_SEED_CSV, engine, clear_existing=False)
    return engine


@pytest.fixture
def alphamissense_engine() -> sa.Engine:
    """In-memory AlphaMissense DB with one missense prediction."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_alphamissense_table(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO alphamissense_scores "
                "(chrom, pos, ref, alt, am_pathogenicity, am_class) "
                "VALUES ('1', 11856378, 'G', 'A', 0.91, 'likely_pathogenic')"
            )
        )
    return engine


@pytest.fixture
def mock_registry(
    tmp_path: Path,
    reference_engine: sa.Engine,
    vep_engine_inmemory: sa.Engine,
    gnomad_engine: sa.Engine,
    dbnsfp_engine: sa.Engine,
    alphamissense_engine: sa.Engine,
) -> MagicMock:
    """Mock DBRegistry with all annotation source engines."""
    registry = MagicMock()
    registry.reference_engine = reference_engine
    vep_bundle_db_path = tmp_path / "vep_bundle.db"
    vep_bundle_db_path.touch()
    registry.settings = SimpleNamespace(vep_bundle_db_path=vep_bundle_db_path)
    type(registry).vep_engine = property(lambda self: vep_engine_inmemory)
    type(registry).gnomad_engine = property(lambda self: gnomad_engine)
    type(registry).dbnsfp_engine = property(lambda self: dbnsfp_engine)
    registry.alphamissense_engine = alphamissense_engine
    return registry


# ═══════════════════════════════════════════════════════════════════════
# AnnotationEngineResult
# ═══════════════════════════════════════════════════════════════════════


class TestAnnotationEngineResult:
    def test_defaults(self) -> None:
        r = AnnotationEngineResult()
        assert r.total_variants == 0
        assert r.total_matched == 0
        assert r.errors == []
        assert r.alphamissense_matched == 0
        # §5.6 coverage telemetry — empty by default; only populated by run_annotation.
        assert r.coverage_stats == {}

    def test_total_matched_equals_rows_written(self) -> None:
        r = AnnotationEngineResult(rows_written=42)
        assert r.total_matched == 42


# ═══════════════════════════════════════════════════════════════════════
# Individual source lookups
# ═══════════════════════════════════════════════════════════════════════


class TestLookupVep:
    def test_returns_vep_fields(self, vep_engine_inmemory: sa.Engine) -> None:
        result = _lookup_vep(["rs429358"], {}, vep_engine_inmemory)
        assert "rs429358" in result
        assert result["rs429358"]["gene_symbol"] == "APOE"
        assert result["rs429358"]["consequence"] == "missense_variant"

    def test_empty_rsids(self, vep_engine_inmemory: sa.Engine) -> None:
        result = _lookup_vep([], {}, vep_engine_inmemory)
        assert len(result) == 0

    def test_multiallelic_rsid_uses_carried_alt(self, vep_engine_inmemory: sa.Engine) -> None:
        """VEP consequence/HGVS selection must be allele-specific before merge."""
        with vep_engine_inmemory.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO vep_annotations "
                    "(rsid, chrom, pos, ref, alt, gene_symbol, transcript_id, "
                    "consequence, hgvs_coding, hgvs_protein, strand, exon_number, "
                    "intron_number, mane_select) "
                    "VALUES "
                    "('rs_multi', '1', 101, 'A', 'C', 'GENE', 'ENST_C', "
                    "'synonymous_variant', 'c.1A>C', 'p.=', '+', 1, NULL, 0), "
                    "('rs_multi', '1', 101, 'A', 'G', 'GENE', 'ENST_G', "
                    "'stop_gained', 'c.1A>G', 'p.Ter', '+', 1, NULL, 0)"
                )
            )
        raw = SimpleNamespace(rsid="rs_multi", chrom="1", pos=101, genotype="AC")

        result = _lookup_vep(["rs_multi"], {"rs_multi": raw}, vep_engine_inmemory)

        assert result["rs_multi"]["transcript_id"] == "ENST_C"
        assert result["rs_multi"]["consequence"] == "synonymous_variant"
        assert result["rs_multi"]["hgvs_coding"] == "c.1A>C"
        assert result["rs_multi"]["_vep_ref"] == "A"
        assert result["rs_multi"]["_vep_alt"] == "C"


class TestLookupClinvar:
    def test_returns_clinvar_fields(self, reference_engine: sa.Engine) -> None:
        result = _lookup_clinvar(["rs429358"], {}, reference_engine)
        assert "rs429358" in result
        assert result["rs429358"]["clinvar_significance"] == "risk_factor"
        assert result["rs429358"]["clinvar_review_stars"] == 3

    def test_coord_fallback_rescues_rsid_mismatch(self, reference_engine: sa.Engine) -> None:
        """A probe whose array rsID differs from ClinVar's is rescued by (chrom, pos).

        ClinVar can file a variant under a different rsID than the array probe
        (rsID merges/withdrawals, or the chip uses an internal id). Without a
        coordinate fallback the live engine silently drops the Pathogenic record
        — ClinVar was the only annotation source lacking the fallback that VEP,
        gnomAD and dbNSFP already have. The carried allele is selected among the
        site's records: genotype ``AA`` carries the G>A Pathogenic allele, not
        the higher-star G>C Uncertain one, so a star-only pick would be wrong.
        """
        with reference_engine.begin() as conn:
            conn.execute(
                clinvar_variants.insert(),
                [
                    {
                        "rsid": "rs_clinvar_label",  # ClinVar's rsID, not the array's
                        "chrom": "2",
                        "pos": 238253397,
                        "ref": "G",
                        "alt": "C",
                        "significance": "Uncertain significance",
                        "review_stars": 3,  # highest stars — must NOT be chosen
                        "accession": "VCV_uncertain",
                        "conditions": "not provided",
                        "gene_symbol": "GENEX",
                        "variation_id": 9001,
                    },
                    {
                        "rsid": "rs_clinvar_label",
                        "chrom": "2",
                        "pos": 238253397,
                        "ref": "G",
                        "alt": "A",
                        "significance": "Pathogenic",
                        "review_stars": 2,
                        "accession": "VCV_pathogenic",
                        "conditions": "Some disease",
                        "gene_symbol": "GENEX",
                        "variation_id": 9002,
                    },
                ],
            )
        raw = SimpleNamespace(rsid="rs_array_probe", chrom="2", pos=238253397, genotype="AA")

        result = _lookup_clinvar(["rs_array_probe"], {"rs_array_probe": raw}, reference_engine)

        assert "rs_array_probe" in result, (
            "coord-fallback did not rescue the rsID-mismatched ClinVar record"
        )
        # Carriage-aware pick: the carried G>A Pathogenic allele wins over the
        # higher-star G>C Uncertain record the sample does not carry.
        assert result["rs_array_probe"]["clinvar_significance"] == "Pathogenic"
        assert result["rs_array_probe"]["alt"] == "A"

    def test_rsid_hit_requires_coordinate_concordance(self, reference_engine: sa.Engine) -> None:
        """An rsID match at a different coordinate is rejected."""
        # rs1801133 is filed at chrom 1; pretend the raw row carries a different
        # coordinate. The ClinVar hit must not be attached to that row.
        raw = SimpleNamespace(rsid="rs1801133", chrom="99", pos=1, genotype="AG")
        result = _lookup_clinvar(["rs1801133"], {"rs1801133": raw}, reference_engine)
        assert result == {}


class TestLookupGnomad:
    def test_returns_gnomad_fields(self, gnomad_engine: sa.Engine) -> None:
        result = _lookup_gnomad(["rs429358"], {}, gnomad_engine)
        assert "rs429358" in result
        data = result["rs429358"]
        assert data["gnomad_af_global"] == pytest.approx(0.1387)
        assert isinstance(data["rare_flag"], bool)
        assert data["rare_flag"] is False  # 0.1387 > 0.01

    def test_rare_flag(self, gnomad_engine: sa.Engine) -> None:
        # Insert a rare variant
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, "
                    "af_asj, af_eas, af_eur, af_fin, af_sas, homozygous_count) "
                    "VALUES "
                    "('rs_rare', '1', 1, 'A', 'G', 0.005, "
                    "0.003, 0.004, 0.0045, 0.006, 0.005, 0.002, 0.007, 5)"
                )
            )
        result = _lookup_gnomad(["rs_rare"], {}, gnomad_engine)
        assert result["rs_rare"]["rare_flag"] is True
        assert result["rs_rare"]["ultra_rare_flag"] is False

    def test_ultra_rare_flag(self, gnomad_engine: sa.Engine) -> None:
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, "
                    "af_asj, af_eas, af_eur, af_fin, af_sas, homozygous_count) "
                    "VALUES "
                    "('rs_ultrarare', '1', 2, 'A', 'G', 0.00005, "
                    "0.00003, 0.00004, 0.00004, 0.00006, 0.00005, 0.00002, 0.00007, 1)"
                )
            )
        result = _lookup_gnomad(["rs_ultrarare"], {}, gnomad_engine)
        assert result["rs_ultrarare"]["rare_flag"] is True
        assert result["rs_ultrarare"]["ultra_rare_flag"] is True

    def test_empty_rsids(self, gnomad_engine: sa.Engine) -> None:
        result = _lookup_gnomad([], {}, gnomad_engine)
        assert len(result) == 0


class TestLookupDbnsfp:
    def test_returns_dbnsfp_fields(self, dbnsfp_engine: sa.Engine) -> None:
        result = _lookup_dbnsfp(["rs429358"], {}, dbnsfp_engine)
        assert "rs429358" in result
        data = result["rs429358"]
        assert data["cadd_phred"] == pytest.approx(28.3)
        assert data["sift_pred"] == "D"
        assert data["polyphen2_hsvar_pred"] == "D"

    def test_empty_rsids(self, dbnsfp_engine: sa.Engine) -> None:
        result = _lookup_dbnsfp([], {}, dbnsfp_engine)
        assert len(result) == 0


class TestLookupAlphaMissense:
    def test_returns_context_for_missense_position(
        self,
        sample_with_variants: sa.Engine,
        vep_engine_inmemory: sa.Engine,
        alphamissense_engine: sa.Engine,
    ) -> None:
        with sample_with_variants.connect() as conn:
            raw_rows = conn.execute(sa.select(raw_variants)).fetchall()
        raw_by_rsid = {r.rsid: r for r in raw_rows}
        vep = _lookup_vep(["rs1801133"], raw_by_rsid, vep_engine_inmemory)

        result = _lookup_alphamissense(
            ["rs1801133"],
            raw_by_rsid,
            vep,
            {"rs1801133": {"ref": "G", "alt": "A"}},
            alphamissense_engine,
        )

        assert result["rs1801133"]["alphamissense_pathogenicity"] == pytest.approx(0.91)
        assert result["rs1801133"]["alphamissense_class"] == "likely_pathogenic"

    def test_skips_non_missense(
        self,
        sample_with_variants: sa.Engine,
        vep_engine_inmemory: sa.Engine,
        alphamissense_engine: sa.Engine,
    ) -> None:
        with sample_with_variants.connect() as conn:
            raw_rows = conn.execute(sa.select(raw_variants)).fetchall()
        raw_by_rsid = {r.rsid: r for r in raw_rows}
        vep = {
            "rs1801133": {"consequence": "synonymous_variant", "_vep_ref": "G", "_vep_alt": "A"}
        }

        assert (
            _lookup_alphamissense(
                ["rs1801133"],
                raw_by_rsid,
                vep,
                {},
                alphamissense_engine,
            )
            == {}
        )


# ═══════════════════════════════════════════════════════════════════════
# Merge + bitmask
# ═══════════════════════════════════════════════════════════════════════


class TestMergeAnnotations:
    def test_merge_all_sources(self) -> None:
        """Merging data from all 4 sources produces correct bitmask."""
        # Create a fake raw row
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text("CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, genotype TEXT)")
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs1', '1', 100, 'AG')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        vep = {"rs1": {"gene_symbol": "GENE1", "consequence": "missense_variant"}}
        clinvar = {"rs1": {"clinvar_significance": "Pathogenic"}}
        gnomad = {"rs1": {"gnomad_af_global": 0.01}}
        dbnsfp = {"rs1": {"cadd_phred": 25.0}}

        merged = _merge_annotations([row], vep, clinvar, gnomad, dbnsfp)
        assert len(merged) == 1
        assert merged[0]["annotation_coverage"] == VEP_BIT | CLINVAR_BIT | GNOMAD_BIT | DBNSFP_BIT
        assert merged[0]["gene_symbol"] == "GENE1"
        assert merged[0]["clinvar_significance"] == "Pathogenic"
        assert merged[0]["gnomad_af_global"] == 0.01
        assert merged[0]["gnomad_source_status"] == GNOMAD_SOURCE_OBSERVED
        assert merged[0]["cadd_phred"] == 25.0

    # Keep this contract independent of the production set so a dropped or
    # misspelled member fails its own derivation case.
    @pytest.mark.parametrize(
        "consequence",
        (
            "3_prime_UTR_variant",
            "5_prime_UTR_variant",
            "downstream_gene_variant",
            "intergenic_variant",
            "intron_variant",
            "mature_miRNA_variant",
            "non_coding_transcript_exon_variant",
            "non_coding_transcript_variant",
            "regulatory_region_variant",
            "TF_binding_site_variant",
            "upstream_gene_variant",
        ),
    )
    def test_noncoding_gnomad_miss_marks_source_uncovered(self, consequence: str) -> None:
        """Every known out-of-scope consequence derives source-uncovered status."""
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text("CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, genotype TEXT)")
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs1799963', '11', 46761055, 'AG')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        vep = {"rs1799963": {"gene_symbol": "F2", "consequence": consequence}}
        merged = _merge_annotations([row], vep, {}, {}, {})

        assert merged[0]["annotation_coverage"] == VEP_BIT
        assert merged[0]["gnomad_source_status"] == GNOMAD_SOURCE_UNCOVERED
        assert "gnomad_af_global" not in merged[0]

    def test_coding_gnomad_miss_leaves_status_unknown(self) -> None:
        """A coding miss without coverage proof is not marked source-uncovered."""
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text("CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, genotype TEXT)")
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs_missense', '1', 100, 'AG')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        vep = {"rs_missense": {"gene_symbol": "GENE1", "consequence": "missense_variant"}}
        merged = _merge_annotations([row], vep, {}, {}, {})

        assert merged[0]["annotation_coverage"] == VEP_BIT
        assert "gnomad_source_status" not in merged[0]

    def test_partial_sources(self) -> None:
        """Variant matched by only VEP has only VEP bit set."""
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text("CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, genotype TEXT)")
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs1', '1', 100, 'AG')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        vep = {"rs1": {"gene_symbol": "GENE1"}}
        merged = _merge_annotations([row], vep, {}, {}, {})
        assert len(merged) == 1
        assert merged[0]["annotation_coverage"] == VEP_BIT

    def test_no_match_gets_coverage_zero(self) -> None:
        """Variants with no source match are emitted with annotation_coverage=0.

        F36: a processed-but-unmatched variant must leave an explicit
        ``coverage=0`` marker, not be dropped (which made it indistinguishable
        from a variant that never entered the pipeline).
        """
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text("CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, genotype TEXT)")
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs_none', '1', 100, 'AA')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        merged = _merge_annotations([row], {}, {}, {}, {})
        assert len(merged) == 1
        assert merged[0]["rsid"] == "rs_none"
        assert merged[0]["annotation_coverage"] == 0

    def test_alphamissense_sets_context_and_coverage_bit(self) -> None:
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text("CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, genotype TEXT)")
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs1', '1', 100, 'AG')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        alpha = {
            "rs1": {
                "alphamissense_pathogenicity": 0.91,
                "alphamissense_class": "likely_pathogenic",
            }
        }
        merged = _merge_annotations([row], {}, {}, {}, {}, alphamissense_data=alpha)

        assert merged[0]["alphamissense_pathogenicity"] == pytest.approx(0.91)
        assert merged[0]["alphamissense_class"] == "likely_pathogenic"
        assert merged[0]["annotation_coverage"] == ALPHAMISSENSE_BIT


# ═══════════════════════════════════════════════════════════════════════
# Bulk upsert
# ═══════════════════════════════════════════════════════════════════════


class TestBulkUpsert:
    def test_upsert_writes_rows(self, sample_engine: sa.Engine) -> None:
        rows = [
            {
                "rsid": "rs1",
                "chrom": "1",
                "pos": 100,
                "genotype": "AG",
                "gene_symbol": "GENE1",
                "annotation_coverage": VEP_BIT,
            }
        ]
        written = _bulk_upsert(sample_engine, rows)
        assert written == 1

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs1")
            ).fetchone()
        assert row is not None
        assert row.gene_symbol == "GENE1"
        assert row.annotation_coverage == VEP_BIT

    def test_upsert_ors_bitmask(self, sample_engine: sa.Engine) -> None:
        """Second upsert ORs the bitmask with existing."""
        # First insert with VEP bit
        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert().values(
                    rsid="rs1",
                    chrom="1",
                    pos=100,
                    genotype="AG",
                    annotation_coverage=VEP_BIT,
                )
            )
        # Upsert with ClinVar bit
        rows = [
            {
                "rsid": "rs1",
                "chrom": "1",
                "pos": 100,
                "genotype": "AG",
                "clinvar_significance": "Pathogenic",
                "annotation_coverage": CLINVAR_BIT,
            }
        ]
        _bulk_upsert(sample_engine, rows)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs1"
                )
            ).fetchone()
        assert row.annotation_coverage == VEP_BIT | CLINVAR_BIT

    def test_empty_rows(self, sample_engine: sa.Engine) -> None:
        assert _bulk_upsert(sample_engine, []) == 0


# ═══════════════════════════════════════════════════════════════════════
# Full orchestration: run_annotation
# ═══════════════════════════════════════════════════════════════════════


class TestRunAnnotation:
    def test_full_annotation(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Full annotation populates annotated_variants with data from all sources."""
        result = run_annotation(sample_with_variants, mock_registry)

        assert result.total_variants == len(SEED_RAW_VARIANTS)
        assert result.rows_written > 0
        assert result.vep_matched > 0
        assert result.clinvar_matched > 0
        assert result.gnomad_matched > 0
        assert result.dbnsfp_matched > 0
        assert result.alphamissense_matched > 0
        assert result.batches_processed >= 1
        assert result.errors == []

    def test_all_fields_populated(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """T2-04: Known variant has VEP + ClinVar + gnomAD + dbNSFP fields."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs429358")
            ).fetchone()

        assert row is not None
        # VEP fields
        assert row.gene_symbol == "APOE"
        assert row.consequence == "missense_variant"
        assert row.mane_select in (True, 1)
        # ClinVar fields
        assert row.clinvar_significance == "risk_factor"
        assert row.clinvar_review_stars == 3
        # gnomAD fields
        assert row.gnomad_af_global is not None
        assert row.gnomad_af_global == pytest.approx(0.1387)
        # dbNSFP fields
        assert row.cadd_phred is not None
        assert row.cadd_phred == pytest.approx(28.3)
        assert row.sift_pred == "D"
        # Bitmask: all 5 sources (APOE is in gene_phenotype seed)
        assert row.annotation_coverage == (
            VEP_BIT | CLINVAR_BIT | GNOMAD_BIT | DBNSFP_BIT | GENE_PHENOTYPE_BIT
        )

    def test_clinvar_coord_fallback_annotates_rsid_mismatch(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Live engine annotates a Pathogenic ClinVar record matched only by coordinate.

        PR-tier twin of the nightly real-bundle ClinVar hit-rate guard
        (``test_annotation_engine_ancestrydna_real_bundle``): that test computes
        its expected set by ClinVar *position*, so the live ``run_annotation``
        path — not just the standalone ``annotate_sample_clinvar`` writer — must
        fall back to (chrom, pos) when the array rsID and ClinVar rsID differ.
        """
        with mock_registry.reference_engine.begin() as conn:
            conn.execute(
                clinvar_variants.insert(),
                [
                    {
                        "rsid": "rs_clinvar_label",  # differs from the array probe
                        "chrom": "2",
                        "pos": 238253397,
                        "ref": "G",
                        "alt": "A",
                        "significance": "Pathogenic",
                        "review_stars": 2,
                        "accession": "VCV_coord",
                        "conditions": "Some disease",
                        "gene_symbol": "GENEX",
                        "variation_id": 9101,
                    }
                ],
            )
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [{"rsid": "rs_array_probe", "chrom": "2", "pos": 238253397, "genotype": "AA"}],
            )

        run_annotation(sample_engine, mock_registry)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs_array_probe")
            ).fetchone()

        assert row is not None
        assert row.clinvar_significance == "Pathogenic"
        assert row.annotation_coverage & CLINVAR_BIT
        # genotype AA vs ref G / alt A → homozygous carrier (non-NULL zygosity)
        assert row.zygosity is not None

    def test_merged_rsid_clinvar_hit_rejected_when_position_differs(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """A merged rsID must not re-key an adjacent ClinVar record onto a sample row."""
        with mock_registry.reference_engine.begin() as conn:
            conn.execute(
                dbsnp_merges.insert(),
                [
                    {
                        "old_rsid": "rs2854121",
                        "current_rsid": "rs1556423844",
                        "build_id": 151,
                    }
                ],
            )
            conn.execute(
                clinvar_variants.insert(),
                [
                    {
                        "rsid": "rs1556423844",
                        "chrom": "MT",
                        "pos": 10663,
                        "ref": "T",
                        "alt": "C",
                        "significance": "Likely pathogenic",
                        "review_stars": 3,
                        "accession": "VCV000009707",
                        "conditions": "Mitochondrial disease|Leber optic atrophy",
                        "gene_symbol": "MT-ND4L",
                        "variation_id": 9707,
                    }
                ],
            )
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [{"rsid": "rs2854121", "chrom": "MT", "pos": 10664, "genotype": "C"}],
            )

        run_annotation(sample_engine, mock_registry)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs2854121")
            ).fetchone()

        assert row is not None
        assert row.dbsnp_rsid_current == "rs1556423844"
        assert row.clinvar_significance is None
        assert not (row.annotation_coverage & CLINVAR_BIT)

    def test_engine_populates_zygosity(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """The engine computes carriage (zygosity) from genotype vs ClinVar ref/alt.

        Locks the live-engine carriage wiring (PR #320): run_annotation writes a
        non-NULL zygosity for matched SNVs so downstream carriage gates can fire.
        """
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            zygs = conn.execute(sa.select(annotated_variants.c.zygosity)).fetchall()

        assert any(z.zygosity is not None for z in zygs), (
            "engine wrote no zygosity — the carriage column is NULL for every variant"
        )

    def test_bitmask_partial_coverage(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Variants matched by fewer sources have partial bitmask.

        Matched variants carry a non-zero bitmask of exactly the sources that
        hit; an unmatched variant (e.g. ``rs_nomatch``) carries the explicit
        ``coverage=0`` marker (F36) rather than being dropped.
        """
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            rows = conn.execute(sa.select(annotated_variants)).fetchall()

        all_source_bits = VEP_BIT | CLINVAR_BIT | GNOMAD_BIT | DBNSFP_BIT | GENE_PHENOTYPE_BIT
        for row in rows:
            coverage = row.annotation_coverage
            assert coverage is not None  # always set (0 for unmatched)
            if row.rsid == "rs_nomatch":
                assert coverage == 0
            else:
                # Matched variants: at least one source bit set.
                assert coverage & all_source_bits > 0

    def test_unmatched_variants_marked_coverage_zero(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Variants matching no source are present with annotation_coverage=0 (F36).

        Previously dropped, which made "processed but unmatched" indistinguishable
        from "never processed" and broke raw↔annotated reconciliation.
        """
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs_nomatch")
            ).fetchone()
        assert row is not None
        assert row.annotation_coverage == 0
        # ...and an unmatched variant has no source-derived data.
        assert row.clinvar_significance is None
        assert row.zygosity is None

    def test_alphamissense_context_attached_without_mutating_core_evidence(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs1801133")
            ).fetchone()

        assert row is not None
        assert row.alphamissense_pathogenicity == pytest.approx(0.91)
        assert row.alphamissense_class == "likely_pathogenic"
        assert (row.annotation_coverage & ALPHAMISSENSE_BIT) == ALPHAMISSENSE_BIT
        assert row.revel is not None
        assert row.clinvar_significance == "drug_response"

    def test_crash_recovery_clears_previous(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Re-running annotation deletes previous results first."""
        result1 = run_annotation(sample_with_variants, mock_registry)
        result2 = run_annotation(sample_with_variants, mock_registry)

        # Same number of rows written
        assert result1.rows_written == result2.rows_written

        # No duplicates
        with sample_with_variants.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count()).select_from(annotated_variants)
            ).scalar()
        assert count == result2.rows_written

    def test_empty_sample(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Empty sample returns zeros."""
        result = run_annotation(sample_engine, mock_registry)
        assert result.total_variants == 0
        assert result.rows_written == 0

    def test_progress_callback(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Progress callback is invoked at least once."""
        calls: list[tuple[int, int]] = []
        run_annotation(
            sample_with_variants,
            mock_registry,
            progress_callback=lambda done, total: calls.append((done, total)),
        )
        assert len(calls) >= 1
        # Final call should indicate all variants processed
        last_done, last_total = calls[-1]
        assert last_done == last_total

    def test_graceful_degradation_missing_vep(
        self,
        sample_with_variants: sa.Engine,
        reference_engine: sa.Engine,
        gnomad_engine: sa.Engine,
        dbnsfp_engine: sa.Engine,
    ) -> None:
        """Annotation proceeds when VEP engine is unavailable."""
        registry = MagicMock()
        registry.reference_engine = reference_engine
        # VEP engine raises an exception when accessed
        type(registry).vep_engine = property(
            lambda self: (_ for _ in ()).throw(FileNotFoundError("no VEP"))
        )
        type(registry).gnomad_engine = property(lambda self: gnomad_engine)
        type(registry).dbnsfp_engine = property(lambda self: dbnsfp_engine)

        result = run_annotation(sample_with_variants, registry)
        assert result.vep_matched == 0
        assert result.clinvar_matched > 0  # ClinVar should still work
        assert result.rows_written > 0

    def test_genotype_preserved(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Raw genotypes are preserved without scoring generic indel tokens."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            rows = conn.execute(
                sa.select(annotated_variants).where(
                    annotated_variants.c.rsid.in_(["rs429358", "rs80357906"])
                )
            ).fetchall()

        by_rsid = {row.rsid: row for row in rows}
        assert by_rsid["rs429358"].genotype == "TC"
        assert by_rsid["rs80357906"].genotype == "DI"
        assert by_rsid["rs80357906"].zygosity is None

    def test_custom_batch_size(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Custom batch size of 3 processes multiple batches."""
        result = run_annotation(sample_with_variants, mock_registry, batch_size=3)
        assert result.batches_processed >= 2
        assert result.rows_written > 0


# ═══════════════════════════════════════════════════════════════════════
# Step 9 / Plan §5.6: AnnotationEngineResult.coverage_stats payload shape
# ═══════════════════════════════════════════════════════════════════════


def _stamp_bundle_version(reference_engine: sa.Engine, version: str) -> None:
    """Insert a `vep_bundle` row into the reference DB's `database_versions`."""
    with reference_engine.begin() as conn:
        conn.execute(database_versions.insert().values(db_name="vep_bundle", version=version))


def _stamp_sample_metadata(sample_engine: sa.Engine, *, file_format: str | None) -> None:
    """Insert the single sample_metadata row with a chosen file_format."""
    with sample_engine.begin() as conn:
        conn.execute(
            sample_metadata_table.insert().values(
                id=1,
                name="fixture-sample",
                file_format=file_format,
            )
        )


class TestCoverageStatsPayload:
    """Plan §5.6: `AnnotationEngineResult.coverage_stats` shape + content."""

    _REQUIRED_TOP_KEYS = {
        "bundle_version",
        "total_variants",
        "vep_bundle_rsid_hits",
        "vep_bundle_coord_fallback_hits",
        "vep_misses",
        "by_source",
    }
    _REQUIRED_PER_SOURCE_KEYS = {
        "vep_bundle_rsid_hits",
        "vep_bundle_coord_fallback_hits",
        "vep_misses",
    }

    def test_payload_shape_23andme(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Unmerged 23andMe sample: single-key by_source under `"23andme"`."""
        _stamp_bundle_version(mock_registry.reference_engine, "v2.0.0")
        _stamp_sample_metadata(sample_with_variants, file_format="23andme_v5")

        result = run_annotation(sample_with_variants, mock_registry)
        stats = result.coverage_stats

        assert set(stats.keys()) == self._REQUIRED_TOP_KEYS
        assert stats["bundle_version"] == "v2.0.0"
        assert stats["total_variants"] == result.total_variants
        assert stats["vep_bundle_rsid_hits"] == result.vep_matched
        assert stats["vep_bundle_coord_fallback_hits"] == 0
        expected_misses = result.total_variants - result.vep_matched
        assert stats["vep_misses"] == expected_misses

        assert list(stats["by_source"].keys()) == ["23andme"]
        per_source = stats["by_source"]["23andme"]
        assert set(per_source.keys()) == self._REQUIRED_PER_SOURCE_KEYS
        assert per_source["vep_bundle_rsid_hits"] == stats["vep_bundle_rsid_hits"]
        assert per_source["vep_bundle_coord_fallback_hits"] == 0
        assert per_source["vep_misses"] == stats["vep_misses"]

    def test_payload_shape_ancestrydna(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Unmerged AncestryDNA sample: single-key by_source under `"ancestrydna"`."""
        _stamp_bundle_version(mock_registry.reference_engine, "v2.0.0")
        _stamp_sample_metadata(sample_with_variants, file_format="ancestrydna_v2.0")

        result = run_annotation(sample_with_variants, mock_registry)
        stats = result.coverage_stats

        assert list(stats["by_source"].keys()) == ["ancestrydna"]
        assert stats["bundle_version"] == "v2.0.0"
        # Rollup sums match the (single) per-source entry.
        per_source = stats["by_source"]["ancestrydna"]
        assert per_source["vep_bundle_rsid_hits"] == stats["vep_bundle_rsid_hits"]
        assert per_source["vep_misses"] == stats["vep_misses"]

    def test_rollup_consistency(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Top-level rollup equals the sum across by_source per Plan §5.6."""
        _stamp_bundle_version(mock_registry.reference_engine, "v2.0.0")
        _stamp_sample_metadata(sample_with_variants, file_format="23andme_v5")

        result = run_annotation(sample_with_variants, mock_registry)
        stats = result.coverage_stats

        rollup_rsid = sum(s["vep_bundle_rsid_hits"] for s in stats["by_source"].values())
        rollup_coord = sum(
            s["vep_bundle_coord_fallback_hits"] for s in stats["by_source"].values()
        )
        rollup_misses = sum(s["vep_misses"] for s in stats["by_source"].values())
        assert rollup_rsid == stats["vep_bundle_rsid_hits"]
        assert rollup_coord == stats["vep_bundle_coord_fallback_hits"]
        assert rollup_misses == stats["vep_misses"]
        assert (
            stats["vep_bundle_rsid_hits"]
            + stats["vep_bundle_coord_fallback_hits"]
            + stats["vep_misses"]
            == stats["total_variants"]
        )

    def test_versionless_bundle_reports_v1_baseline(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """No stamp or embedded version reports the committed v1 baseline."""
        _stamp_sample_metadata(sample_with_variants, file_format="23andme_v5")

        result = run_annotation(sample_with_variants, mock_registry)
        stats = result.coverage_stats

        assert stats["bundle_version"] == "v1.0.0"
        # Payload shape stays intact for the versionless committed fixture.
        assert set(stats.keys()) == self._REQUIRED_TOP_KEYS
        assert list(stats["by_source"].keys()) == ["23andme"]

    def test_unreadable_version_table_leaves_bundle_version_unknown(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """A query failure stays non-fatal without claiming the v1 baseline."""
        database_versions.drop(mock_registry.reference_engine)
        _stamp_sample_metadata(sample_with_variants, file_format="23andme_v5")

        result = run_annotation(sample_with_variants, mock_registry)

        assert result.total_variants > 0
        assert result.coverage_stats["bundle_version"] is None

    def test_absent_bundle_file_does_not_open_vep_engine(
        self,
        tmp_path: Path,
        reference_engine: sa.Engine,
    ) -> None:
        """Telemetry stays unknown without materializing an empty SQLite file."""
        missing_path = tmp_path / "missing-vep.db"
        registry = MagicMock()
        registry.reference_engine = reference_engine
        registry.settings.vep_bundle_db_path = missing_path
        vep_engine_property = PropertyMock(side_effect=AssertionError("VEP engine opened"))
        type(registry).vep_engine = vep_engine_property

        assert _read_bundle_version(registry) is None
        vep_engine_property.assert_not_called()
        assert not missing_path.exists()

    def test_missing_file_format_yields_unknown_vendor(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """No sample_metadata row → vendor key defaults to `"unknown"`."""
        _stamp_bundle_version(mock_registry.reference_engine, "v2.0.0")
        # Intentionally do NOT insert sample_metadata.

        result = run_annotation(sample_with_variants, mock_registry)
        stats = result.coverage_stats

        assert list(stats["by_source"].keys()) == ["unknown"]
        assert stats["bundle_version"] == "v2.0.0"

    def test_empty_sample_leaves_stats_empty(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Empty samples short-circuit before telemetry; coverage_stats stays `{}`."""
        _stamp_bundle_version(mock_registry.reference_engine, "v2.0.0")
        _stamp_sample_metadata(sample_engine, file_format="23andme_v5")

        result = run_annotation(sample_engine, mock_registry)
        assert result.total_variants == 0
        assert result.coverage_stats == {}


class TestCoverageStatsSideEffects:
    """Phase 0 closure (Step 18) / Plan §5.6 + §16.6 negative-side assertions.

    `run_annotation` returns coverage telemetry on the result dataclass but
    must NOT side-effect any reference- or per-sample-DB state. Provenance
    is written by `huey_tasks.run_annotation_task` only after
    `run_all_analyses` returns (Plan §5.6, §7.3, §7.4). These assertions lock
    the contract so a future refactor can't quietly push provenance writes
    back into the engine and re-introduce the half-fresh-gate failure mode.
    """

    def test_update_history_row_count_unchanged(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """`run_annotation` never writes to `update_history`."""
        _stamp_bundle_version(mock_registry.reference_engine, "v2.0.0")
        _stamp_sample_metadata(sample_with_variants, file_format="23andme_v5")

        ref_engine = mock_registry.reference_engine
        with ref_engine.connect() as conn:
            before = conn.execute(
                sa.select(sa.func.count()).select_from(update_history)
            ).scalar_one()

        result = run_annotation(sample_with_variants, mock_registry)
        assert result.total_variants > 0

        with ref_engine.connect() as conn:
            after = conn.execute(
                sa.select(sa.func.count()).select_from(update_history)
            ).scalar_one()
        assert after == before == 0

    def test_database_versions_vep_bundle_row_unchanged(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """`run_annotation` never mutates the `vep_bundle` row in `database_versions`."""
        _stamp_bundle_version(mock_registry.reference_engine, "v2.0.0")
        _stamp_sample_metadata(sample_with_variants, file_format="23andme_v5")

        ref_engine = mock_registry.reference_engine
        with ref_engine.connect() as conn:
            before_rows = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "vep_bundle")
            ).fetchall()

        result = run_annotation(sample_with_variants, mock_registry)
        assert result.coverage_stats["bundle_version"] == "v2.0.0"

        with ref_engine.connect() as conn:
            after_rows = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "vep_bundle")
            ).fetchall()

        # Same row count, same version string, same downloaded_at timestamp —
        # the engine read but did not write.
        assert len(after_rows) == len(before_rows) == 1
        assert before_rows[0].version == after_rows[0].version == "v2.0.0"
        assert before_rows[0].downloaded_at == after_rows[0].downloaded_at

    def test_embedded_bundle_version_populates_unstamped_telemetry(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """A status-copied, self-described bundle reports its embedded version."""
        seed_embedded_vep_bundle_version(
            mock_registry.settings.vep_bundle_db_path,
            "v3.0.0",
        )
        _stamp_sample_metadata(sample_with_variants, file_format="23andme_v5")

        result = run_annotation(sample_with_variants, mock_registry)

        assert result.coverage_stats["bundle_version"] == "v3.0.0"
        with mock_registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions.c.version).where(
                    database_versions.c.db_name == "vep_bundle"
                )
            ).fetchone()
        assert row is None

    def test_annotation_state_untouched_by_engine(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Per-sample `annotation_state` has zero rows touched by `run_annotation` alone.

        The Huey-task wrapper is responsible for upserting provenance after
        analysis returns; the engine itself must leave the table empty.
        """
        _stamp_bundle_version(mock_registry.reference_engine, "v2.0.0")
        _stamp_sample_metadata(sample_with_variants, file_format="23andme_v5")

        with sample_with_variants.connect() as conn:
            before = conn.execute(
                sa.select(sa.func.count()).select_from(annotation_state)
            ).scalar_one()
        assert before == 0

        result = run_annotation(sample_with_variants, mock_registry)
        assert result.coverage_stats != {}

        with sample_with_variants.connect() as conn:
            after_rows = conn.execute(sa.select(annotation_state)).fetchall()
        assert after_rows == []


# ═══════════════════════════════════════════════════════════════════════
# T2-04: Integration test - 1000 variants end-to-end
# ═══════════════════════════════════════════════════════════════════════


class TestIntegration1000Variants:
    """T2-04: Annotation engine processes 1000 variants end-to-end."""

    def test_1000_variants_all_fields(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Generate 1000 variants, annotate, verify fields populated."""
        # Insert 1000 raw variants (mix of known + synthetic)
        known = SEED_RAW_VARIANTS[:5]
        synthetic = [
            {"rsid": f"rs_synth_{i}", "chrom": "1", "pos": 200000 + i, "genotype": "AG"}
            for i in range(1000 - len(known))
        ]
        all_variants = known + synthetic

        with sample_engine.begin() as conn:
            conn.execute(raw_variants.insert(), all_variants)

        result = run_annotation(sample_engine, mock_registry)

        assert result.total_variants == 1000
        assert result.rows_written > 0
        # Known variants should have annotations
        assert result.vep_matched >= 3  # at least some known rsids match

        # Verify a known variant has all fields
        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs429358")
            ).fetchone()

        assert row is not None
        assert row.gene_symbol == "APOE"
        assert row.clinvar_significance is not None
        assert row.gnomad_af_global is not None
        assert row.cadd_phred is not None
        assert row.annotation_coverage == (
            VEP_BIT | CLINVAR_BIT | GNOMAD_BIT | DBNSFP_BIT | GENE_PHENOTYPE_BIT
        )


# ═══════════════════════════════════════════════════════════════════════
# P2-09: gnomAD annotation lookup integration
# ═══════════════════════════════════════════════════════════════════════


class TestGnomadAnnotationLookupIntegration:
    """P2-09: gnomAD annotation lookup integrated into annotation engine.

    Verifies:
    - rsid-based primary lookup delegates to gnomad.py
    - Position-based fallback for unmatched rsids with ref/alt
    - Correct rare/ultra-rare thresholds (0.01 / 0.001)
    - All population AF fields returned (AFR/AMR/EAS/EUR/SAS)
    - Homozygous count returned
    - _annot_to_dict conversion preserves all fields
    """

    def test_annot_to_dict_preserves_fields(self) -> None:
        """_annot_to_dict converts GnomADAnnotation to engine dict."""
        annot = GnomADAnnotation(
            rsid="rs7412",
            af_global=0.0781,
            af_afr=0.1130,
            af_amr=0.0560,
            af_asj=0.0269,
            af_eas=0.0980,
            af_eur=0.0730,
            af_fin=0.0410,
            af_sas=0.0650,
            homozygous_count=874,
            rare_flag=False,
            ultra_rare_flag=False,
            an_global=120000,
            an_afr=18000,
            an_amr=16000,
            an_asj=10000,
            an_eas=14000,
            an_eur=60000,
            an_fin=12000,
            an_sas=15000,
            an_popmax=18000,
        )
        d = _annot_to_dict(annot)

        assert d["gnomad_af_global"] == pytest.approx(0.0781)
        assert d["gnomad_af_afr"] == pytest.approx(0.1130)
        assert d["gnomad_af_amr"] == pytest.approx(0.0560)
        assert d["gnomad_af_asj"] == pytest.approx(0.0269)
        assert d["gnomad_af_eas"] == pytest.approx(0.0980)
        assert d["gnomad_af_eur"] == pytest.approx(0.0730)
        assert d["gnomad_af_fin"] == pytest.approx(0.0410)
        assert d["gnomad_af_sas"] == pytest.approx(0.0650)
        assert d["gnomad_an_global"] == 120000
        assert d["gnomad_an_afr"] == 18000
        assert d["gnomad_an_popmax"] == 18000
        assert d["gnomad_source_status"] == GNOMAD_SOURCE_OBSERVED
        assert d["gnomad_homozygous_count"] == 874
        assert d["rare_flag"] is False
        assert d["ultra_rare_flag"] is False

    def test_rsid_lookup_returns_all_population_afs(self, gnomad_engine: sa.Engine) -> None:
        """P2-09: Lookup returns global AF and per-population AF."""
        result = _lookup_gnomad(["rs7412"], {}, gnomad_engine)

        assert "rs7412" in result
        data = result["rs7412"]
        assert data["gnomad_af_global"] == pytest.approx(0.0781)
        assert data["gnomad_af_afr"] == pytest.approx(0.1130)
        assert data["gnomad_af_amr"] == pytest.approx(0.0560)
        # ASJ is a real gnomAD v4.1 value, distinct from af_global (#1964): an
        # engine mis-wiring of af_asj -> af_global would fail here.
        assert data["gnomad_af_asj"] == pytest.approx(0.0741546)
        assert data["gnomad_af_asj"] != pytest.approx(data["gnomad_af_global"])
        assert data["gnomad_af_eas"] == pytest.approx(0.0980)
        assert data["gnomad_af_eur"] == pytest.approx(0.0730)
        assert data["gnomad_af_fin"] == pytest.approx(0.0410)
        assert data["gnomad_af_sas"] == pytest.approx(0.0650)

    def test_rsid_lookup_returns_homozygous_count(self, gnomad_engine: sa.Engine) -> None:
        """P2-09: Lookup returns homozygous count."""
        result = _lookup_gnomad(["rs7412"], {}, gnomad_engine)
        assert result["rs7412"]["gnomad_homozygous_count"] == 874

    def test_rare_threshold_correct(self, gnomad_engine: sa.Engine) -> None:
        """P2-09 / F15: rare_flag uses the 0.01 threshold on popmax (not global)."""
        # rs5030862: popmax (afr)=0.006 — rare in every population.
        result = _lookup_gnomad(["rs5030862"], {}, gnomad_engine)
        assert result["rs5030862"]["rare_flag"] is True
        assert result["rs5030862"]["ultra_rare_flag"] is False
        assert result["rs5030862"]["gnomad_af_popmax"] == pytest.approx(0.006)

    def test_ancestry_common_not_rare(self, gnomad_engine: sa.Engine) -> None:
        """F15: rare globally (0.0052) but common in AFR (0.018) → not rare by popmax."""
        result = _lookup_gnomad(["rs28897696"], {}, gnomad_engine)
        assert result["rs28897696"]["gnomad_af_popmax"] == pytest.approx(0.018)
        assert result["rs28897696"]["rare_flag"] is False
        assert result["rs28897696"]["ultra_rare_flag"] is False

    def test_ultra_rare_threshold_correct(self, gnomad_engine: sa.Engine) -> None:
        """P2-09: ultra_rare_flag uses 0.001 threshold (bug fix from 0.0001)."""
        # rs63750066 is ultra-rare in every population (popmax < 0.001). rs80357906
        # is no longer a valid ultra-rare example: its real ASJ founder AF (0.00118)
        # correctly makes it rare-not-ultra (#1964).
        result = _lookup_gnomad(["rs63750066"], {}, gnomad_engine)
        assert result["rs63750066"]["rare_flag"] is True
        assert result["rs63750066"]["ultra_rare_flag"] is True

        # Verify threshold constants match PRD
        assert RARE_AF_THRESHOLD == 0.01
        assert ULTRA_RARE_AF_THRESHOLD == 0.001

    def test_ultra_rare_boundary(self, gnomad_engine: sa.Engine) -> None:
        """AF exactly at 0.001 is NOT ultra-rare (strict less-than)."""
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, "
                    "af_asj, af_eas, af_eur, af_fin, af_sas, homozygous_count) "
                    "VALUES "
                    "('rs_boundary', '1', 999, 'A', 'G', 0.001, "
                    "0.001, 0.001, 0.001, 0.001, 0.001, 0.001, 0.001, 2)"
                )
            )
        result = _lookup_gnomad(["rs_boundary"], {}, gnomad_engine)
        assert result["rs_boundary"]["rare_flag"] is True
        assert result["rs_boundary"]["ultra_rare_flag"] is False

    def test_position_fallback_with_ref_alt(self, gnomad_engine: sa.Engine) -> None:
        """Position-based fallback matches when rsid differs but coords match."""
        # Insert variant under different rsid but same position
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, "
                    "af_asj, af_eas, af_eur, af_fin, af_sas, homozygous_count) "
                    "VALUES "
                    "('rs_gnomad_id', '5', 500, 'C', 'T', 0.02, "
                    "0.03, 0.01, 0.022, 0.02, 0.025, 0.015, 0.018, 30)"
                )
            )

        # Create a fake raw row with ref/alt for position fallback
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, "
                    "genotype TEXT, ref TEXT, alt TEXT)"
                )
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs_user_id', '5', 500, 'CT', 'C', 'T')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        raw_by_rsid = {"rs_user_id": row}
        result = _lookup_gnomad(["rs_user_id"], raw_by_rsid, gnomad_engine)

        assert "rs_user_id" in result
        assert result["rs_user_id"]["gnomad_af_global"] == pytest.approx(0.02)

    def test_position_fallback_with_null_reference_rsid(self, gnomad_engine: sa.Engine) -> None:
        """Exact coordinate lookup recovers gnomAD rows that have no dbSNP rsID."""
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_asj, "
                    "af_eas, af_eur, af_fin, af_sas, homozygous_count) VALUES "
                    "(NULL, '1', 10108, 'C', "
                    "'CAACCCTAACCCTAACCCTAACCCTAACCCTAACCCTAACCCT', "
                    "0.00019821605550049553, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0)"
                )
            )

        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, "
                    "genotype TEXT, ref TEXT, alt TEXT)"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO t VALUES ("
                    "'sample_no_rsid', '1', 10108, 'ID', 'C', "
                    "'CAACCCTAACCCTAACCCTAACCCTAACCCTAACCCTAACCCT')"
                )
            )
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        result = _lookup_gnomad(["sample_no_rsid"], {"sample_no_rsid": row}, gnomad_engine)

        assert result["sample_no_rsid"]["gnomad_af_global"] == pytest.approx(
            0.00019821605550049553
        )

    def test_position_lookup_beats_ambiguous_shared_rsid(self, gnomad_engine: sa.Engine) -> None:
        """Exact allele coordinates select the carried ALT at shared-rsID sites."""
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_asj, "
                    "af_eas, af_eur, af_fin, af_sas, homozygous_count) VALUES "
                    "('rs_shared', '1', 300, 'G', 'A', 0.001, 0.001, 0.001, 0.001, "
                    "0.001, 0.001, 0.001, 0.001, 1), "
                    "('rs_shared', '1', 300, 'G', 'T', 0.20, 0.05, 0.04, 0.03, "
                    "0.02, 0.01, 0.07, 0.08, 5)"
                )
            )

        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, "
                    "genotype TEXT, ref TEXT, alt TEXT)"
                )
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs_shared', '1', 300, 'GA', 'G', 'A')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        result = _lookup_gnomad(["rs_shared"], {"rs_shared": row}, gnomad_engine)

        assert result["rs_shared"]["gnomad_af_global"] == pytest.approx(0.001)
        assert result["rs_shared"]["gnomad_homozygous_count"] == 1

    def test_exact_coord_miss_does_not_fallback_to_shared_rsid(
        self, gnomad_engine: sa.Engine
    ) -> None:
        """A carried ALT absent by exact coordinates must not inherit another ALT's AF."""
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_asj, "
                    "af_eas, af_eur, af_fin, af_sas, homozygous_count) VALUES "
                    "('rs_shared_miss', '1', 301, 'G', 'T', 0.20, 0.05, 0.04, 0.03, "
                    "0.02, 0.01, 0.07, 0.08, 5)"
                )
            )

        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, "
                    "genotype TEXT, ref TEXT, alt TEXT)"
                )
            )
            conn.execute(
                sa.text("INSERT INTO t VALUES ('rs_shared_miss', '1', 301, 'GA', 'G', 'A')")
            )
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        result = _lookup_gnomad(["rs_shared_miss"], {"rs_shared_miss": row}, gnomad_engine)

        assert "rs_shared_miss" not in result

    # ── production-shaped: raw_variants has no ref/alt (#2171) ──────────
    #
    # The two tests above synthesise a raw row carrying `ref`/`alt`, which the
    # real `raw_variants` table does not have (backend/db/tables.py), so they
    # exercise the exact-coordinate branch that `run_annotation()` can never
    # reach. Everything below goes through `run_annotation()` on the real
    # schema, where the rsID fallback is the only path.

    @staticmethod
    def _shared_rsid_gnomad_rows(gnomad_engine: sa.Engine) -> None:
        """One rsID, two ALTs: a rare G>A and a common G>T."""
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_asj, "
                    "af_eas, af_eur, af_fin, af_sas, homozygous_count) VALUES "
                    "('rs_shared_prod', '1', 300, 'G', 'A', 0.001, 0.001, 0.001, 0.001, "
                    "0.001, 0.001, 0.001, 0.001, 1), "
                    "('rs_shared_prod', '1', 300, 'G', 'T', 0.20, 0.05, 0.04, 0.03, "
                    "0.02, 0.01, 0.07, 0.08, 500)"
                )
            )

    @staticmethod
    def _annotated_row(sample_engine: sa.Engine, rsid: str) -> sa.Row | None:
        with sample_engine.connect() as conn:
            return conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == rsid)
            ).fetchone()

    def test_raw_variants_carries_no_allele_identity(self, sample_engine: sa.Engine) -> None:
        """Premise guard: the production schema has no ref/alt to match on.

        If this ever fails the exact-coordinate branch became reachable and the
        rsID-fallback tests below stop testing the production path (#2171).
        """
        columns = {c.name for c in raw_variants.columns}
        assert "ref" not in columns
        assert "alt" not in columns

    def test_shared_rsid_takes_the_carried_alt_frequency(
        self, sample_engine: sa.Engine, mock_registry: MagicMock, gnomad_engine: sa.Engine
    ) -> None:
        """`AG` carries G>A, so it must not inherit the common G>T frequency.

        Pre-fix the rsID fallback ranked candidates by population-max AF, so the
        rare carried allele was stored with the common allele's AF (0.20) and
        homozygote count (500) and dropped out of rare-variant results (#2171).
        """
        self._shared_rsid_gnomad_rows(gnomad_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert().values(
                    rsid="rs_shared_prod", chrom="1", pos=300, genotype="AG"
                )
            )

        run_annotation(sample_engine, mock_registry)

        row = self._annotated_row(sample_engine, "rs_shared_prod")
        assert row is not None
        assert row.gnomad_af_global == pytest.approx(0.001)
        assert row.gnomad_homozygous_count == 1

    def test_shared_rsid_with_no_carried_alt_withholds_frequency(
        self, sample_engine: sa.Engine, mock_registry: MagicMock, gnomad_engine: sa.Engine
    ) -> None:
        """A genotype matching neither catalogued ALT gets no frequency at all.

        Borrowing the most common ALT's numbers would present another variant's
        evidence as this one's; absent is the honest state (#2171).
        """
        self._shared_rsid_gnomad_rows(gnomad_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert().values(
                    rsid="rs_shared_prod", chrom="1", pos=300, genotype="CC"
                )
            )

        run_annotation(sample_engine, mock_registry)

        row = self._annotated_row(sample_engine, "rs_shared_prod")
        assert row is not None
        assert row.gnomad_af_global is None
        assert row.gnomad_homozygous_count is None

    def test_two_alt_compound_het_withholds_frequency(
        self, sample_engine: sa.Engine, mock_registry: MagicMock, gnomad_engine: sa.Engine
    ) -> None:
        """`AT` at a G>A / G>T site matches neither row, so nothing is published.

        `classify_zygosity` returns None for a genotype carrying two ALTs and no
        REF, so this lands on the zero-carried branch rather than the
        multiple-carried one; `test_ref_alt_swapped_rows_withhold_frequency`
        covers that separately.
        """
        self._shared_rsid_gnomad_rows(gnomad_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert().values(
                    rsid="rs_shared_prod", chrom="1", pos=300, genotype="AT"
                )
            )

        run_annotation(sample_engine, mock_registry)

        row = self._annotated_row(sample_engine, "rs_shared_prod")
        assert row is not None
        assert row.gnomad_af_global is None

    def test_ref_alt_swapped_rows_withhold_frequency(
        self, sample_engine: sa.Engine, mock_registry: MagicMock, gnomad_engine: sa.Engine
    ) -> None:
        """More than one candidate row is genuinely carried, so neither may be published.

        An rsID whose rows are REF/ALT mirror images (G>A and A>G) makes `AG` a
        heterozygote under *both*, with different frequencies. Picking either
        would be a coin flip presented as a measurement.
        """
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_asj, "
                    "af_eas, af_eur, af_fin, af_sas, homozygous_count) VALUES "
                    "('rs_swapped', '1', 500, 'G', 'A', 0.003, 0.003, 0.003, 0.003, "
                    "0.003, 0.003, 0.003, 0.003, 2), "
                    "('rs_swapped', '1', 500, 'A', 'G', 0.30, 0.30, 0.30, 0.30, "
                    "0.30, 0.30, 0.30, 0.30, 900)"
                )
            )
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert().values(rsid="rs_swapped", chrom="1", pos=500, genotype="AG")
            )

        run_annotation(sample_engine, mock_registry)

        row = self._annotated_row(sample_engine, "rs_swapped")
        assert row is not None
        assert row.gnomad_af_global is None
        assert row.gnomad_homozygous_count is None

    def test_carried_rare_alt_reaches_the_rare_variant_finder(
        self, sample_engine: sa.Engine, mock_registry: MagicMock, gnomad_engine: sa.Engine
    ) -> None:
        """The consumer #2171 names: a rare carried ALT must not be filtered out.

        `find_rare_variants` judges rarity on the stored population-max AF, so
        inheriting the common ALT's 0.20 pushed the carried 0.001 allele above
        every sane threshold and silently dropped it from the report.
        """
        from backend.analysis.rare_variant_finder import RareVariantFilter, find_rare_variants

        self._shared_rsid_gnomad_rows(gnomad_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert().values(
                    rsid="rs_shared_prod", chrom="1", pos=300, genotype="AG"
                )
            )

        run_annotation(sample_engine, mock_registry)
        found = find_rare_variants(
            RareVariantFilter(af_threshold=0.01, include_novel=False), sample_engine
        )

        assert "rs_shared_prod" in {v.rsid for v in found.variants}

    def test_withheld_rsid_is_not_reported_as_absent_from_gnomad(
        self, sample_engine: sa.Engine, mock_registry: MagicMock, gnomad_engine: sa.Engine
    ) -> None:
        """#2171 review: withholding must not read as "Not in gnomAD".

        gnomAD lists `rs_shared_prod` -- twice. Withholding the frequency because
        the carried ALT is unresolvable is correct, but leaving no status makes
        the row indistinguishable from an rsID gnomAD has never heard of, and
        `gnomadNoFrequencyLabel` then tells the user "Not in gnomAD" about a
        variant that is in gnomAD.
        """
        self._shared_rsid_gnomad_rows(gnomad_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert().values(
                    rsid="rs_shared_prod", chrom="1", pos=300, genotype="CC"
                )
            )

        run_annotation(sample_engine, mock_registry)

        row = self._annotated_row(sample_engine, "rs_shared_prod")
        assert row is not None
        assert row.gnomad_af_global is None
        assert row.gnomad_source_status == "allele_ambiguous"

    def test_absent_rsid_keeps_no_ambiguity_status(
        self, sample_engine: sa.Engine, mock_registry: MagicMock, gnomad_engine: sa.Engine
    ) -> None:
        """Discriminating control: a genuinely absent rsID must NOT be relabelled.

        Without this, always emitting the ambiguity status would satisfy the test
        above while making the new label meaningless.
        """
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert().values(
                    rsid="rs_absent_prod", chrom="1", pos=999, genotype="AG"
                )
            )

        run_annotation(sample_engine, mock_registry)

        row = self._annotated_row(sample_engine, "rs_absent_prod")
        assert row is not None
        assert row.gnomad_af_global is None
        assert row.gnomad_source_status != "allele_ambiguous"

    def test_ambiguity_status_survives_a_merged_rsid(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
        gnomad_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """#2214 review: the ambiguity set must be re-keyed like every other source.

        Source lookups are issued under the *current* rsid (F18), so the set
        comes back keyed by the current id while `_merge_annotations` looks the
        sample's *original* id up. `lookup_key` maps original -> current, so it
        has to be walked in that direction -- a `.get(current)` lookup misses
        silently and drops the status for every deprecated rsid, which is
        indistinguishable from "absent from gnomAD" downstream.
        """
        with reference_engine.begin() as conn:
            conn.execute(
                dbsnp_merges.insert().values(
                    old_rsid="rs_old_prod", current_rsid="rs_shared_prod", build_id=155
                )
            )
        self._shared_rsid_gnomad_rows(gnomad_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert().values(rsid="rs_old_prod", chrom="1", pos=300, genotype="CC")
            )

        run_annotation(sample_engine, mock_registry)

        row = self._annotated_row(sample_engine, "rs_old_prod")
        assert row is not None
        assert row.gnomad_af_global is None
        assert row.gnomad_source_status == "allele_ambiguous"

    def test_aliased_rsids_with_different_genotypes_do_not_share_a_frequency(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
        gnomad_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """#2214 review: alias collapse must not hand one call's ALT to another.

        `raw_by_query` keeps ONE sample row per queried rsID, so a deprecated
        rsID and its current replacement (with different genotypes) share a
        single genotype for ALT selection, and `_rekey_to_original` then fans the
        chosen frequency back to both. That is #2171's own defect reached by a
        different route: one allele's frequency assigned to another allele.
        """
        with reference_engine.begin() as conn:
            conn.execute(
                dbsnp_merges.insert().values(
                    old_rsid="rs_alias_old", current_rsid="rs_shared_prod", build_id=155
                )
            )
        self._shared_rsid_gnomad_rows(gnomad_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [
                    # Carries G>A (AF 0.001)
                    {"rsid": "rs_alias_old", "chrom": "1", "pos": 300, "genotype": "AG"},
                    # Carries G>T (AF 0.20) -- self-mapped, so it wins raw_by_query
                    {"rsid": "rs_shared_prod", "chrom": "1", "pos": 300, "genotype": "TT"},
                ],
            )

        run_annotation(sample_engine, mock_registry)

        old_row = self._annotated_row(sample_engine, "rs_alias_old")
        assert old_row is not None
        # It must NOT inherit the other call's 0.20. Either its own 0.001, or
        # withheld -- what it may not do is report a frequency it does not have.
        assert old_row.gnomad_af_global != pytest.approx(0.20)
        # Discriminating control for the status split: here the candidates are
        # G>A and G>T at ONE position, so the allele genuinely is ambiguous.
        assert old_row.gnomad_source_status == "allele_ambiguous"

    def test_aliased_rsids_agreeing_on_genotype_still_resolve(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
        gnomad_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Discriminating control for the alias guard.

        Withholding whenever a query id serves several rows would suppress the
        common, harmless case where the aliases simply agree. Both calls carry
        G>A here, so the pick is well-defined and the frequency must still land.
        """
        with reference_engine.begin() as conn:
            conn.execute(
                dbsnp_merges.insert().values(
                    old_rsid="rs_agree_old", current_rsid="rs_shared_prod", build_id=155
                )
            )
        self._shared_rsid_gnomad_rows(gnomad_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [
                    {"rsid": "rs_agree_old", "chrom": "1", "pos": 300, "genotype": "AG"},
                    {"rsid": "rs_shared_prod", "chrom": "1", "pos": 300, "genotype": "AG"},
                ],
            )

        run_annotation(sample_engine, mock_registry)

        for rsid in ("rs_agree_old", "rs_shared_prod"):
            row = self._annotated_row(sample_engine, rsid)
            assert row is not None, rsid
            assert row.gnomad_af_global == pytest.approx(0.001), rsid

    def test_alias_conflict_is_independent_of_batch_boundaries(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
        gnomad_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """#2214 review: the conflict map must span the sample, not one batch.

        Computed per batch, two aliases landing in *different* batches each see a
        single genotype and publish allele-specific frequencies, while the same
        pair in one batch is withheld. Stored AF and status would then depend on
        `batch_size` and row order. `batch_size=1` forces the split.
        """
        with reference_engine.begin() as conn:
            conn.execute(
                dbsnp_merges.insert().values(
                    old_rsid="rs_batch_old", current_rsid="rs_shared_prod", build_id=155
                )
            )
        self._shared_rsid_gnomad_rows(gnomad_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [
                    {"rsid": "rs_batch_old", "chrom": "1", "pos": 300, "genotype": "AG"},
                    {"rsid": "rs_shared_prod", "chrom": "1", "pos": 300, "genotype": "TT"},
                ],
            )

        run_annotation(sample_engine, mock_registry, batch_size=1)

        for rsid in ("rs_batch_old", "rs_shared_prod"):
            row = self._annotated_row(sample_engine, rsid)
            assert row is not None, rsid
            # Same outcome as the single-batch case: neither may borrow the
            # other's allele just because batching separated them.
            assert row.gnomad_af_global is None, rsid
            assert row.gnomad_source_status == "allele_ambiguous", rsid

    def test_multi_locus_rsid_uses_the_sample_locus(
        self, sample_engine: sa.Engine, mock_registry: MagicMock, gnomad_engine: sa.Engine
    ) -> None:
        """#2214 review: an rsID catalogued at two coordinates must not cross over.

        Picking by genotype alone lets a G>A row at position 900 supply the
        frequency for the sample's call at position 300 -- #2171's defect across
        loci rather than across ALTs. Both rows here are G>A, so genotype cannot
        discriminate; only the coordinate can.
        """
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_asj, "
                    "af_eas, af_eur, af_fin, af_sas, homozygous_count) VALUES "
                    "('rs_multiloc', '1', 300, 'G', 'A', 0.004, 0.004, 0.004, 0.004, "
                    "0.004, 0.004, 0.004, 0.004, 3), "
                    "('rs_multiloc', '1', 900, 'G', 'A', 0.40, 0.40, 0.40, 0.40, "
                    "0.40, 0.40, 0.40, 0.40, 800)"
                )
            )
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert().values(rsid="rs_multiloc", chrom="1", pos=300, genotype="AG")
            )

        run_annotation(sample_engine, mock_registry)

        row = self._annotated_row(sample_engine, "rs_multiloc")
        assert row is not None
        assert row.gnomad_af_global == pytest.approx(0.004)
        assert row.gnomad_homozygous_count == 3

    def test_single_locus_rsid_ignores_a_position_mismatch(
        self, sample_engine: sa.Engine, mock_registry: MagicMock, gnomad_engine: sa.Engine
    ) -> None:
        """Discriminating control: coordinate agreement is NOT newly required.

        rsID-only lookup has never demanded that the array's position match
        gnomAD's. Filtering unconditionally would silently drop frequencies
        wherever the two disagree, so the locus filter applies only when
        candidates actually span several coordinates.
        """
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_asj, "
                    "af_eas, af_eur, af_fin, af_sas, homozygous_count) VALUES "
                    "('rs_offset', '1', 700, 'G', 'A', 0.05, 0.05, 0.05, 0.05, "
                    "0.05, 0.05, 0.05, 0.05, 9)"
                )
            )
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert().values(rsid="rs_offset", chrom="1", pos=701, genotype="AG")
            )

        run_annotation(sample_engine, mock_registry)

        row = self._annotated_row(sample_engine, "rs_offset")
        assert row is not None
        assert row.gnomad_af_global == pytest.approx(0.05)

    def test_aliases_at_different_loci_conflict_even_with_equal_genotypes(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
        gnomad_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """#2214 review: the conflict key needs the locus, not just the genotype.

        Two aliases can agree on `AG` while sitting at different GRCh37 loci.
        `raw_by_query` keeps one of them, so the surviving locus's frequency is
        fanned to both -- and which one survives depends on `batch_size`. Keying
        the conflict map on (genotype, chrom, pos) catches it.
        """
        with reference_engine.begin() as conn:
            conn.execute(
                dbsnp_merges.insert().values(
                    old_rsid="rs_loci_old", current_rsid="rs_loci_cur", build_id=155
                )
            )
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_asj, "
                    "af_eas, af_eur, af_fin, af_sas, homozygous_count) VALUES "
                    "('rs_loci_cur', '1', 300, 'G', 'A', 0.004, 0.004, 0.004, 0.004, "
                    "0.004, 0.004, 0.004, 0.004, 3), "
                    "('rs_loci_cur', '1', 900, 'G', 'A', 0.40, 0.40, 0.40, 0.40, "
                    "0.40, 0.40, 0.40, 0.40, 800)"
                )
            )
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [
                    # Same genotype, different loci -- indistinguishable without
                    # the coordinate in the conflict key.
                    {"rsid": "rs_loci_old", "chrom": "1", "pos": 300, "genotype": "AG"},
                    {"rsid": "rs_loci_cur", "chrom": "1", "pos": 900, "genotype": "AG"},
                ],
            )

        run_annotation(sample_engine, mock_registry)

        old_row = self._annotated_row(sample_engine, "rs_loci_old")
        assert old_row is not None
        # It sits at pos 300; it must not inherit pos 900's 0.40.
        assert old_row.gnomad_af_global != pytest.approx(0.40)
        # gnomAD lists a row at BOTH of the sample's positions, so neither the
        # alleles nor the coordinates are at fault: the limit is that one
        # per-rsID result cannot carry a frequency for two calls. Saying
        # "listed only at other positions" would be false here (#2214 review).
        assert old_row.gnomad_source_status == "alias_unresolved"

    def test_no_call_alias_does_not_suppress_a_valid_call(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
        gnomad_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """#2214 review: over-suppression is a failure too.

        A deprecated rsID with a `--` no-call mapping onto the same multi-ALT
        query as a validly typed current rsID used to register as a conflicting
        genotype, so the guard withheld the frequency from the *valid* call --
        and with `include_novel=False` that can drop it from rare-variant
        results. A no-call carries no allele information and cannot disagree.
        """
        with reference_engine.begin() as conn:
            conn.execute(
                dbsnp_merges.insert().values(
                    old_rsid="rs_nocall_old", current_rsid="rs_shared_prod", build_id=155
                )
            )
        self._shared_rsid_gnomad_rows(gnomad_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [
                    {"rsid": "rs_nocall_old", "chrom": "1", "pos": 300, "genotype": "--"},
                    {"rsid": "rs_shared_prod", "chrom": "1", "pos": 300, "genotype": "AG"},
                ],
            )

        run_annotation(sample_engine, mock_registry)

        row = self._annotated_row(sample_engine, "rs_shared_prod")
        assert row is not None
        # The typed call carries G>A and must keep its own frequency.
        assert row.gnomad_af_global == pytest.approx(0.001)
        assert row.gnomad_homozygous_count == 1

    def test_typed_alias_beats_a_no_call_current_id(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
        gnomad_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """#2214 review: `raw_by_query`'s tie-break must not pick the no-call.

        A typed deprecated rsID with a no-call *current* replacement leaves no
        conflict, but the self-map tie-break keeps the current (no-call) row, so
        selection ran on `--` and withheld from the typed alias too -- and with
        `batch_size=1` it resolved normally instead. gnomAD now selects on the
        sample's single typed call for that query id.
        """
        with reference_engine.begin() as conn:
            conn.execute(
                dbsnp_merges.insert().values(
                    old_rsid="rs_typed_old", current_rsid="rs_shared_prod", build_id=155
                )
            )
        self._shared_rsid_gnomad_rows(gnomad_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [
                    {"rsid": "rs_typed_old", "chrom": "1", "pos": 300, "genotype": "AG"},
                    {"rsid": "rs_shared_prod", "chrom": "1", "pos": 300, "genotype": "--"},
                ],
            )

        run_annotation(sample_engine, mock_registry)

        row = self._annotated_row(sample_engine, "rs_typed_old")
        assert row is not None
        assert row.gnomad_af_global == pytest.approx(0.001)

    def test_locus_unresolved_is_not_reported_as_allele_ambiguous(
        self, sample_engine: sa.Engine, mock_registry: MagicMock, gnomad_engine: sa.Engine
    ) -> None:
        """#2214 review: a coordinate mismatch is not an allele ambiguity.

        Both rows here are G>A, so nothing about the *allele* is ambiguous; the
        sample's position simply matches neither. Reporting `allele_ambiguous`
        would render "several alternate alleles ... which one you carry", which
        is false, and would hide a build/mapping mismatch.
        """
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_asj, "
                    "af_eas, af_eur, af_fin, af_sas, homozygous_count) VALUES "
                    "('rs_elsewhere', '1', 500, 'G', 'A', 0.01, 0.01, 0.01, 0.01, "
                    "0.01, 0.01, 0.01, 0.01, 2), "
                    "('rs_elsewhere', '1', 600, 'G', 'A', 0.02, 0.02, 0.02, 0.02, "
                    "0.02, 0.02, 0.02, 0.02, 4)"
                )
            )
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert().values(
                    rsid="rs_elsewhere", chrom="1", pos=999, genotype="AG"
                )
            )

        run_annotation(sample_engine, mock_registry)

        row = self._annotated_row(sample_engine, "rs_elsewhere")
        assert row is not None
        assert row.gnomad_af_global is None
        assert row.gnomad_source_status == "locus_unresolved"
        assert row.gnomad_source_status != "allele_ambiguous"

    def test_no_call_alias_at_another_locus_still_conflicts(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
        gnomad_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """#2214 review: a no-call has no allele, but it still occupies a locus.

        Skipping no-call rows entirely (to stop them suppressing a valid call)
        also erased their coordinate, so the typed alias's locus was selected and
        `_rekey_to_original` fanned that frequency back to the no-call row at a
        different position -- the cross-locus assignment this PR exists to stop.
        Genotype conflicts now come from typed rows only; locus conflicts from
        every row.
        """
        with reference_engine.begin() as conn:
            conn.execute(
                dbsnp_merges.insert().values(
                    old_rsid="rs_nc_far", current_rsid="rs_multiloc_nc", build_id=155
                )
            )
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_asj, "
                    "af_eas, af_eur, af_fin, af_sas, homozygous_count) VALUES "
                    "('rs_multiloc_nc', '1', 300, 'G', 'A', 0.004, 0.004, 0.004, 0.004, "
                    "0.004, 0.004, 0.004, 0.004, 3), "
                    "('rs_multiloc_nc', '1', 900, 'G', 'A', 0.40, 0.40, 0.40, 0.40, "
                    "0.40, 0.40, 0.40, 0.40, 800)"
                )
            )
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [
                    # No-call, at the OTHER coordinate from the typed alias.
                    {"rsid": "rs_nc_far", "chrom": "1", "pos": 900, "genotype": "--"},
                    {"rsid": "rs_multiloc_nc", "chrom": "1", "pos": 300, "genotype": "AG"},
                ],
            )

        run_annotation(sample_engine, mock_registry)

        nc_row = self._annotated_row(sample_engine, "rs_nc_far")
        assert nc_row is not None
        # It sits at pos 900 and was never typed; it must not receive pos 300's
        # frequency just because the typed alias resolved there.
        assert nc_row.gnomad_af_global != pytest.approx(0.004)

    def test_aliases_resolving_to_one_alt_keep_their_frequency(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
        gnomad_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """#2214 review: different zygosities of ONE allele are not a conflict.

        `AG` and `AA` at a G>A / G>T site are het and hom for the same allele:
        both unambiguously select G>A. Comparing genotype *strings* called that
        a conflict and withheld a perfectly resolvable frequency -- and with
        `include_novel=False` that drops the carried variant. The guard now
        compares what the calls RESOLVE TO.
        """
        with reference_engine.begin() as conn:
            conn.execute(
                dbsnp_merges.insert().values(
                    old_rsid="rs_samealt_old", current_rsid="rs_shared_prod", build_id=155
                )
            )
        self._shared_rsid_gnomad_rows(gnomad_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [
                    {"rsid": "rs_samealt_old", "chrom": "1", "pos": 300, "genotype": "AG"},
                    {"rsid": "rs_shared_prod", "chrom": "1", "pos": 300, "genotype": "AA"},
                ],
            )

        run_annotation(sample_engine, mock_registry)

        for rsid in ("rs_samealt_old", "rs_shared_prod"):
            row = self._annotated_row(sample_engine, rsid)
            assert row is not None, rsid
            assert row.gnomad_af_global == pytest.approx(0.001), rsid

    def test_equal_genotypes_at_different_loci_with_different_alts_withhold(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
        gnomad_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """#2214 review: global resolution must not precede locus filtering.

        Both aliases call `AG`, but the gnomAD rows are G>A at pos 300 and G>T
        at pos 900. `AG` carries G>A only, so resolving *globally* collapses the
        candidates to the pos-300 row -- and `_rekey_to_original` then hands its
        frequency to the alias sitting at pos 900. A locus disagreement now
        withholds before any resolution shortcut can run.
        """
        with reference_engine.begin() as conn:
            conn.execute(
                dbsnp_merges.insert().values(
                    old_rsid="rs_xloc_old", current_rsid="rs_xloc_cur", build_id=155
                )
            )
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_asj, "
                    "af_eas, af_eur, af_fin, af_sas, homozygous_count) VALUES "
                    "('rs_xloc_cur', '1', 300, 'G', 'A', 0.004, 0.004, 0.004, 0.004, "
                    "0.004, 0.004, 0.004, 0.004, 3), "
                    "('rs_xloc_cur', '1', 900, 'G', 'T', 0.40, 0.40, 0.40, 0.40, "
                    "0.40, 0.40, 0.40, 0.40, 800)"
                )
            )
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [
                    {"rsid": "rs_xloc_old", "chrom": "1", "pos": 300, "genotype": "AG"},
                    {"rsid": "rs_xloc_cur", "chrom": "1", "pos": 900, "genotype": "AG"},
                ],
            )

        run_annotation(sample_engine, mock_registry)

        far_row = self._annotated_row(sample_engine, "rs_xloc_cur")
        assert far_row is not None
        # It sits at pos 900; pos 300's 0.004 is not its evidence.
        assert far_row.gnomad_af_global != pytest.approx(0.004)

    def test_alias_locus_conflict_withholds_even_for_a_single_row(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
        gnomad_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """#2214 review: the locus conflict is not about how many rows gnomAD has.

        Aliases at pos 300 and pos 900 cannot share one per-rsID result whatever
        gnomAD holds. Scoping the guard to `len(candidates) > 1` skipped it when
        only one row existed, and that row's frequency went to both calls.
        """
        with reference_engine.begin() as conn:
            conn.execute(
                dbsnp_merges.insert().values(
                    old_rsid="rs_one_row_old", current_rsid="rs_one_row", build_id=155
                )
            )
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_asj, "
                    "af_eas, af_eur, af_fin, af_sas, homozygous_count) VALUES "
                    "('rs_one_row', '1', 300, 'G', 'A', 0.006, 0.006, 0.006, 0.006, "
                    "0.006, 0.006, 0.006, 0.006, 5)"
                )
            )
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [
                    {"rsid": "rs_one_row_old", "chrom": "1", "pos": 300, "genotype": "AG"},
                    {"rsid": "rs_one_row", "chrom": "1", "pos": 900, "genotype": "AG"},
                ],
            )

        run_annotation(sample_engine, mock_registry)

        far_row = self._annotated_row(sample_engine, "rs_one_row")
        assert far_row is not None
        assert far_row.gnomad_af_global != pytest.approx(0.006)

    def test_genotype_conflict_resolves_within_the_sample_locus(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
        gnomad_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """#2214 review: resolve AFTER narrowing, never before.

        Same-locus aliases `AG`/`AA` at pos 300, with gnomAD holding G>T at 300
        and G>A at 900. Resolving first picks the pos-900 row (the only one
        either genotype carries), collapses the candidates to it, and then slips
        past the multi-locus check because only one row remains -- publishing
        900's frequency for a call at 300.
        """
        with reference_engine.begin() as conn:
            conn.execute(
                dbsnp_merges.insert().values(
                    old_rsid="rs_narrow_old", current_rsid="rs_narrow", build_id=155
                )
            )
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_asj, "
                    "af_eas, af_eur, af_fin, af_sas, homozygous_count) VALUES "
                    "('rs_narrow', '1', 300, 'G', 'T', 0.30, 0.30, 0.30, 0.30, "
                    "0.30, 0.30, 0.30, 0.30, 600), "
                    "('rs_narrow', '1', 900, 'G', 'A', 0.002, 0.002, 0.002, 0.002, "
                    "0.002, 0.002, 0.002, 0.002, 1)"
                )
            )
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [
                    {"rsid": "rs_narrow_old", "chrom": "1", "pos": 300, "genotype": "AG"},
                    {"rsid": "rs_narrow", "chrom": "1", "pos": 300, "genotype": "AA"},
                ],
            )

        run_annotation(sample_engine, mock_registry)

        row = self._annotated_row(sample_engine, "rs_narrow")
        assert row is not None
        # pos 900's 0.002 is not evidence for a call at pos 300.
        assert row.gnomad_af_global != pytest.approx(0.002)

    def test_alias_conflict_with_rows_at_both_loci_is_not_a_position_miss(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
        gnomad_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """#2214 review: withholding is right, but the stated reason must be true.

        gnomAD lists a row at BOTH of the sample's positions, so "listed only at
        other genomic positions" is false and would send someone hunting a build
        mismatch that does not exist. The real limit is that one per-rsID result
        cannot carry a frequency for two calls.
        """
        with reference_engine.begin() as conn:
            conn.execute(
                dbsnp_merges.insert().values(
                    old_rsid="rs_both_old", current_rsid="rs_both_cur", build_id=155
                )
            )
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_asj, "
                    "af_eas, af_eur, af_fin, af_sas, homozygous_count) VALUES "
                    "('rs_both_cur', '1', 300, 'G', 'A', 0.004, 0.004, 0.004, 0.004, "
                    "0.004, 0.004, 0.004, 0.004, 3), "
                    "('rs_both_cur', '1', 900, 'G', 'A', 0.40, 0.40, 0.40, 0.40, "
                    "0.40, 0.40, 0.40, 0.40, 800)"
                )
            )
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [
                    {"rsid": "rs_both_old", "chrom": "1", "pos": 300, "genotype": "AG"},
                    {"rsid": "rs_both_cur", "chrom": "1", "pos": 900, "genotype": "AG"},
                ],
            )

        run_annotation(sample_engine, mock_registry)

        row = self._annotated_row(sample_engine, "rs_both_old")
        assert row is not None
        assert row.gnomad_af_global is None
        assert row.gnomad_source_status == "alias_unresolved"
        assert row.gnomad_source_status != "locus_unresolved"

    def test_partially_matched_aliases_get_their_own_reason(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
        gnomad_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """#2214 review: the withhold reason is per ORIGINAL, not per query.

        Aliases at pos 300 and pos 900 where gnomAD holds a row only at 300: the
        pos-300 call hit the shared-rsID limitation, but the pos-900 call really
        did encounter a position mismatch. Fanning one query-level status to both
        told the pos-900 call it was a generic shared-rsID case and hid the
        build/mapping problem its own coordinate met.
        """
        with reference_engine.begin() as conn:
            conn.execute(
                dbsnp_merges.insert().values(
                    old_rsid="rs_part_old", current_rsid="rs_part_cur", build_id=155
                )
            )
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_asj, "
                    "af_eas, af_eur, af_fin, af_sas, homozygous_count) VALUES "
                    "('rs_part_cur', '1', 300, 'G', 'A', 0.004, 0.004, 0.004, 0.004, "
                    "0.004, 0.004, 0.004, 0.004, 3)"
                )
            )
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [
                    # Its coordinate IS listed -> shared-rsID limitation.
                    {"rsid": "rs_part_old", "chrom": "1", "pos": 300, "genotype": "AG"},
                    # Its coordinate is NOT listed -> genuine position mismatch.
                    {"rsid": "rs_part_cur", "chrom": "1", "pos": 900, "genotype": "AG"},
                ],
            )

        run_annotation(sample_engine, mock_registry)

        matched = self._annotated_row(sample_engine, "rs_part_old")
        unmatched = self._annotated_row(sample_engine, "rs_part_cur")
        assert matched is not None and unmatched is not None
        assert matched.gnomad_source_status == "alias_unresolved"
        assert unmatched.gnomad_source_status == "locus_unresolved"
        # Neither may borrow the other's frequency.
        assert matched.gnomad_af_global is None
        assert unmatched.gnomad_af_global is None

    def test_frequency_is_dropped_when_the_row_resolves_to_another_allele(
        self,
        sample_engine: sa.Engine,
        mock_registry: MagicMock,
        gnomad_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """#2214 review: the frequency must describe the allele the row records.

        gnomAD holds G>A and G>T; the sample calls `AG`, so G>A is selected. But
        ClinVar wins allele identity and lists only G>T, so the stored row used
        to carry G>A's frequency, homozygote count and rarity flags under a G>T
        identity -- #2171's defect surviving into the merged row.
        """
        with reference_engine.begin() as conn:
            conn.execute(
                clinvar_variants.insert().values(
                    rsid="rs_idmix",
                    chrom="1",
                    pos=300,
                    ref="G",
                    alt="T",
                    significance="Pathogenic",
                    review_stars=2,
                    accession="VCV999999",
                    conditions="Test condition",
                    gene_symbol="GENE1",
                    variation_id=999999,
                )
            )
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_asj, "
                    "af_eas, af_eur, af_fin, af_sas, homozygous_count) VALUES "
                    "('rs_idmix', '1', 300, 'G', 'A', 0.001, 0.001, 0.001, 0.001, "
                    "0.001, 0.001, 0.001, 0.001, 1), "
                    "('rs_idmix', '1', 300, 'G', 'T', 0.20, 0.05, 0.04, 0.03, "
                    "0.02, 0.01, 0.07, 0.08, 500)"
                )
            )
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert().values(rsid="rs_idmix", chrom="1", pos=300, genotype="AG")
            )

        run_annotation(sample_engine, mock_registry)

        row = self._annotated_row(sample_engine, "rs_idmix")
        assert row is not None
        # The row's identity is ClinVar's G>T.
        assert (row.ref, row.alt) == ("G", "T")
        # So G>A's 0.001 must not ride along under it.
        assert row.gnomad_af_global is None
        assert row.gnomad_homozygous_count is None
        # The genotype DID identify gnomAD's allele; the sources disagree. Saying
        # "your genotype cannot identify which allele you carry" would be false
        # (#2214 review), so this is its own status.
        assert row.gnomad_source_status == "allele_mismatch"
        assert row.gnomad_source_status != "allele_ambiguous"
        # The rarity flags are derived from the rejected frequency and carry no
        # `gnomad_` prefix, so a prefix-only sweep left them behind -- labelling
        # the G>T row rare on G>A's evidence (#2214 review).
        assert not row.rare_flag
        assert not row.ultra_rare_flag

    def test_single_alt_rsid_is_annotated_regardless_of_genotype(
        self, sample_engine: sa.Engine, mock_registry: MagicMock, gnomad_engine: sa.Engine
    ) -> None:
        """Discriminating control: withholding must not swallow unambiguous sites.

        With one catalogued ALT there is nothing to disambiguate, so a hom-ref
        sample still gets that variant's frequency — otherwise the fix would
        read as correct simply by suppressing gnomAD everywhere.
        """
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_asj, "
                    "af_eas, af_eur, af_fin, af_sas, homozygous_count) VALUES "
                    "('rs_single_prod', '1', 400, 'G', 'A', 0.02, 0.02, 0.02, 0.02, "
                    "0.02, 0.02, 0.02, 0.02, 7)"
                )
            )
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert().values(
                    rsid="rs_single_prod", chrom="1", pos=400, genotype="GG"
                )
            )

        run_annotation(sample_engine, mock_registry)

        row = self._annotated_row(sample_engine, "rs_single_prod")
        assert row is not None
        assert row.gnomad_af_global == pytest.approx(0.02)
        assert row.gnomad_homozygous_count == 7

    def test_position_lookup_preserves_aliases_for_same_coordinate(
        self, gnomad_engine: sa.Engine
    ) -> None:
        """Several raw IDs with the same exact allele all receive the coordinate hit."""
        with gnomad_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af "
                    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, "
                    "af_asj, af_eas, af_eur, af_fin, af_sas, homozygous_count) "
                    "VALUES "
                    "('rs_gnomad_alias', '5', 501, 'C', 'T', 0.02, "
                    "0.03, 0.01, 0.022, 0.02, 0.025, 0.015, 0.018, 30)"
                )
            )

        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, "
                    "genotype TEXT, ref TEXT, alt TEXT)"
                )
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs_alias_a', '5', 501, 'CT', 'C', 'T')"))
            conn.execute(sa.text("INSERT INTO t VALUES ('rs_alias_b', '5', 501, 'CT', 'C', 'T')"))
            rows = conn.execute(sa.text("SELECT * FROM t")).fetchall()

        raw_by_rsid = {row.rsid: row for row in rows}
        result = _lookup_gnomad(["rs_alias_a", "rs_alias_b"], raw_by_rsid, gnomad_engine)

        assert result["rs_alias_a"]["gnomad_af_global"] == pytest.approx(0.02)
        assert result["rs_alias_b"]["gnomad_af_global"] == pytest.approx(0.02)

    def test_position_fallback_skipped_without_ref_alt(self, gnomad_engine: sa.Engine) -> None:
        """Fallback is skipped when raw variant lacks ref/alt columns."""
        # Create a raw row without ref/alt (like 23andMe data)
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text("CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, genotype TEXT)")
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs_no_match', '1', 100, 'AG')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        raw_by_rsid = {"rs_no_match": row}
        result = _lookup_gnomad(["rs_no_match"], raw_by_rsid, gnomad_engine)

        # No match by rsid and no ref/alt for position fallback
        assert "rs_no_match" not in result

    def test_gnomad_fields_in_annotated_variants(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-09: Full pipeline writes all gnomAD fields to annotated_variants."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs7412")
            ).fetchone()

        assert row is not None
        # Global AF
        assert row.gnomad_af_global == pytest.approx(0.0781)
        # Per-population AFs (including ASJ from gnomAD r2.1)
        assert row.gnomad_af_afr == pytest.approx(0.1130)
        assert row.gnomad_af_amr == pytest.approx(0.0560)
        # Real gnomAD v4.1 ASJ AF, distinct from af_global (#1964).
        assert row.gnomad_af_asj == pytest.approx(0.0741546)
        assert row.gnomad_af_asj != pytest.approx(row.gnomad_af_global)
        assert row.gnomad_af_eas == pytest.approx(0.0980)
        assert row.gnomad_af_eur == pytest.approx(0.0730)
        assert row.gnomad_af_sas == pytest.approx(0.0650)
        assert row.gnomad_source_status == GNOMAD_SOURCE_OBSERVED
        # Homozygous count
        assert row.gnomad_homozygous_count == 874
        # Rare flags
        assert row.rare_flag in (False, 0)
        assert row.ultra_rare_flag in (False, 0)
        # Bitmask has gnomAD bit set
        assert row.annotation_coverage & GNOMAD_BIT == GNOMAD_BIT

    def test_gnomad_rare_variant_in_pipeline(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-09: rare-variant flags flow through the full pipeline.

        rs80357906 (BRCA1 5382insC) carries its real gnomAD v4.1 ASJ founder AF
        (0.00118) end-to-end: rare, but NOT ultra-rare — popmax exceeds the 0.001
        floor (#1964). The copied af_asj column used to propagate ultra-rare here.
        """
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs80357906")
            ).fetchone()

        assert row is not None
        assert row.gnomad_af_global == pytest.approx(0.00004)
        assert row.gnomad_af_asj == pytest.approx(0.00118275)
        assert row.rare_flag in (True, 1)
        assert row.ultra_rare_flag in (False, 0)

    def test_delegates_to_gnomad_module(self, gnomad_engine: sa.Engine) -> None:
        """Engine uses gnomad.py lookup functions (not duplicated SQL)."""
        # Verify that the module-level lookup and engine lookup produce
        # identical results, proving delegation
        module_result = lookup_gnomad_by_rsids(["rs429358"], gnomad_engine)
        engine_result = _lookup_gnomad(["rs429358"], {}, gnomad_engine)

        module_annot = module_result["rs429358"]
        engine_data = engine_result["rs429358"]

        assert engine_data["gnomad_af_global"] == module_annot.af_global
        assert engine_data["gnomad_af_afr"] == module_annot.af_afr
        assert engine_data["gnomad_af_amr"] == module_annot.af_amr
        assert engine_data["gnomad_af_asj"] == module_annot.af_asj
        assert engine_data["gnomad_af_eas"] == module_annot.af_eas
        assert engine_data["gnomad_af_eur"] == module_annot.af_eur
        assert engine_data["gnomad_af_sas"] == module_annot.af_sas
        assert engine_data["gnomad_homozygous_count"] == module_annot.homozygous_count
        assert engine_data["rare_flag"] == module_annot.rare_flag
        assert engine_data["ultra_rare_flag"] == module_annot.ultra_rare_flag


# ═══════════════════════════════════════════════════════════════════════
# P2-12: dbNSFP annotation integration
# ═══════════════════════════════════════════════════════════════════════


class TestDbnsfpAnnotationIntegration:
    """P2-12: dbNSFP annotation integrated into annotation engine.

    Verifies:
    - Delegation to dbnsfp.py lookup functions (not duplicated SQL)
    - All 14 score fields flow through pipeline into annotated_variants
    - deleterious_count is computed and stored
    - Position-based fallback for unmatched rsids with ref/alt
    - _dbnsfp_annot_to_dict conversion preserves all fields
    """

    def test_dbnsfp_annot_to_dict_preserves_fields(self) -> None:
        """_dbnsfp_annot_to_dict converts DbNSFPAnnotation to engine dict."""
        annot = DbNSFPAnnotation(
            rsid="rs429358",
            chrom="19",
            pos=44908684,
            ref="T",
            alt="C",
            cadd_phred=28.3,
            sift_score=0.001,
            sift_pred="D",
            polyphen2_hsvar_score=0.998,
            polyphen2_hsvar_pred="D",
            revel=0.812,
            mutpred2=0.780,
            vest4=0.891,
            metasvm=0.920,
            metalr=0.885,
            gerp_rs=5.48,
            phylop=7.92,
            mpc=1.85,
            primateai=0.91,
        )
        d = _dbnsfp_annot_to_dict(annot)

        assert d["cadd_phred"] == pytest.approx(28.3)
        assert d["sift_score"] == pytest.approx(0.001)
        assert d["sift_pred"] == "D"
        assert d["polyphen2_hsvar_score"] == pytest.approx(0.998)
        assert d["polyphen2_hsvar_pred"] == "D"
        assert d["revel"] == pytest.approx(0.812)
        assert d["mutpred2"] == pytest.approx(0.780)
        assert d["vest4"] == pytest.approx(0.891)
        assert d["metasvm"] == pytest.approx(0.920)
        assert d["metalr"] == pytest.approx(0.885)
        assert d["gerp_rs"] == pytest.approx(5.48)
        assert d["phylop"] == pytest.approx(7.92)
        assert d["mpc"] == pytest.approx(1.85)
        assert d["primateai"] == pytest.approx(0.91)
        # All 4 independent axes deleterious (META = REVEL/MetaSVM/MetaLR, F24)
        assert d["deleterious_count"] == 4

    def test_dbnsfp_annot_to_dict_null_scores(self) -> None:
        """_dbnsfp_annot_to_dict handles all-null scores."""
        annot = DbNSFPAnnotation(
            rsid="rs1",
            chrom="1",
            pos=100,
            ref="A",
            alt="G",
        )
        d = _dbnsfp_annot_to_dict(annot)

        assert d["cadd_phred"] is None
        assert d["sift_score"] is None
        assert d["revel"] is None
        assert d["deleterious_count"] == 0

    def test_rsid_lookup_returns_all_score_fields(self, dbnsfp_engine: sa.Engine) -> None:
        """P2-12: Lookup returns all 14 dbNSFP score fields."""
        result = _lookup_dbnsfp(["rs429358"], {}, dbnsfp_engine)

        assert "rs429358" in result
        data = result["rs429358"]
        assert data["cadd_phred"] == pytest.approx(28.3)
        assert data["sift_score"] == pytest.approx(0.001)
        assert data["sift_pred"] == "D"
        assert data["polyphen2_hsvar_score"] == pytest.approx(0.998)
        assert data["polyphen2_hsvar_pred"] == "D"
        assert data["revel"] == pytest.approx(0.812)
        assert data["mutpred2"] == pytest.approx(0.780)
        assert data["vest4"] == pytest.approx(0.891)
        assert data["metasvm"] == pytest.approx(0.920)
        assert data["metalr"] == pytest.approx(0.885)
        assert data["gerp_rs"] == pytest.approx(5.48)
        assert data["phylop"] == pytest.approx(7.92)
        assert data["mpc"] == pytest.approx(1.85)
        assert data["primateai"] == pytest.approx(0.91)

    def test_rsid_lookup_returns_deleterious_count(self, dbnsfp_engine: sa.Engine) -> None:
        """P2-12: Lookup computes and returns deleterious_count."""
        result = _lookup_dbnsfp(["rs429358"], {}, dbnsfp_engine)
        data = result["rs429358"]
        # rs429358: SIFT(D), PP2(D), CADD(D), META=REVEL/MetaSVM/MetaLR(D) → 4 axes (F24)
        assert data["deleterious_count"] == 4

    def test_delegates_to_dbnsfp_module(self, dbnsfp_engine: sa.Engine) -> None:
        """Engine uses dbnsfp.py lookup functions (not duplicated SQL)."""
        module_result = lookup_dbnsfp_by_rsids(["rs429358"], dbnsfp_engine)
        engine_result = _lookup_dbnsfp(["rs429358"], {}, dbnsfp_engine)

        module_annot = module_result["rs429358"]
        engine_data = engine_result["rs429358"]

        assert engine_data["cadd_phred"] == module_annot.cadd_phred
        assert engine_data["sift_score"] == module_annot.sift_score
        assert engine_data["sift_pred"] == module_annot.sift_pred
        assert engine_data["polyphen2_hsvar_score"] == module_annot.polyphen2_hsvar_score
        assert engine_data["polyphen2_hsvar_pred"] == module_annot.polyphen2_hsvar_pred
        assert engine_data["revel"] == module_annot.revel
        assert engine_data["mutpred2"] == module_annot.mutpred2
        assert engine_data["vest4"] == module_annot.vest4
        assert engine_data["metasvm"] == module_annot.metasvm
        assert engine_data["metalr"] == module_annot.metalr
        assert engine_data["gerp_rs"] == module_annot.gerp_rs
        assert engine_data["phylop"] == module_annot.phylop
        assert engine_data["mpc"] == module_annot.mpc
        assert engine_data["primateai"] == module_annot.primateai
        assert engine_data["deleterious_count"] == module_annot.deleterious_count

    def test_position_fallback_skipped_cross_build(self, dbnsfp_engine: sa.Engine) -> None:
        """F35: the GRCh37 position fallback is skipped against GRCh38 dbNSFP.

        Even when a raw row carries ref/alt (a future VCF/WGS input), the
        ``(chrom, pos, ref, alt)`` fallback is a GRCh37→GRCh38 cross-build join,
        so ``lookup_dbnsfp_by_positions`` declines it and the engine produces no
        position-based match. The rsid path (build-agnostic) remains the only
        live match. (The skip warning is asserted in test_dbnsfp.py.)
        """
        # Create a fake raw row with ref/alt — would have triggered the (now
        # guarded) position fallback before F35.
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, "
                    "genotype TEXT, ref TEXT, alt TEXT)"
                )
            )
            conn.execute(
                sa.text("INSERT INTO t VALUES ('rs_user_id', '19', 44908684, 'TC', 'T', 'C')")
            )
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        raw_by_rsid = {"rs_user_id": row}
        result = _lookup_dbnsfp(["rs_user_id"], raw_by_rsid, dbnsfp_engine)

        # rsid "rs_user_id" is not in the dbNSFP DB and the position fallback is
        # cross-build → no match at all.
        assert "rs_user_id" not in result

    def test_position_fallback_skipped_without_ref_alt(self, dbnsfp_engine: sa.Engine) -> None:
        """Fallback is skipped when raw variant lacks ref/alt columns."""
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text("CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, genotype TEXT)")
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs_no_match', '1', 100, 'AG')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        raw_by_rsid = {"rs_no_match": row}
        result = _lookup_dbnsfp(["rs_no_match"], raw_by_rsid, dbnsfp_engine)

        assert "rs_no_match" not in result

    def test_dbnsfp_fields_in_annotated_variants(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-12: Full pipeline writes all dbNSFP fields to annotated_variants."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs429358")
            ).fetchone()

        assert row is not None
        # All 14 score fields
        assert row.cadd_phred == pytest.approx(28.3)
        assert row.sift_score == pytest.approx(0.001)
        assert row.sift_pred == "D"
        assert row.polyphen2_hsvar_score == pytest.approx(0.998)
        assert row.polyphen2_hsvar_pred == "D"
        assert row.revel == pytest.approx(0.812)
        assert row.mutpred2 == pytest.approx(0.780)
        assert row.vest4 == pytest.approx(0.891)
        assert row.metasvm == pytest.approx(0.920)
        assert row.metalr == pytest.approx(0.885)
        assert row.gerp_rs == pytest.approx(5.48)
        assert row.phylop == pytest.approx(7.92)
        assert row.mpc == pytest.approx(1.85)
        assert row.primateai == pytest.approx(0.91)
        # Deleterious count: 4 independent axes (SIFT, PolyPhen, CADD, collapsed
        # META = REVEL/MetaSVM/MetaLR), all deleterious (F24).
        assert row.deleterious_count == 4
        assert row.deleterious_total_assessed == 4
        # Bitmask has dbNSFP bit set
        assert row.annotation_coverage & DBNSFP_BIT == DBNSFP_BIT

    def test_deleterious_count_in_pipeline(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-12: deleterious_count flows through full pipeline correctly."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            rows = conn.execute(
                sa.select(annotated_variants).where(
                    annotated_variants.c.annotation_coverage.op("&")(DBNSFP_BIT) == DBNSFP_BIT
                )
            ).fetchall()

        # All dbNSFP-matched variants should have deleterious_count
        for row in rows:
            assert row.deleterious_count is not None
            # 4 independent axes after the meta-predictor collapse (F24).
            assert 0 <= row.deleterious_count <= 4

    def test_partial_scores_deleterious_count(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-12: Variant with partial scores has correct deleterious count."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs1801133")
            ).fetchone()

        # rs1801133 (MTHFR C677T) is in seed data for all sources
        assert row is not None
        assert row.deleterious_count is not None
        assert 0 <= row.deleterious_count <= 4

    def test_known_variant_rs1801133_scores(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """T2-11 via engine: rs1801133 CADD and REVEL scores flow through pipeline."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs1801133")
            ).fetchone()

        assert row is not None
        assert row.cadd_phred == pytest.approx(24.8)
        assert row.revel == pytest.approx(0.689)

    def test_empty_rsids(self, dbnsfp_engine: sa.Engine) -> None:
        """Empty rsid list returns empty dict."""
        result = _lookup_dbnsfp([], {}, dbnsfp_engine)
        assert len(result) == 0


# ═══════════════════════════════════════════════════════════════════════
# P2-13 / F24/F25: Ensemble pathogenicity flag (majority of present axes)
# ═══════════════════════════════════════════════════════════════════════


class TestEnsemblePathogenicIntegration:
    """P2-13 / F24/F25: ensemble flag = strict majority of *present* independent axes.

    Verifies:
    - _dbnsfp_annot_to_dict includes ensemble_pathogenic + deleterious_total_assessed
    - apply_ensemble_pathogenic sets flag on merged dicts via the k-of-present rule
    - Flag and denominator flow through the full pipeline into annotated_variants
    - The four axes are SIFT, PolyPhen, CADD and the collapsed META family (F24)
    - The denominator is the axes actually assessed, not a fixed 5 (F25)
    """

    def test_dbnsfp_annot_to_dict_includes_ensemble_flag(self) -> None:
        """_dbnsfp_annot_to_dict carries the vote counts and a True flag when a majority agree."""
        annot = DbNSFPAnnotation(
            rsid="rs1",
            chrom="1",
            pos=100,
            ref="A",
            alt="G",
            cadd_phred=28.0,
            sift_score=0.001,
            sift_pred="D",
            polyphen2_hsvar_score=0.998,
            polyphen2_hsvar_pred="D",
            revel=0.8,
            metasvm=0.9,
        )
        d = _dbnsfp_annot_to_dict(annot)
        # SIFT, PolyPhen, CADD + collapsed META (REVEL/MetaSVM) = 4 axes, all del.
        assert d["deleterious_count"] == 4
        assert d["deleterious_total_assessed"] == 4
        assert d["ensemble_pathogenic"] is True

    def test_dbnsfp_annot_to_dict_not_pathogenic_under_threshold(self) -> None:
        """_dbnsfp_annot_to_dict returns ensemble_pathogenic=False when <3 deleterious."""
        annot = DbNSFPAnnotation(
            rsid="rs2",
            chrom="1",
            pos=200,
            ref="A",
            alt="G",
            sift_score=0.001,
            sift_pred="D",
            polyphen2_hsvar_score=0.95,  # > 0.909 "probably damaging" (F38)
            polyphen2_hsvar_pred="D",
            # Only 2 of 4 axes deleterious (SIFT + PolyPhen); CADD and the META
            # axis (REVEL+MetaSVM both tolerated) vote not-deleterious.
            cadd_phred=10.0,  # Below 20 threshold
            revel=0.3,  # Below 0.5 threshold
            metasvm=-0.5,  # Below 0 threshold
        )
        d = _dbnsfp_annot_to_dict(annot)
        assert d["deleterious_count"] == 2
        assert d["deleterious_total_assessed"] == 4
        # 2 of 4 is not a strict majority → not flagged.
        assert d["ensemble_pathogenic"] is False

    def test_dbnsfp_annot_to_dict_null_scores_not_pathogenic(self) -> None:
        """All-null scores yield ensemble_pathogenic=False."""
        annot = DbNSFPAnnotation(
            rsid="rs3",
            chrom="1",
            pos=300,
            ref="A",
            alt="G",
        )
        d = _dbnsfp_annot_to_dict(annot)
        assert d["deleterious_count"] == 0
        assert d["ensemble_pathogenic"] is False

    def test_apply_ensemble_pathogenic_on_merged(self) -> None:
        """apply_ensemble_pathogenic sets the flag via the k-of-present rule (F24/F25)."""
        from backend.annotation.engine import apply_ensemble_pathogenic

        merged = [
            # 3 of 4 → strict majority → flagged
            {"rsid": "rs1", "deleterious_count": 3, "deleterious_total_assessed": 4},
            # 2 of 4 → not a majority → not flagged
            {"rsid": "rs2", "deleterious_count": 2, "deleterious_total_assessed": 4},
            # 2 of 2 present → majority → flagged (unreachable under the old fixed-3)
            {"rsid": "rs3", "deleterious_count": 2, "deleterious_total_assessed": 2},
            # 1 of 1 present → too few axes → not flagged
            {"rsid": "rs4", "deleterious_count": 1, "deleterious_total_assessed": 1},
            {"rsid": "rs5"},  # No vote counts at all → flag left unset
        ]
        apply_ensemble_pathogenic(merged)

        assert merged[0]["ensemble_pathogenic"] is True
        assert merged[1]["ensemble_pathogenic"] is False
        assert merged[2]["ensemble_pathogenic"] is True
        assert merged[3]["ensemble_pathogenic"] is False
        assert "ensemble_pathogenic" not in merged[4]

    def test_apply_ensemble_pathogenic_does_not_overwrite(self) -> None:
        """apply_ensemble_pathogenic skips dicts that already have the key."""
        from backend.annotation.engine import apply_ensemble_pathogenic

        merged = [
            {
                "rsid": "rs1",
                "deleterious_count": 1,
                "deleterious_total_assessed": 4,
                "ensemble_pathogenic": True,
            },
        ]
        apply_ensemble_pathogenic(merged)
        # Pre-set flag is preserved even though 1 of 4 would not flag on its own.
        assert merged[0]["ensemble_pathogenic"] is True

    def test_ensemble_flag_in_annotated_variants_true(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-13: Variant with ≥3 deleterious tools has ensemble_pathogenic=True in DB."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            # rs429358: SIFT=D, PP2=D, CADD=D, META(REVEL/MetaSVM/MetaLR)=D
            # → all 4 independent axes deleterious (F24).
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs429358")
            ).fetchone()

        assert row is not None
        assert row.deleterious_count == 4
        assert row.deleterious_total_assessed == 4
        assert row.ensemble_pathogenic in (True, 1)

    def test_ensemble_flag_in_annotated_variants_false(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-13: Variant with <3 deleterious tools has ensemble_pathogenic=False."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            # rs4680: SIFT=0.082(T), PP2=0.451(T), CADD=15.2(T), and the META axis
            # is tolerated — REVEL=0.312(T) and MetaLR=0.420(T) outvote the lone
            # MetaSVM=0.380(D), so the collapsed family contributes no vote (F24).
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs4680")
            ).fetchone()

        assert row is not None
        assert row.deleterious_count == 0
        assert row.deleterious_total_assessed == 4
        assert row.ensemble_pathogenic in (False, 0)

    def test_ensemble_flag_exactly_three(
        self,
        sample_engine: sa.Engine,
        dbnsfp_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """P2-13: Variant with exactly 3 deleterious tools is flagged."""
        # Insert a custom variant with exactly 3 deleterious predictions:
        # SIFT=0.01(D), PP2=0.95(D>0.909), CADD=25(D≥20), REVEL=0.3(<0.5), MetaSVM=-0.5(<0)
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert().values(
                    rsid="rs_three_del", chrom="1", pos=99999, genotype="AG"
                )
            )
        with dbnsfp_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO dbnsfp_scores "
                    "(rsid, chrom, pos, ref, alt, cadd_phred, sift_score, sift_pred, "
                    "polyphen2_hsvar_score, polyphen2_hsvar_pred, revel, metasvm) "
                    "VALUES ('rs_three_del', '1', 99999, 'A', 'G', 25.0, 0.01, 'D', "
                    "0.95, 'D', 0.3, -0.5)"
                )
            )

        registry = MagicMock()
        registry.reference_engine = reference_engine
        type(registry).vep_engine = property(
            lambda self: (_ for _ in ()).throw(FileNotFoundError("no VEP"))
        )
        type(registry).gnomad_engine = property(
            lambda self: (_ for _ in ()).throw(FileNotFoundError("no gnomAD"))
        )
        type(registry).dbnsfp_engine = property(lambda s: dbnsfp_engine)

        run_annotation(sample_engine, registry)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs_three_del")
            ).fetchone()

        assert row is not None
        assert row.deleterious_count == 3
        assert row.ensemble_pathogenic in (True, 1)

    def test_all_dbnsfp_variants_have_ensemble_flag(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-13: Every variant with dbNSFP data has ensemble_pathogenic set."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            rows = conn.execute(
                sa.select(annotated_variants).where(
                    annotated_variants.c.annotation_coverage.op("&")(DBNSFP_BIT) == DBNSFP_BIT
                )
            ).fetchall()

        assert len(rows) > 0
        for row in rows:
            assert row.ensemble_pathogenic is not None
            assert row.deleterious_count is not None
            assert row.deleterious_total_assessed is not None
            # Flag matches the k-of-present rule: a strict majority of the
            # assessed axes, with at least 2 axes present (F24/F25).
            expected = (
                row.deleterious_total_assessed >= 2
                and row.deleterious_count * 2 > row.deleterious_total_assessed
            )
            assert bool(row.ensemble_pathogenic) is expected

    def test_ensemble_pathogenic_in_upsert_columns(self) -> None:
        """ensemble_pathogenic is in _UPSERT_COLUMNS list."""
        from backend.annotation.engine import _UPSERT_COLUMNS

        assert "ensemble_pathogenic" in _UPSERT_COLUMNS


# ═══════════════════════════════════════════════════════════════════════
# P2-15: Gene-phenotype annotation
# ═══════════════════════════════════════════════════════════════════════


class TestGenePhenotypeAnnotation:
    """P2-15: Gene-phenotype annotation via MONDO/HPO + optional OMIM.

    Verifies:
    - _lookup_gene_phenotype maps VEP gene symbols to phenotype records
    - Gene-phenotype data flows through _merge_annotations with bitmask bit 4
    - Full pipeline writes disease_name, disease_id, hpo_terms, phenotype_source,
      inheritance_pattern to annotated_variants
    - Variants without gene_symbol (no VEP match) get no phenotype data
    - gene_phenotype_matched count is tracked in AnnotationEngineResult
    """

    def test_lookup_gene_phenotype_returns_fields(self, reference_engine: sa.Engine) -> None:
        """_lookup_gene_phenotype returns phenotype fields keyed by rsid."""
        vep_data = {
            "rs1": {"gene_symbol": "BRCA1", "consequence": "missense_variant"},
            "rs2": {"gene_symbol": "CFTR", "consequence": "missense_variant"},
        }
        result = _lookup_gene_phenotype(vep_data, reference_engine)

        assert "rs1" in result
        assert result["rs1"]["disease_name"] == "Hereditary breast and ovarian cancer syndrome"
        assert result["rs1"]["disease_id"] == "MONDO:0011450"
        assert result["rs1"]["phenotype_source"] == "mondo_hpo"
        assert result["rs1"]["inheritance_pattern"] == "Autosomal dominant"
        assert result["rs1"]["hpo_terms"] is not None  # JSON string

        assert "rs2" in result
        assert result["rs2"]["disease_name"] == "Cystic fibrosis"
        assert result["rs2"]["inheritance_pattern"] == "Autosomal recessive"

    def test_lookup_prefers_an_inheritance_only_disease_over_an_unmatched_one(
        self, reference_engine: sa.Engine
    ) -> None:
        """A disease carrying only inheritance still counts as resolved (#2163).

        Parsing lifts a disease whose sole HPO term is an inheritance term out of
        ``hpo_terms`` and into ``inheritance``. Selecting the summary row on HPO
        terms alone therefore skipped exactly that disease and fell back to the
        first association, which may carry nothing at all — dropping the
        disease-scoped inheritance this annotation exists to surface.
        """
        with reference_engine.begin() as conn:
            conn.execute(gene_phenotype.delete().where(gene_phenotype.c.gene_symbol == "BRCA1"))
            conn.execute(
                gene_phenotype.insert(),
                [
                    {
                        "gene_symbol": "BRCA1",
                        "disease_name": "Unmatched placeholder disease",
                        "disease_id": None,
                        "hpo_terms": json.dumps([]),
                        "source": "mondo_hpo",
                        "inheritance": None,
                    },
                    {
                        "gene_symbol": "BRCA1",
                        "disease_name": "Inheritance-only disease",
                        "disease_id": "MONDO:0000001",
                        "hpo_terms": json.dumps([]),
                        "source": "mondo_hpo",
                        "inheritance": "Autosomal dominant",
                    },
                ],
            )

        result = _lookup_gene_phenotype(
            {"rs1": {"gene_symbol": "BRCA1", "consequence": "missense_variant"}},
            reference_engine,
        )

        assert result["rs1"]["disease_name"] == "Inheritance-only disease"
        assert result["rs1"]["disease_id"] == "MONDO:0000001"
        assert result["rs1"]["inheritance_pattern"] == "Autosomal dominant"

    def test_lookup_labelled_hpo_storage_keeps_sample_id_array(
        self, reference_engine: sa.Engine
    ) -> None:
        """Structured reference terms remain ID strings in the sample database."""
        with reference_engine.begin() as conn:
            conn.execute(
                gene_phenotype.update()
                .where(gene_phenotype.c.gene_symbol == "BRCA1")
                .values(hpo_terms=json.dumps([{"id": "HP:0003002", "name": "Breast carcinoma"}]))
            )

        result = _lookup_gene_phenotype(
            {"rs1": {"gene_symbol": "BRCA1", "consequence": "missense_variant"}},
            reference_engine,
        )

        assert json.loads(result["rs1"]["hpo_terms"]) == ["HP:0003002"]

    def test_lookup_gene_phenotype_no_gene_symbol(self, reference_engine: sa.Engine) -> None:
        """Variants without gene_symbol in VEP data are skipped."""
        vep_data = {"rs1": {"consequence": "intergenic_variant"}}
        result = _lookup_gene_phenotype(vep_data, reference_engine)
        assert len(result) == 0

    def test_lookup_gene_phenotype_unmatched_gene(self, reference_engine: sa.Engine) -> None:
        """Gene not in gene_phenotype table returns no result."""
        vep_data = {"rs1": {"gene_symbol": "NONEXISTENT_GENE"}}
        result = _lookup_gene_phenotype(vep_data, reference_engine)
        assert "rs1" not in result

    def test_lookup_gene_phenotype_empty_vep(self, reference_engine: sa.Engine) -> None:
        """Empty VEP data returns empty dict."""
        result = _lookup_gene_phenotype({}, reference_engine)
        assert len(result) == 0

    def test_merge_includes_gene_phenotype_bit(self) -> None:
        """Gene-phenotype data in merge produces GENE_PHENOTYPE_BIT in bitmask."""
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text("CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, genotype TEXT)")
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs1', '1', 100, 'AG')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        vep = {"rs1": {"gene_symbol": "BRCA1"}}
        gp = {"rs1": {"disease_name": "HBOC", "phenotype_source": "mondo_hpo"}}

        merged = _merge_annotations([row], vep, {}, {}, {}, gp)
        assert len(merged) == 1
        assert merged[0]["annotation_coverage"] == VEP_BIT | GENE_PHENOTYPE_BIT
        assert merged[0]["disease_name"] == "HBOC"

    @pytest.mark.parametrize(
        ("significance", "label_expected"),
        [
            ("Pathogenic", True),
            ("Likely pathogenic", True),
            ("Uncertain significance", True),  # VUS keeps gene context
            ("risk_factor", True),
            (None, True),  # unclassified keeps gene context
            ("Benign", False),
            ("Likely benign", False),
            ("Benign/Likely benign", False),
            ("Likely_benign", False),  # raw ClinVar VCF spelling
            ("Benign/Likely_benign", False),  # underscore combined form
            ("benign", False),  # lowercase fixture form
            ("likely_benign", False),  # lowercase + underscore
        ],
    )
    def test_merge_gene_phenotype_gated_on_pathogenicity(
        self, significance: str | None, label_expected: bool
    ) -> None:
        """F22: a benign variant must not inherit its gene's disease label."""
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(
                sa.text("CREATE TABLE t (rsid TEXT, chrom TEXT, pos INTEGER, genotype TEXT)")
            )
            conn.execute(sa.text("INSERT INTO t VALUES ('rs1', '13', 100, 'GA')"))
            row = conn.execute(sa.text("SELECT * FROM t")).fetchone()

        vep = {"rs1": {"gene_symbol": "BRCA2"}}
        clinvar = {"rs1": {"clinvar_significance": significance}} if significance else {}
        gp = {"rs1": {"disease_name": "breast-ovarian cancer susceptibility 2"}}

        merged = _merge_annotations([row], vep, clinvar, {}, {}, gp)
        assert len(merged) == 1
        has_bit = bool(merged[0]["annotation_coverage"] & GENE_PHENOTYPE_BIT)
        if label_expected:
            assert merged[0].get("disease_name") == "breast-ovarian cancer susceptibility 2"
            assert has_bit
        else:
            assert merged[0].get("disease_name") is None
            assert not has_bit

    def test_gene_phenotype_fields_in_annotated_variants(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-15: Full pipeline writes gene-phenotype fields for known gene."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs429358")
            ).fetchone()

        assert row is not None
        # rs429358 → APOE gene → "Alzheimer disease susceptibility" in seed CSV
        assert row.disease_name == "Alzheimer disease susceptibility"
        assert row.disease_id == "MONDO:0004975"
        assert row.phenotype_source == "mondo_hpo"
        assert row.hpo_terms is not None
        # APOE has no inheritance in seed CSV
        # Bitmask has GENE_PHENOTYPE_BIT set
        assert row.annotation_coverage & GENE_PHENOTYPE_BIT == GENE_PHENOTYPE_BIT

    def test_brca1_phenotype_in_pipeline(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-15: BRCA1 variant gets correct disease name and inheritance."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs80357906")
            ).fetchone()

        assert row is not None
        assert row.gene_symbol == "BRCA1"
        assert row.disease_name == "Hereditary breast and ovarian cancer syndrome"
        assert row.inheritance_pattern == "Autosomal dominant"

    def test_mthfr_phenotype_in_pipeline(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-15: MTHFR variant gets correct phenotype data."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs1801133")
            ).fetchone()

        assert row is not None
        assert row.disease_name is not None
        assert row.phenotype_source == "mondo_hpo"
        assert row.inheritance_pattern == "Autosomal recessive"

    def test_gene_phenotype_matched_count(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """P2-15: gene_phenotype_matched is tracked in result."""
        result = run_annotation(sample_with_variants, mock_registry)
        assert result.gene_phenotype_matched > 0

    def test_single_summary_prefers_a_disease_that_actually_resolved(self, tmp_path: Path) -> None:
        # Only one association is persisted per variant, and
        # `lookup_gene_phenotypes` orders by disease ID -- which is lexicographic
        # and says nothing about which disease resolved. Taking annots[0]
        # unconditionally discarded real disease-scoped HPO terms whenever a
        # gene's alphabetically-first MONDO disease was one that did not map.
        # MONDO:0000001 sorts first and has no terms; MONDO:0009061 has them.
        from backend.annotation.engine import _lookup_gene_phenotype

        ref_db = tmp_path / "ref.db"
        engine = sa.create_engine(f"sqlite:///{ref_db}")
        reference_metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(
                gene_phenotype.insert(),
                [
                    {
                        "gene_symbol": "CFTR",
                        "disease_name": "Unmapped first-by-id disease",
                        "disease_id": "MONDO:0000001",
                        "hpo_terms": None,
                        "source": "mondo_hpo",
                        "inheritance": None,
                    },
                    {
                        "gene_symbol": "CFTR",
                        "disease_name": "Cystic fibrosis",
                        "disease_id": "MONDO:0009061",
                        "hpo_terms": json.dumps([{"id": "HP:0002110", "name": "Bronchiectasis"}]),
                        "source": "mondo_hpo",
                        "inheritance": "AR",
                    },
                ],
            )
        record_mondo_hpo_version(engine, version=f"20260801+{MONDO_HPO_INGESTION_REVISION}")

        result = _lookup_gene_phenotype({"rs1": {"gene_symbol": "CFTR"}}, engine)

        assert result["rs1"]["disease_id"] == "MONDO:0009061"
        assert result["rs1"]["inheritance_pattern"] == "AR"
        assert json.loads(result["rs1"]["hpo_terms"]) == ["HP:0002110"]

    def test_single_summary_falls_back_when_no_disease_resolved(self, tmp_path: Path) -> None:
        # Counterpart control: a gene whose diseases all lack terms must still be
        # summarised, and must still take the first by disease ID so the choice
        # stays deterministic.
        from backend.annotation.engine import _lookup_gene_phenotype

        ref_db = tmp_path / "ref2.db"
        engine = sa.create_engine(f"sqlite:///{ref_db}")
        reference_metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(
                gene_phenotype.insert(),
                [
                    {
                        "gene_symbol": "CFTR",
                        "disease_name": "First by id",
                        "disease_id": "MONDO:0000001",
                        "hpo_terms": None,
                        "source": "mondo_hpo",
                        "inheritance": None,
                    },
                    {
                        "gene_symbol": "CFTR",
                        "disease_name": "Second by id",
                        "disease_id": "MONDO:0009061",
                        "hpo_terms": None,
                        "source": "mondo_hpo",
                        "inheritance": None,
                    },
                ],
            )
        record_mondo_hpo_version(engine, version=f"20260801+{MONDO_HPO_INGESTION_REVISION}")

        result = _lookup_gene_phenotype({"rs1": {"gene_symbol": "CFTR"}}, engine)

        assert result["rs1"]["disease_id"] == "MONDO:0000001"

    def test_gene_phenotype_columns_in_upsert(self) -> None:
        """P2-15: Gene-phenotype columns are in _UPSERT_COLUMNS."""
        from backend.annotation.engine import _UPSERT_COLUMNS

        assert "disease_name" in _UPSERT_COLUMNS
        assert "disease_id" in _UPSERT_COLUMNS
        assert "phenotype_source" in _UPSERT_COLUMNS
        assert "hpo_terms" in _UPSERT_COLUMNS
        assert "inheritance_pattern" in _UPSERT_COLUMNS

    def test_gene_phenotype_bit_constant(self) -> None:
        """P2-15: GENE_PHENOTYPE_BIT is bit 4 (value 16)."""
        assert GENE_PHENOTYPE_BIT == 0b010000
        assert GENE_PHENOTYPE_BIT == 16

    def test_no_phenotype_for_unmatched_gene(
        self,
        sample_with_variants: sa.Engine,
        mock_registry: MagicMock,
    ) -> None:
        """Variants whose gene is not in gene_phenotype table get no phenotype data."""
        run_annotation(sample_with_variants, mock_registry)

        with sample_with_variants.connect() as conn:
            rows = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.disease_name.is_(None))
            ).fetchall()

        # Some variants should have no phenotype data (gene not in seed CSV
        # or no VEP gene_symbol)
        # At minimum, rs_nomatch should not appear at all (no annotations)
        # but rs12913832 (HERC2) IS in seed CSV, so most VEP-matched variants
        # will have gene-phenotype data
        for row in rows:
            assert row.annotation_coverage & GENE_PHENOTYPE_BIT == 0
