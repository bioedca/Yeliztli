"""Tests for ClinVar annotation lookup (P1-11).

Covers:
- lookup_clinvar_by_rsids: batch rsid matching
- lookup_clinvar_by_positions: (chrom, pos) fallback matching
- annotate_sample_clinvar: full end-to-end annotation pipeline
- Bitmask OR logic for annotation_coverage
- Edge cases: empty inputs, no matches, duplicate rsids, re-annotation
"""

from __future__ import annotations

import sqlalchemy as sa

from backend.annotation.clinvar import (
    CLINVAR_BITMASK,
    AnnotationResult,
    annotate_sample_clinvar,
    lookup_clinvar_by_positions,
    lookup_clinvar_by_rsids,
)
from backend.db.tables import annotated_variants, clinvar_variants, raw_variants
from tests.backend.conftest import SEED_RAW_VARIANTS

# ═══════════════════════════════════════════════════════════════════════
# lookup_clinvar_by_rsids
# ═══════════════════════════════════════════════════════════════════════


class TestLookupByRsids:
    """Tests for rsid-based ClinVar lookup."""

    def test_single_rsid_match(self, seeded_reference_engine: sa.Engine) -> None:
        result = lookup_clinvar_by_rsids(["rs429358"], seeded_reference_engine)
        assert len(result) == 1
        annot = result["rs429358"]
        assert annot.clinvar_significance == "risk_factor"
        assert annot.clinvar_review_stars == 3
        assert annot.clinvar_accession == "VCV000017864"
        assert annot.clinvar_conditions == "Alzheimer disease"
        assert annot.matched_by == "rsid"

    def test_multiple_rsids(self, seeded_reference_engine: sa.Engine) -> None:
        rsids = ["rs429358", "rs7412", "rs1801133", "rs4680"]
        result = lookup_clinvar_by_rsids(rsids, seeded_reference_engine)
        assert len(result) == 4
        assert all(r.matched_by == "rsid" for r in result.values())

    def test_no_match(self, seeded_reference_engine: sa.Engine) -> None:
        result = lookup_clinvar_by_rsids(["rs999999999"], seeded_reference_engine)
        assert len(result) == 0

    def test_partial_match(self, seeded_reference_engine: sa.Engine) -> None:
        rsids = ["rs429358", "rs999999999"]
        result = lookup_clinvar_by_rsids(rsids, seeded_reference_engine)
        assert len(result) == 1
        assert "rs429358" in result

    def test_empty_input(self, seeded_reference_engine: sa.Engine) -> None:
        result = lookup_clinvar_by_rsids([], seeded_reference_engine)
        assert result == {}

    def test_pathogenic_variant(self, seeded_reference_engine: sa.Engine) -> None:
        result = lookup_clinvar_by_rsids(["rs80357906"], seeded_reference_engine)
        annot = result["rs80357906"]
        assert annot.clinvar_significance == "Pathogenic"
        assert annot.clinvar_review_stars == 3
        assert annot.clinvar_conditions == "Hereditary breast and ovarian cancer syndrome"

    def test_vus_variant(self, seeded_reference_engine: sa.Engine) -> None:
        result = lookup_clinvar_by_rsids(["rs12345"], seeded_reference_engine)
        annot = result["rs12345"]
        assert annot.clinvar_significance == "Uncertain_significance"
        assert annot.clinvar_review_stars == 1

    def test_duplicate_rsids_in_reference_picks_highest_stars(
        self, reference_engine: sa.Engine
    ) -> None:
        """When ClinVar has multiple entries for the same rsid, pick highest review_stars."""
        with reference_engine.begin() as conn:
            conn.execute(
                clinvar_variants.insert(),
                [
                    {
                        "rsid": "rs100",
                        "chrom": "1",
                        "pos": 1000,
                        "ref": "A",
                        "alt": "G",
                        "significance": "Benign",
                        "review_stars": 1,
                        "accession": "VCV000000001",
                        "conditions": "Condition A",
                        "gene_symbol": "GENE1",
                        "variation_id": 1,
                    },
                    {
                        "rsid": "rs100",
                        "chrom": "1",
                        "pos": 1000,
                        "ref": "A",
                        "alt": "G",
                        "significance": "Pathogenic",
                        "review_stars": 3,
                        "accession": "VCV000000002",
                        "conditions": "Condition B",
                        "gene_symbol": "GENE1",
                        "variation_id": 2,
                    },
                ],
            )

        result = lookup_clinvar_by_rsids(["rs100"], reference_engine)
        assert len(result) == 1
        assert result["rs100"].clinvar_significance == "Pathogenic"
        assert result["rs100"].clinvar_review_stars == 3

    def test_genotype_aware_selection_at_multiallelic_site(
        self, reference_engine: sa.Engine
    ) -> None:
        """At a multi-allelic site, the record whose ALT the sample carries is
        preferred over a higher-star record it does not carry."""
        with reference_engine.begin() as conn:
            conn.execute(
                clinvar_variants.insert(),
                [
                    {
                        "rsid": "rs_multi",
                        "chrom": "1",
                        "pos": 2000,
                        "ref": "C",
                        "alt": "G",
                        "significance": "Pathogenic",
                        "review_stars": 3,
                        "accession": "VCV000000010",
                        "conditions": "Condition G",
                        "gene_symbol": "GENE1",
                        "variation_id": 10,
                    },
                    {
                        "rsid": "rs_multi",
                        "chrom": "1",
                        "pos": 2000,
                        "ref": "C",
                        "alt": "T",
                        "significance": "Pathogenic",
                        "review_stars": 2,
                        "accession": "VCV000000011",
                        "conditions": "Condition T",
                        "gene_symbol": "GENE1",
                        "variation_id": 11,
                    },
                ],
            )

        # No genotype → highest review_stars wins (alt G).
        res = lookup_clinvar_by_rsids(["rs_multi"], reference_engine)
        assert res["rs_multi"].alt == "G"

        # Genotype CT carries the alt T record (lower stars) → it is chosen.
        res2 = lookup_clinvar_by_rsids(
            ["rs_multi"], reference_engine, genotype_by_rsid={"rs_multi": "CT"}
        )
        assert res2["rs_multi"].alt == "T"
        assert res2["rs_multi"].clinvar_conditions == "Condition T"

    def test_large_batch_exceeding_sqlite_limit(self, seeded_reference_engine: sa.Engine) -> None:
        """Test with >500 rsids to verify batching logic."""
        # Generate 600 fake rsids, but include known seed rsids
        rsids = [f"rs{i}" for i in range(1, 598)]
        rsids.extend(["rs429358", "rs7412", "rs1801133"])
        assert len(rsids) == 600
        result = lookup_clinvar_by_rsids(rsids, seeded_reference_engine)
        assert "rs429358" in result
        assert "rs7412" in result
        assert "rs1801133" in result


