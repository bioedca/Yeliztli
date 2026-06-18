"""GTEx eQTL ingestion + regulatory-context badge (SW-F3).

Validates the GRCh38 variant_id → rsID mapping (no liftover), the eQTL ingestion
guardrails (no-rsID skip, bad-row skip, empty-parse guard, per-tissue clear), and
the context-only badge (never ACMG evidence).
"""

from __future__ import annotations

import gzip
import tarfile
from pathlib import Path

import pytest
import sqlalchemy as sa

import backend.annotation.gtex_eqtl as gtex_mod
from backend.analysis.gtex import GTEX_PMID, eqtl_regulatory_context
from backend.annotation.gtex_eqtl import (
    GTEX_VERSION,
    create_gtex_tables,
    download_and_load_gtex_eqtl,
    gtex_eqtl,
    load_gtex_eqtl,
    load_gtex_eqtl_dir,
    load_variant_rsid_lookup,
    lookup_eqtls_by_rsids,
    parse_variant_id,
)
from backend.db.tables import database_versions, reference_metadata

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


def _count_tissue_rows(engine: sa.Engine, tissue: str) -> int:
    with engine.connect() as conn:
        return conn.scalar(
            sa.select(sa.func.count()).select_from(gtex_eqtl).where(gtex_eqtl.c.tissue == tissue)
        )


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

    def test_loading_second_tissue_preserves_existing_tissue(self, tmp_path: Path) -> None:
        engine, _ = self._load(tmp_path)
        lk = load_variant_rsid_lookup(_write(tmp_path, "lookup2.txt", _LOOKUP))
        liver_sig = _write(tmp_path, "liver.txt", _SIGNIF)

        load_gtex_eqtl(liver_sig, lk, "Liver", engine)

        assert _count_tissue_rows(engine, "Whole_Blood") == 2
        assert _count_tissue_rows(engine, "Liver") == 2

    def test_create_tables_idempotent_and_usable(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        create_gtex_tables(engine)
        create_gtex_tables(engine)  # second call must not error
        # Table exists and is usable: an insert + count round-trips.
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


def _make_eqtl_tar(tmp_path: Path, tissue_to_signif: dict[str, str]) -> Path:
    """Build a synthetic ``GTEx_Analysis_v8_eQTL.tar`` with per-tissue signif files."""
    raw = tmp_path / "tar_src"
    raw.mkdir(exist_ok=True)
    members: list[Path] = []
    for tissue, content in tissue_to_signif.items():
        f = raw / f"{tissue}.v8.signif_variant_gene_pairs.txt.gz"
        with gzip.open(f, "wt", encoding="utf-8") as fh:
            fh.write(content)
        members.append(f)
    tar_path = tmp_path / "GTEx_Analysis_v8_eQTL.tar"
    with tarfile.open(tar_path, "w") as tf:
        for f in members:
            tf.add(f, arcname=f"GTEx_Analysis_v8_eQTL/{f.name}")
    return tar_path


class TestLoadDir:
    def test_loads_all_tissues_from_extracted_dir(self, tmp_path: Path) -> None:
        eqtl_dir = tmp_path / "ex"
        eqtl_dir.mkdir()
        _write(eqtl_dir, "Whole_Blood.v8.signif_variant_gene_pairs.txt.gz", _SIGNIF, gz=True)
        _write(eqtl_dir, "Liver.v8.signif_variant_gene_pairs.txt.gz", _SIGNIF, gz=True)
        lookup = _write(tmp_path, "lk.txt", _LOOKUP)
        engine = _engine(tmp_path)

        stats = load_gtex_eqtl_dir(eqtl_dir, lookup, engine)

        assert {s.tissue for s in stats} == {"Whole_Blood", "Liver"}
        assert _count_tissue_rows(engine, "Whole_Blood") == 2
        assert _count_tissue_rows(engine, "Liver") == 2

    def test_tissue_with_no_rsid_matches_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        # Whole_Blood matches the lookup; "Brain" has only off-lookup variants → its
        # per-tissue empty-parse ValueError is caught and the tissue skipped.
        brain = _SIGNIF.replace("chr1_64764_C_T_b38", "chr1_999_C_T_b38").replace(
            "chr2_100_A_G_b38", "chr2_999_A_G_b38"
        )
        eqtl_dir = tmp_path / "ex"
        eqtl_dir.mkdir()
        _write(eqtl_dir, "Whole_Blood.v8.signif_variant_gene_pairs.txt.gz", _SIGNIF, gz=True)
        _write(eqtl_dir, "Brain.v8.signif_variant_gene_pairs.txt.gz", brain, gz=True)
        lookup = _write(tmp_path, "lk.txt", _LOOKUP)
        engine = _engine(tmp_path)

        stats = load_gtex_eqtl_dir(eqtl_dir, lookup, engine)

        tissues = {s.tissue for s in stats}
        assert "Whole_Blood" in tissues
        assert "Brain" not in tissues

    def test_malformed_schema_file_aborts_not_skipped(self, tmp_path: Path) -> None:
        # A signif file missing the required variant_id/gene_id columns must abort
        # the build (re-raised), NOT be silently skipped like a zero-rsID tissue.
        eqtl_dir = tmp_path / "ex"
        eqtl_dir.mkdir()
        _write(
            eqtl_dir,
            "Bad.v8.signif_variant_gene_pairs.txt.gz",
            "foo\tbar\tpval_nominal\n1\t2\t3\n",
            gz=True,
        )
        lookup = _write(tmp_path, "lk.txt", _LOOKUP)
        engine = _engine(tmp_path)
        with pytest.raises(ValueError, match="missing variant_id/gene_id"):
            load_gtex_eqtl_dir(eqtl_dir, lookup, engine)

    def test_no_signif_files_refuses_to_build(self, tmp_path: Path) -> None:
        eqtl_dir = tmp_path / "empty"
        eqtl_dir.mkdir()
        (eqtl_dir / "README.txt").write_text("not an eqtl file", encoding="utf-8")
        lookup = _write(tmp_path, "lk.txt", _LOOKUP)
        engine = _engine(tmp_path)
        with pytest.raises(ValueError, match="no GTEx"):
            load_gtex_eqtl_dir(eqtl_dir, lookup, engine)


class TestDownloadAndLoad:
    def test_builds_db_and_records_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tar_path = _make_eqtl_tar(tmp_path, {"Whole_Blood": _SIGNIF, "Liver": _SIGNIF})
        lookup_path = _write(tmp_path, "lookup.txt", _LOOKUP)

        def fake_download(dest_dir: Path, **_kw: object) -> tuple[Path, Path]:
            return tar_path, lookup_path

        monkeypatch.setattr(gtex_mod, "_download_gtex_inputs", fake_download)

        engine = _engine(tmp_path)
        ref = sa.create_engine(f"sqlite:///{tmp_path}/reference.db")
        reference_metadata.create_all(ref)

        stats = download_and_load_gtex_eqtl(engine, tmp_path / "dl", reference_engine=ref)

        assert {s.tissue for s in stats} == {"Whole_Blood", "Liver"}
        assert _count_tissue_rows(engine, "Whole_Blood") == 2
        assert _count_tissue_rows(engine, "Liver") == 2

        with ref.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "gtex_eqtl")
            ).fetchone()
        assert row is not None
        assert row.version == GTEX_VERSION
        assert row.genome_build == "GRCh37"  # rsID-joined to GRCh37 despite GTEx b38

    def test_empty_tar_refuses_to_build(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        other = tmp_path / "readme.txt"
        other.write_text("not an eqtl file", encoding="utf-8")
        tar_path = tmp_path / "empty.tar"
        with tarfile.open(tar_path, "w") as tf:
            tf.add(other, arcname="GTEx_Analysis_v8_eQTL/readme.txt")
        lookup_path = _write(tmp_path, "lookup.txt", _LOOKUP)

        monkeypatch.setattr(
            gtex_mod, "_download_gtex_inputs", lambda d, **k: (tar_path, lookup_path)
        )
        engine = _engine(tmp_path)
        ref = sa.create_engine(f"sqlite:///{tmp_path}/reference.db")
        reference_metadata.create_all(ref)
        with pytest.raises(ValueError, match="no GTEx"):
            download_and_load_gtex_eqtl(engine, tmp_path / "dl", reference_engine=ref)

    def test_requires_reference_engine(self, tmp_path: Path) -> None:
        # The registry contract needs the version in reference.db, so a build
        # without a reference engine must fail fast (before any download).
        engine = _engine(tmp_path)
        with pytest.raises(ValueError, match="requires reference_engine"):
            download_and_load_gtex_eqtl(engine, tmp_path / "dl")
