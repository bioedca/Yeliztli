"""Tests for AlphaMissense ingestion + the REVEL-complement badge (SW-A12)."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.alphamissense import (
    ALPHAMISSENSE_PMID,
    alphamissense_badge,
    alphamissense_badge_for_variant,
    classify_am_pathogenicity,
)
from backend.annotation import alphamissense as am
from backend.annotation.alphamissense import (
    ALPHAMISSENSE_MD5,
    create_alphamissense_table,
    download_and_load_alphamissense,
    load_alphamissense_from_csv,
    load_alphamissense_from_tsv,
    lookup_alphamissense_by_positions,
)

# AlphaMissense TSV format: copyright/license comment lines, a "#CHROM" header,
# then tab-delimited rows. Chroms are "chr"-prefixed; am_class uses the file's
# benign/ambiguous/pathogenic spellings.
_TSV = (
    "# Copyright 2023 DeepMind Technologies Limited\n"
    "# Licensed under CC BY-NC-SA 4.0 license\n"
    "#CHROM\tPOS\tREF\tALT\tgenome\tuniprot_id\ttranscript_id\tprotein_variant\t"
    "am_pathogenicity\tam_class\n"
    "chr1\t100\tG\tA\thg19\tP1\tENST1\tV2L\t0.9\tlikely_pathogenic\n"
    "chr1\t100\tG\tC\thg19\tP1\tENST1\tV2P\t0.1\tlikely_benign\n"
    "chr2\t200\tC\tT\thg19\tP2\tENST2\tA3T\t0.45\tambiguous\n"
    "chrM\t300\tA\tG\thg19\tP3\tENST3\tX1Y\t0.95\tpathogenic\n"
    "chrZZ\t9\tA\tT\thg19\tPX\tENSTX\tQ1R\t0.5\tambiguous\n"  # invalid chrom → skipped
)


def _engine(tmp_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{tmp_path}/am.db")


def test_load_and_chrom_normalization(tmp_path: Path) -> None:
    tsv = tmp_path / "am.tsv"
    tsv.write_text(_TSV, encoding="utf-8")
    engine = _engine(tmp_path)
    stats = load_alphamissense_from_tsv(tsv, engine)
    assert stats.loaded == 4  # the chrZZ row is skipped
    assert stats.skipped == 1
    with engine.connect() as conn:
        chroms = {
            r.chrom
            for r in conn.execute(sa.text("SELECT DISTINCT chrom FROM alphamissense_scores"))
        }
    assert chroms == {"1", "2", "MT"}  # chr stripped, chrM → MT


def test_lookup_by_positions_chr_agnostic(tmp_path: Path) -> None:
    tsv = tmp_path / "am.tsv"
    tsv.write_text(_TSV, encoding="utf-8")
    engine = _engine(tmp_path)
    load_alphamissense_from_tsv(tsv, engine)

    # Plain and chr-prefixed chrom inputs both resolve.
    hits = lookup_alphamissense_by_positions(
        [("1", 100, "G", "A"), ("chr1", 100, "G", "C"), ("1", 100, "G", "T")], engine
    )
    assert hits[("1", 100, "G", "A")]["am_class"] == "likely_pathogenic"
    assert hits[("1", 100, "G", "A")]["am_pathogenicity"] == 0.9
    assert hits[("chr1", 100, "G", "C")]["am_class"] == "likely_benign"
    assert ("1", 100, "G", "T") not in hits  # no such alt

    assert lookup_alphamissense_by_positions([], engine) == {}


def test_create_table_idempotent(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    create_alphamissense_table(engine)
    create_alphamissense_table(engine)  # no error on second call


def test_csv_invalid_pos_skipped(tmp_path: Path) -> None:
    csv_path = tmp_path / "am.csv"
    csv_path.write_text(
        "chrom,pos,ref,alt,am_pathogenicity,am_class\n"
        "chr1,100,G,A,0.9,likely_pathogenic\n"
        "chr1,notanint,G,C,0.1,likely_benign\n",  # bad pos → skipped, not a crash
        encoding="utf-8",
    )
    engine = _engine(tmp_path)
    stats = load_alphamissense_from_csv(csv_path, engine)
    assert stats.loaded == 1
    assert stats.skipped == 1


def test_md5_mismatch_aborts_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrupt download (MD5 mismatch) must raise and not load anything."""

    def fake_download(dest_dir: Path, **_kw: object) -> Path:
        p = Path(dest_dir)
        p.mkdir(parents=True, exist_ok=True)
        f = p / "AlphaMissense_hg19.tsv.gz"
        f.write_bytes(b"corrupt-not-the-real-file")  # wrong MD5
        return f

    monkeypatch.setattr(am, "download_alphamissense", fake_download)
    engine = _engine(tmp_path)
    dl_dir = tmp_path / "dl"
    with pytest.raises(ValueError, match="MD5 mismatch"):
        download_and_load_alphamissense(engine, dl_dir)
    assert ALPHAMISSENSE_MD5  # the pinned constant is what we validate against
    assert not (dl_dir / "AlphaMissense_hg19.tsv.gz").exists()  # corrupt file removed