# ═══════════════════════════════════════════════════════════════════════
# lookup_clinvar_by_positions
# ═══════════════════════════════════════════════════════════════════════


class TestLookupByPositions:
    """Tests for (chrom, pos) fallback ClinVar lookup."""

    def test_single_position_match(self, seeded_reference_engine: sa.Engine) -> None:
        positions = [("19", 44908684, "i6025323")]
        result = lookup_clinvar_by_positions(positions, seeded_reference_engine)
        assert len(result) == 1
        annot = result["i6025323"]
        assert annot.clinvar_significance == "risk_factor"
        assert annot.matched_by == "chrom_pos"

    def test_multiple_positions(self, seeded_reference_engine: sa.Engine) -> None:
        positions = [
            ("19", 44908684, "i6025323"),
            ("1", 11856378, "i4000001"),
        ]
        result = lookup_clinvar_by_positions(positions, seeded_reference_engine)
        assert len(result) == 2
        assert all(r.matched_by == "chrom_pos" for r in result.values())

    def test_no_match(self, seeded_reference_engine: sa.Engine) -> None:
        positions = [("99", 999999999, "rs_fake")]
        result = lookup_clinvar_by_positions(positions, seeded_reference_engine)
        assert len(result) == 0

    def test_empty_input(self, seeded_reference_engine: sa.Engine) -> None:
        result = lookup_clinvar_by_positions([], seeded_reference_engine)
        assert result == {}

    def test_preserves_sample_rsid_as_key(self, seeded_reference_engine: sa.Engine) -> None:
        """Result should use the sample's rsid, not the ClinVar rsid."""
        positions = [("19", 44908684, "i_custom_id")]
        result = lookup_clinvar_by_positions(positions, seeded_reference_engine)
        assert "i_custom_id" in result
        assert result["i_custom_id"].rsid == "i_custom_id"

    def test_null_reference_rsid_position_match(self, reference_engine: sa.Engine) -> None:
        """Coordinate fallback can recover ClinVar records with no dbSNP rsid."""
        with reference_engine.begin() as conn:
            conn.execute(
                clinvar_variants.insert(),
                [
                    {
                        "rsid": None,
                        "chrom": "2",
                        "pos": 47630325,
                        "ref": "TTCGACATGGCG",
                        "alt": "T",
                        "significance": "Pathogenic",
                        "review_stars": 1,
                        "accession": "VCV001075377",
                        "conditions": "Lynch syndrome",
                        "gene_symbol": "MSH2",
                        "variation_id": 1075377,
                    }
                ],
            )

        result = lookup_clinvar_by_positions(
            [("2", 47630325, "sample_msh2_del")],
            reference_engine,
            genotype_by_rsid={"sample_msh2_del": "ID"},
        )

        assert result["sample_msh2_del"].rsid == "sample_msh2_del"
        assert result["sample_msh2_del"].clinvar_significance == "Pathogenic"
        assert result["sample_msh2_del"].clinvar_accession == "VCV001075377"
        assert result["sample_msh2_del"].matched_by == "chrom_pos"


