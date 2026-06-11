"""GTEx eQTL ingestion + regulatory-context badge (SW-F3).

Validates the GRCh38 variant_id → rsID mapping (no liftover), the eQTL ingestion
guardrails (no-rsID skip, bad-row skip, empty-parse guard, per-tissue clear), and
the context-only badge (never ACMG evidence).
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.gtex import GTEX_PMID, eqtl_regulatory_context
from backend.annotation.gtex_eqtl import (
    create_gtex_tables,
    load_gtex_eqtl,
    load_variant_rsid_lookup,
    lookup_eqtls_by_rsids,
    parse_variant_id,
)

# GTEx WGS lookup table (variant_id → rsID). Last variant has no rsID ('.').
_LOOKUP = (
    "chr\tvariant_pos\tvariant_id\tref\talt\tnum_alt_per_site\trs_id_dbSNP151_GRCh38p7\n"
    "chr1\t64764\tchr1_64764_C_T_b38\tC\tT\t1\trs769952832\n"
    "chr2\t100\tchr2_100_A_G_b38\tA\tG\t1\trs2222\n"
    "chr3\t200\tchr3_200_G_A_b38\tG\tA\t1\t.\n"
)

# Significant variant-gene pairs (GTEx format). Row 3 has no rsID in the lookup;
# row 4 is a malformed short row.
_SIGNIF = (
    "variant_id\tgene_id\ttss_distance\tmaf\tpval_nominal\tslope\tslope_se\n"
    "chr1_64764_C_T_b38\tENSG001\t-1000\t0.2\t1e-12\t0.45\t0.05\n"
    "chr2_100_A_G_b38\tENSG002\t500\t0.3\t3e-8\t-0.22\t0.04\n"
    "chr3_200_G_A_b38\tENSG003\t10\t0.1\t1e-9\t0.30\t0.06\n"  # no rsID → skipped
    "malformed_row\n"  # bad → skipped
)


def _engine(tmp_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{tmp_path}/gtex.db")


def _write(tmp_path: Path, name: str, content: str, gz: bool = False) -> Path:
    p = tmp_path / name
    if gz:
        with gzip.open(p, "wt", encoding="utf-8") as fh:
            fh.write(content)
    else:
        p.write_text(content, encoding="utf-8")
    return p


class TestParseVariantId:
    def test_valid_b38(self) -> None:
        assert parse_variant_id("chr1_64764_C_T_b38") == ("1", 64764)
        assert parse_variant_id("chrX_5_A_G_b38") == ("X", 5)

    def test_invalid(self) -> None:
        assert parse_variant_id("rs123") is None
        assert parse_variant_id("1_64764_C_T") is None


class TestLookup:
    def test_loads_and_skips_missing_rsid(self, tmp_path: Path) -> None:
        f = _write(tmp_path, "lookup.txt", _LOOKUP)
        lk = load_variant_rsid_lookup(f)
        assert lk["chr1_64764_C_T_b38"] == "rs769952832"
        assert lk["chr2_100_A_G_b38"] == "rs2222"
        assert "chr3_200_G_A_b38" not in lk  # '.' rsID omitted


class TestIngestion:
    def _load(self, tmp_path: Path) -> tuple[sa.Engine, object]:
        lk = load_variant_rsid_lookup(_write(tmp_path, "lookup.txt", _LOOKUP))
        sig = _write(tmp_path, "Whole_Blood.v8.signif_variant_gene_pairs.txt.gz", _SIGNIF, gz=True)
        engine = _engine(tmp_path)
        stats = load_gtex_eqtl(sig, lk, "Whole_Blood", engine)
        return engine, stats

    def test_loads_with_rsid_mapping(self, tmp_path: Path) -> None:
        engine, stats = self._load(tmp_path)
        assert stats.loaded == 2  # rows 1 & 2; row 3 no-rsid, row 4 malformed
        assert stats.skipped_no_rsid == 1
        assert stats.skipped_bad_row == 1
        hits = lookup_eqtls_by_rsids(["rs769952832", "rs2222"], engine)
        assert hits["rs769952832"][0]["gene_id"] == "ENSG001"
        assert hits["rs769952832"][0]["chrom"] == "1" and hits["rs769952832"][0]["pos"] == 64764
        assert hits["rs2222"][0]["slope"] == -0.22

    def test_empty_parse_raises(self, tmp_path: Path) -> None:
        # A signif file whose variants are all absent from the lookup → 0 rows.
        sig = _write(tmp_path, "x.txt", _SIGNIF.replace("chr1_64764_C_T_b38", "chr9_9_C_T_b38"))
        engine = _engine(tmp_path)
        with pytest.raises(ValueError, match="zero eQTL rows"):
            load_gtex_eqtl(sig, {}, "Brain", engine)

    def test_reload_clears_tissue(self, tmp_path: Path) -> None:
        engine, _ = self._load(tmp_path)
        lk = load_variant_rsid_lookup(_write(tmp_path, "lookup2.txt", _LOOKUP))
        sig = _write(tmp_path, "wb2.txt", _SIGNIF)
        load_gtex_eqtl(sig, lk, "Whole_Blood", engine)  # re-load same tissue
        with engine.connect() as conn:
            n = conn.execute(
                sa.select(sa.func.count()).select_from(sa.table("gtex_eqtl"))
            ).scalar()
        assert n == 2  # not duplicated

    def test_create_tables_idempotent_and_usable(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        create_gtex_tables(engine)
        create_gtex_tables(engine)  # second call must not error
        # Table exists and is usable: an insert + count round-trips.
        from backend.annotation.gtex_eqtl import gtex_eqtl

        with engine.begin() as conn:
            conn.execute(gtex_eqtl.insert().values(rsid="rsX", gene_id="ENSG", tissue="T", pos=1))
        with engine.connect() as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(gtex_eqtl)).scalar() == 1


class TestRegulatoryContext:
    def test_none_when_empty(self) -> None:
        assert eqtl_regulatory_context("rs1", []) is None

    def test_summarizes_and_is_not_acmg(self) -> None:
        eqtls = [
            {"gene_id": "ENSG001", "tissue": "Whole_Blood", "pval_nominal": 1e-12, "slope": 0.4},
            {"gene_id": "ENSG002", "tissue": "Liver", "pval_nominal": 3e-8, "slope": -0.2},
        ]
        ctx = eqtl_regulatory_context("rs769952832", eqtls)
        assert ctx["gene_ids"] == ["ENSG001", "ENSG002"]
        assert ctx["n_associations"] == 2
        assert ctx["top_gene_id"] == "ENSG001"  # smallest p-value
        assert ctx["acmg_evidence"] is False
        assert ctx["context_only"] is True
        assert GTEX_PMID in ctx["pmid_citations"]