def test_classify_thresholds() -> None:
    assert classify_am_pathogenicity(0.9) == "likely_pathogenic"
    assert classify_am_pathogenicity(0.2) == "likely_benign"
    assert classify_am_pathogenicity(0.45) == "ambiguous"
    assert classify_am_pathogenicity(None) is None


class TestBadge:
    def test_none_when_no_data(self) -> None:
        assert alphamissense_badge(None, None) is None

    def test_class_spelling_normalized(self) -> None:
        # File "pathogenic" → stable "likely_pathogenic".
        b = alphamissense_badge(0.95, "pathogenic")
        assert b["am_class"] == "likely_pathogenic"

    def test_never_an_acmg_vote(self) -> None:
        b = alphamissense_badge(0.9, "likely_pathogenic", revel_criterion="PP3")
        assert b["acmg_vote"] is False
        assert b["complements_revel"] is True
        assert b["context_only"] is True
        assert ALPHAMISSENSE_PMID in b["pmid_citations"]

    def test_concordant_with_revel_pp3(self) -> None:
        b = alphamissense_badge(0.9, "likely_pathogenic", revel_criterion="PP3")
        assert b["revel_concordance"] == "concordant"

    def test_discordant_with_revel_bp4(self) -> None:
        b = alphamissense_badge(0.9, "likely_pathogenic", revel_criterion="BP4")
        assert b["revel_concordance"] == "discordant"

    def test_ambiguous_is_not_comparable(self) -> None:
        b = alphamissense_badge(0.45, "ambiguous", revel_criterion="PP3")
        assert b["revel_concordance"] == "not_comparable"

    def test_no_revel_is_not_comparable(self) -> None:
        b = alphamissense_badge(0.9, "likely_pathogenic")
        assert b["revel_concordance"] == "not_comparable"

    def test_class_derived_from_score_when_missing(self) -> None:
        b = alphamissense_badge(0.95, None)
        assert b["am_class"] == "likely_pathogenic"

    def test_variant_helper_uses_revel_criterion_without_casting_vote(self) -> None:
        b = alphamissense_badge_for_variant(
            0.95,
            "likely_pathogenic",
            revel=0.8,
            consequence="missense_variant",
        )
        assert b["revel_concordance"] == "concordant"
        assert b["acmg_vote"] is False

    def test_variant_detail_payload_helper_attaches_context_badge(self) -> None:
        from backend.api.routes.variant_detail import _attach_alphamissense_badge

        payload = {
            "alphamissense_pathogenicity": 0.95,
            "alphamissense_class": "likely_pathogenic",
            "revel": 0.8,
            "consequence": "missense_variant",
        }
        _attach_alphamissense_badge(payload)

        assert payload["alphamissense_badge"]["predictor"] == "AlphaMissense"
        assert payload["alphamissense_badge"]["context_only"] is True
        assert payload["alphamissense_badge"]["acmg_vote"] is False