# ═══════════════════════════════════════════════════════════════════════
# annotate_sample_clinvar (end-to-end)
# ═══════════════════════════════════════════════════════════════════════


class TestAnnotateSampleClinvar:
    """End-to-end tests for the full ClinVar annotation pipeline."""

    def test_basic_annotation(
        self,
        sample_with_variants: sa.Engine,
        seeded_reference_engine: sa.Engine,
    ) -> None:
        result = annotate_sample_clinvar(sample_with_variants, seeded_reference_engine)

        assert isinstance(result, AnnotationResult)
        assert result.total_variants == len(SEED_RAW_VARIANTS)
        # rs429358, rs7412, rs1801133, rs4680, rs12345 match by rsid (5 from SEED_CLINVAR)
        # rs12913832, rs7903146 also match by rsid (in mini_clinvar but also seeded)
        assert result.matched_by_rsid >= 5
        assert result.total_matched > 0
        assert result.rows_written == result.total_matched

    def test_annotated_variants_populated(
        self,
        sample_with_variants: sa.Engine,
        seeded_reference_engine: sa.Engine,
    ) -> None:
        annotate_sample_clinvar(sample_with_variants, seeded_reference_engine)

        with sample_with_variants.connect() as conn:
            rows = conn.execute(
                sa.select(annotated_variants).order_by(annotated_variants.c.rsid)
            ).fetchall()

        rsids = {r.rsid for r in rows}
        # At minimum, the 5 ClinVar-seeded variants should be present
        assert "rs429358" in rsids
        assert "rs7412" in rsids
        assert "rs1801133" in rsids
        assert "rs4680" in rsids
        assert "rs12345" in rsids

    def test_clinvar_columns_correct(
        self,
        sample_with_variants: sa.Engine,
        seeded_reference_engine: sa.Engine,
    ) -> None:
        annotate_sample_clinvar(sample_with_variants, seeded_reference_engine)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs429358")
            ).first()

        assert row is not None
        assert row.clinvar_significance == "risk_factor"
        assert row.clinvar_review_stars == 3
        assert row.clinvar_accession == "VCV000017864"
        assert row.clinvar_conditions == "Alzheimer disease"
        assert row.chrom == "19"
        assert row.pos == 44908684
        assert row.genotype == "TC"

    def test_zygosity_and_alleles_populated(
        self,
        sample_with_variants: sa.Engine,
        seeded_reference_engine: sa.Engine,
    ) -> None:
        """Annotation records ClinVar ref/alt and a computed zygosity so
        downstream modules can gate on actual carriage (carriage-bug fix)."""
        annotate_sample_clinvar(sample_with_variants, seeded_reference_engine)

        with sample_with_variants.connect() as conn:
            rows = {
                r.rsid: r
                for r in conn.execute(
                    sa.select(
                        annotated_variants.c.rsid,
                        annotated_variants.c.ref,
                        annotated_variants.c.alt,
                        annotated_variants.c.zygosity,
                    )
                ).fetchall()
            }

        # rs429358: genotype "TC" vs ClinVar ref T / alt C → carrier (het).
        assert rows["rs429358"].ref == "T"
        assert rows["rs429358"].alt == "C"
        assert rows["rs429358"].zygosity == "het"
        # rs7412: genotype "CC" vs ClinVar ref C / alt T → homozygous reference
        # (does NOT carry the variant — must not surface as a finding).
        assert rows["rs7412"].zygosity == "hom_ref"
        # rs12345: genotype "AA" vs ClinVar ref A / alt G → homozygous reference.
        assert rows["rs12345"].zygosity == "hom_ref"

    def test_indel_match_has_null_zygosity(
        self,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """An indel ClinVar record (multi-base ref) is unscoreable on a chip,
        so zygosity is NULL even though the position matches."""
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [{"rsid": "rs777", "chrom": "5", "pos": 500, "genotype": "II"}],
            )
        with reference_engine.begin() as conn:
            conn.execute(
                clinvar_variants.insert(),
                [
                    {
                        "rsid": "rs777",
                        "chrom": "5",
                        "pos": 500,
                        "ref": "CTC",
                        "alt": "C",
                        "significance": "Pathogenic",
                        "review_stars": 3,
                        "accession": "VCV000000777",
                        "conditions": "Some condition",
                        "gene_symbol": "APC",
                        "variation_id": 777,
                    }
                ],
            )

        annotate_sample_clinvar(sample_engine, reference_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants.c.zygosity, annotated_variants.c.ref).where(
                    annotated_variants.c.rsid == "rs777"
                )
            ).first()

        assert row is not None
        assert row.ref == "CTC"  # alleles still recorded
        assert row.zygosity is None  # but carriage is unscoreable

    def test_multiallelic_carriage_aware_annotation(
        self,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """End-to-end: at a multi-allelic site the sample is annotated against
        the allele it carries, not merely the highest-star record."""
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [{"rsid": "rs_multi", "chrom": "1", "pos": 2000, "genotype": "CT"}],
            )
        with reference_engine.begin() as conn:
            conn.execute(
                clinvar_variants.insert(),
                [
                    {
                        "rsid": "rs_multi",
                        "chrom": "1",
                        "pos": 2000,
                        "ref": "C",
                        "alt": "G",  # NOT carried, higher stars
                        "significance": "Pathogenic",
                        "review_stars": 3,
                        "accession": "VCV000000010",
                        "conditions": "Condition G",
                        "gene_symbol": "GENE1",
                        "variation_id": 10,
                    },
                    {
                        "rsid": "rs_multi",
                        "chrom": "1",
                        "pos": 2000,
                        "ref": "C",
                        "alt": "T",  # carried by genotype "CT"
                        "significance": "Pathogenic",
                        "review_stars": 2,
                        "accession": "VCV000000011",
                        "conditions": "Condition T",
                        "gene_symbol": "GENE1",
                        "variation_id": 11,
                    },
                ],
            )

        annotate_sample_clinvar(sample_engine, reference_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs_multi")
            ).first()

        assert row is not None
        assert row.alt == "T"  # scored against the carried allele
        assert row.zygosity == "het"
        assert row.clinvar_conditions == "Condition T"

    def test_annotation_coverage_bitmask(
        self,
        sample_with_variants: sa.Engine,
        seeded_reference_engine: sa.Engine,
    ) -> None:
        annotate_sample_clinvar(sample_with_variants, seeded_reference_engine)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs429358"
                )
            ).first()

        assert row is not None
        assert row.annotation_coverage & CLINVAR_BITMASK == CLINVAR_BITMASK

    def test_bitmask_or_preserves_existing_bits(
        self,
        sample_with_variants: sa.Engine,
        seeded_reference_engine: sa.Engine,
    ) -> None:
        """If a variant already has annotation_coverage from another source,
        the ClinVar bit should be OR'd in without erasing existing bits."""
        # Pre-populate with VEP bit (bit 0 = 1)
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        with sample_with_variants.begin() as conn:
            stmt = sqlite_insert(annotated_variants).values(
                rsid="rs429358",
                chrom="19",
                pos=44908684,
                genotype="TC",
                annotation_coverage=0b000001,  # VEP bit set
            )
            conn.execute(stmt)

        annotate_sample_clinvar(sample_with_variants, seeded_reference_engine)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs429358"
                )
            ).first()

        assert row is not None
        # Both VEP (bit 0) and ClinVar (bit 1) should be set
        assert row.annotation_coverage == 0b000011

    def test_empty_sample(
        self,
        sample_engine: sa.Engine,
        seeded_reference_engine: sa.Engine,
    ) -> None:
        """Annotating a sample with no raw variants returns zero counts."""
        result = annotate_sample_clinvar(sample_engine, seeded_reference_engine)
        assert result.total_variants == 0
        assert result.total_matched == 0
        assert result.rows_written == 0

    def test_no_clinvar_data(
        self,
        sample_with_variants: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Annotating against an empty ClinVar table produces no matches."""
        result = annotate_sample_clinvar(sample_with_variants, reference_engine)
        assert result.total_variants == len(SEED_RAW_VARIANTS)
        assert result.matched_by_rsid == 0
        assert result.matched_by_position == 0
        assert result.not_matched == len(SEED_RAW_VARIANTS)
        assert result.rows_written == 0

    def test_chrom_pos_fallback(
        self,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """Variants with i-prefixed rsids should match by (chrom, pos)."""
        # Insert a raw variant with an i-prefix rsid
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [
                    {
                        "rsid": "i6025323",
                        "chrom": "19",
                        "pos": 44908684,
                        "genotype": "TC",
                    },
                ],
            )

        # Insert ClinVar data with a different rsid but same position
        with reference_engine.begin() as conn:
            conn.execute(
                clinvar_variants.insert(),
                [
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
                ],
            )

        result = annotate_sample_clinvar(sample_engine, reference_engine)
        assert result.matched_by_rsid == 0
        assert result.matched_by_position == 1
        assert result.total_matched == 1

        # Verify the annotation was written with the sample's rsid
        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "i6025323")
            ).first()

        assert row is not None
        assert row.clinvar_significance == "risk_factor"
        assert row.annotation_coverage & CLINVAR_BITMASK == CLINVAR_BITMASK

    def test_chrom_pos_fallback_with_null_reference_rsid(
        self,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ) -> None:
        """End-to-end annotation writes no-rsID ClinVar records by sample key."""
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [
                    {
                        "rsid": "sample_msh2_del",
                        "chrom": "2",
                        "pos": 47630325,
                        "genotype": "ID",
                    },
                ],
            )

        with reference_engine.begin() as conn:
            conn.execute(
                clinvar_variants.insert(),
                [
                    {
                        "rsid": None,
                        "chrom": "2",
                        "pos": 47630325,
                        "ref": "TTCGACATGGCG",
                        "alt": "T",
                        "significance": "Pathogenic",
                        "review_stars": 1,
                        "accession": "VCV001075377",
                        "conditions": "Lynch syndrome",
                        "gene_symbol": "MSH2",
                        "variation_id": 1075377,
                    },
                ],
            )

        result = annotate_sample_clinvar(sample_engine, reference_engine)

        assert result.matched_by_rsid == 0
        assert result.matched_by_position == 1
        assert result.rows_written == 1

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "sample_msh2_del")
            ).first()

        assert row is not None
        assert row.clinvar_significance == "Pathogenic"
        assert row.clinvar_accession == "VCV001075377"
        assert row.ref == "TTCGACATGGCG"
        assert row.alt == "T"
        assert row.zygosity is None

    def test_re_annotation_updates_existing(
        self,
        sample_with_variants: sa.Engine,
        seeded_reference_engine: sa.Engine,
    ) -> None:
        """Running annotation twice should update, not duplicate."""
        result1 = annotate_sample_clinvar(sample_with_variants, seeded_reference_engine)
        result2 = annotate_sample_clinvar(sample_with_variants, seeded_reference_engine)

        # Same counts both times
        assert result1.rows_written == result2.rows_written

        # No duplicate rows and bitmask stable
        with sample_with_variants.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count()).select_from(annotated_variants)
            ).scalar()
            row = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs429358"
                )
            ).first()

        assert count == result1.rows_written
        assert row is not None
        assert row.annotation_coverage == CLINVAR_BITMASK

    def test_unmatched_variants_not_in_annotated(
        self,
        sample_with_variants: sa.Engine,
        seeded_reference_engine: sa.Engine,
    ) -> None:
        """Variants with no ClinVar match should NOT appear in annotated_variants."""
        annotate_sample_clinvar(sample_with_variants, seeded_reference_engine)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs1805007")
            ).first()

        # rs1805007 is not in SEED_CLINVAR, so it shouldn't be in annotated_variants
        assert row is None


# ═══════════════════════════════════════════════════════════════════════
# AnnotationResult dataclass
# ═══════════════════════════════════════════════════════════════════════


class TestAnnotationResult:
    def test_total_matched_property(self) -> None:
        r = AnnotationResult(matched_by_rsid=5, matched_by_position=3)
        assert r.total_matched == 8

    def test_defaults(self) -> None:
        r = AnnotationResult()
        assert r.total_variants == 0
        assert r.matched_by_rsid == 0
        assert r.matched_by_position == 0
        assert r.not_matched == 0
        assert r.rows_written == 0
        assert r.total_matched == 0
