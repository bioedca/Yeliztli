"""PGS Catalog harmonized-file ingestion (SW-B1).

Validates the three guardrails: GRCh37 build firewall, per-score license gating
(only CC0/CC-BY bundle-eligible), and the no-silent-wipe empty-parse guard, plus
column-name-keyed parsing of the harmonized scoring-file format.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.annotation import pgs_catalog as pc
from backend.annotation.pgs_catalog import (
    classify_pgs_license,
    download_and_load_pgs_score,
    list_scores,
    load_pgs_score_from_file,
    load_score_weights,
    parse_pgs_header,
)

# A minimal harmonized GRCh37 scoring file (real PGS Catalog format: '#' header,
# then a name-keyed column table; GRCh37 coords live in hm_chr/hm_pos).
_GRCH37 = (
    "###PGS CATALOG SCORING FILE\n"
    "#format_version=2.0\n"
    "##POLYGENIC SCORE (PGS) INFORMATION\n"
    "#pgs_id=PGS000999\n"
    "#pgs_name=TEST_PRS\n"
    "#trait_reported=Test trait\n"
    "#trait_efo=EFO_0000305\n"
    "#variants_number=3\n"
    "#weight_type=beta\n"
    "##HARMONIZATION DETAILS\n"
    "#HmPOS_build=GRCh37\n"
    "rsID\tchr_name\teffect_allele\tother_allele\teffect_weight\thm_rsID\thm_chr\thm_pos\n"
    "rs1\t1\tA\tG\t0.10\trs1\t1\t1000\n"
    "rs2\t2\tT\tC\t-0.20\trs2\t2\t2000\n"
    "rs3\t5\tG\tA\t0.30\trs3\t5\t5000\n"
)

# Same but GRCh38 — must be rejected.
_GRCH38 = _GRCH37.replace("#HmPOS_build=GRCh37", "#HmPOS_build=GRCh38").replace(
    "#pgs_id=PGS000999", "#pgs_id=PGS000888"
)


def _engine(tmp_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{tmp_path}/pgs.db")


def _write(tmp_path: Path, name: str, content: str, gz: bool = False) -> Path:
    p = tmp_path / name
    if gz:
        with gzip.open(p, "wt", encoding="utf-8") as fh:
            fh.write(content)
    else:
        p.write_text(content, encoding="utf-8")
    return p


class TestLicenseClassification:
    def test_cc0_is_bundleable(self) -> None:
        assert classify_pgs_license("CC0 1.0 Universal") == (True, "CC0")

    def test_cc_by_is_bundleable(self) -> None:
        ok, label = classify_pgs_license("Creative Commons Attribution 4.0 (CC-BY)")
        assert ok is True and label == "CC-BY"

    def test_cc_by_nc_is_not_bundleable(self) -> None:
        ok, label = classify_pgs_license("CC-BY-NC 4.0")
        assert ok is False and label == "non-commercial"

    def test_pgs_default_terms_not_bundleable(self) -> None:
        # PGS Catalog default: defers to author restrictions → user-fetch only.
        ok, label = classify_pgs_license(
            "PGS obtained from the Catalog should be cited appropriately, and used in "
            "accordance with any licensing restrictions set by the authors."
        )
        assert ok is False and label == "author-restricted"

    def test_unspecified_not_bundleable(self) -> None:
        assert classify_pgs_license(None) == (False, "unspecified")
        assert classify_pgs_license("   ") == (False, "unspecified")


class TestHeaderParsing:
    def test_parses_key_values_skips_banners(self) -> None:
        h = parse_pgs_header(_GRCH37.splitlines())
        assert h["pgs_id"] == "PGS000999"
        assert h["HmPOS_build"] == "GRCh37"
        assert h["trait_efo"] == "EFO_0000305"
        assert "POLYGENIC SCORE (PGS) INFORMATION" not in h  # banner skipped


class TestIngestion:
    def test_load_grch37_score(self, tmp_path: Path) -> None:
        f = _write(tmp_path, "PGS000999_hmPOS_GRCh37.txt.gz", _GRCH37, gz=True)
        engine = _engine(tmp_path)
        stats = load_pgs_score_from_file(f, engine, license_text="CC0 1.0")
        assert stats.loaded == 3
        assert stats.genome_build == "GRCh37"
        assert stats.license_bundle_ok is True
        weights = load_score_weights(engine, "PGS000999")
        assert len(weights) == 3
        by_rs = {w["rsid"]: w for w in weights}
        assert by_rs["rs1"]["chrom"] == "1" and by_rs["rs1"]["pos"] == 1000
        assert by_rs["rs1"]["effect_allele"] == "A" and by_rs["rs1"]["effect_weight"] == 0.10

    def test_rejects_grch38_build(self, tmp_path: Path) -> None:
        f = _write(tmp_path, "PGS000888_hmPOS_GRCh38.txt", _GRCH38)
        engine = _engine(tmp_path)
        with pytest.raises(ValueError, match="build is 'GRCh38'"):
            load_pgs_score_from_file(f, engine)

    def test_plain_text_also_supported(self, tmp_path: Path) -> None:
        f = _write(tmp_path, "PGS000999_hmPOS_GRCh37.txt", _GRCH37)
        engine = _engine(tmp_path)
        stats = load_pgs_score_from_file(f, engine, license_text="CC-BY 4.0")
        assert stats.loaded == 3 and stats.license_bundle_ok is True

    def test_metadata_recorded_with_bundle_flag(self, tmp_path: Path) -> None:
        f = _write(tmp_path, "p.txt", _GRCH37)
        engine = _engine(tmp_path)
        load_pgs_score_from_file(f, engine, license_text=None)  # unspecified → not bundleable
        scores = list_scores(engine)
        assert len(scores) == 1
        m = scores[0]
        assert m["pgs_id"] == "PGS000999"
        assert m["trait_reported"] == "Test trait"
        assert m["variants_number"] == 3
        assert m["license_bundle_ok"] == 0
        assert list_scores(engine, bundle_ok_only=True) == []

    def test_column_order_independence(self, tmp_path: Path) -> None:
        # Reorder columns + omit other_allele; parser is name-keyed, not positional.
        reordered = (
            "#pgs_id=PGS000777\n"
            "#HmPOS_build=GRCh37\n"
            "effect_weight\thm_pos\thm_chr\teffect_allele\thm_rsID\n"
            "0.5\t100\t7\tT\trs7\n"
        )
        f = _write(tmp_path, "r.txt", reordered)
        engine = _engine(tmp_path)
        stats = load_pgs_score_from_file(f, engine, license_text="CC0")
        assert stats.loaded == 1
        w = load_score_weights(engine, "PGS000777")[0]
        assert w["chrom"] == "7" and w["pos"] == 100 and w["effect_allele"] == "T"
        assert w["other_allele"] is None

    def test_bad_rows_skipped_not_crash(self, tmp_path: Path) -> None:
        content = (
            "#pgs_id=PGS000111\n#HmPOS_build=GRCh37\n"
            "effect_allele\teffect_weight\thm_chr\thm_pos\n"
            "A\t0.1\t1\t1000\n"
            "G\tnotanumber\t2\t2000\n"  # bad weight → skipped
            "T\t0.2\t3\tnotapos\n"  # bad pos → skipped
        )
        f = _write(tmp_path, "b.txt", content)
        engine = _engine(tmp_path)
        stats = load_pgs_score_from_file(f, engine, license_text="CC0")
        assert stats.loaded == 1 and stats.skipped == 2

    def test_empty_parse_raises_no_wipe(self, tmp_path: Path) -> None:
        content = (
            "#pgs_id=PGS000222\n#HmPOS_build=GRCh37\n"
            "effect_allele\teffect_weight\thm_chr\thm_pos\n"
        )
        f = _write(tmp_path, "e.txt", content)
        engine = _engine(tmp_path)
        with pytest.raises(ValueError, match="zero weight rows"):
            load_pgs_score_from_file(f, engine, license_text="CC0")

    def test_missing_pgs_id_raises(self, tmp_path: Path) -> None:
        content = (
            "#HmPOS_build=GRCh37\neffect_allele\teffect_weight\thm_chr\thm_pos\nA\t0.1\t1\t1000\n"
        )
        f = _write(tmp_path, "n.txt", content)
        engine = _engine(tmp_path)
        with pytest.raises(ValueError, match="pgs_id"):
            load_pgs_score_from_file(f, engine)

    def test_download_and_load_wires_license(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Mock the network: license from REST + the harmonized file from FTP.
        dl_dir = tmp_path / "dl"

        def fake_download(pgs_id: str, dest_dir: Path, **_kw: object) -> Path:
            Path(dest_dir).mkdir(parents=True, exist_ok=True)
            return _write(Path(dest_dir), f"{pgs_id}_hmPOS_GRCh37.txt.gz", _GRCH37, gz=True)

        monkeypatch.setattr(pc, "fetch_pgs_license", lambda pgs_id, **_kw: "CC-BY 4.0")
        monkeypatch.setattr(pc, "download_pgs_score", fake_download)
        engine = _engine(tmp_path)
        stats = download_and_load_pgs_score("PGS000999", engine, dl_dir)
        assert stats.loaded == 3
        assert stats.license_bundle_ok is True  # CC-BY → bundleable

    def test_reingest_replaces_not_duplicates(self, tmp_path: Path) -> None:
        f = _write(tmp_path, "p.txt", _GRCH37)
        engine = _engine(tmp_path)
        load_pgs_score_from_file(f, engine, license_text="CC0")
        load_pgs_score_from_file(f, engine, license_text="CC0")  # idempotent re-load
        assert len(load_score_weights(engine, "PGS000999")) == 3
        assert len(list_scores(engine)) == 1
