"""Tests for the ClinVar VCF downloader and SQLite loader (P1-10 / T1-11).

Covers:
- VCF line parsing (individual records, edge cases)
- Review status to star mapping
- Full VCF file parsing (mini fixture)
- Streaming iterator parsing
- Bulk loading into SQLite via clinvar_variants table
- Stream loading via load_clinvar_from_iter
- Version tracking in database_versions
- Download function (mocked HTTP, error handling, partial cleanup)
- End-to-end download_and_load_clinvar pipeline (mocked HTTP)
- Malformed VCF data handling
"""

from __future__ import annotations

import gzip
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import sqlalchemy as sa

from backend.annotation.clinvar import (
    REVIEW_STATUS_STARS,
    SkipReason,
    _extract_gene_symbol,
    _normalize_chrom,
    _parse_info_field,
    _review_status_to_stars,
    download_and_load_clinvar,
    download_clinvar_vcf,
    iter_clinvar_vcf,
    load_clinvar_from_iter,
    load_clinvar_into_db,
    parse_clinvar_vcf,
    parse_clinvar_vcf_line,
    record_clinvar_version,
)
from backend.db.tables import clinvar_variants, database_versions, reference_metadata

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
MINI_CLINVAR_VCF = FIXTURES_DIR / "mini_clinvar.vcf"


# ═══════════════════════════════════════════════════════════════════════
# Unit tests — helper functions
# ═══════════════════════════════════════════════════════════════════════


class TestParseInfoField:
    def test_basic_key_value(self):
        result = _parse_info_field("RS=429358;CLNSIG=Pathogenic")
        assert result == {"RS": "429358", "CLNSIG": "Pathogenic"}

    def test_flag_fields(self):
        result = _parse_info_field("RS=123;FLAG;CLNSIG=Benign")
        assert result["FLAG"] == ""
        assert result["RS"] == "123"

    def test_empty_string(self):
        result = _parse_info_field("")
        assert result == {"": ""}

    def test_value_with_equals(self):
        result = _parse_info_field("KEY=val=ue;OTHER=x")
        assert result["KEY"] == "val=ue"


class TestReviewStatusToStars:
    @pytest.mark.parametrize(
        "status,expected",
        [
            ("practice_guideline", 4),
            ("reviewed_by_expert_panel", 3),
            ("criteria_provided,_multiple_submitters,_no_conflicts", 2),
            ("criteria_provided,_single_submitter", 1),
            ("criteria_provided,_conflicting_interpretations", 1),
            ("criteria_provided,_conflicting_classifications", 1),
            ("no_assertion_criteria_provided", 0),
            ("no_assertion_provided", 0),
            ("no_classification_provided", 0),
            ("unknown_status", 0),
        ],
    )
    def test_mapping(self, status: str, expected: int):
        assert _review_status_to_stars(status) == expected

    def test_all_known_statuses_mapped(self):
        """Every key in REVIEW_STATUS_STARS resolves correctly."""
        for status, stars in REVIEW_STATUS_STARS.items():
            assert _review_status_to_stars(status) == stars


class TestNormalizeChrom:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1", "1"),
            ("22", "22"),
            ("X", "X"),
            ("Y", "Y"),
            ("MT", "MT"),
            ("chr1", "1"),
            ("chrX", "X"),
            ("chrMT", "MT"),
        ],
    )
    def test_valid(self, raw: str, expected: str):
        assert _normalize_chrom(raw) == expected

    @pytest.mark.parametrize("raw", ["0", "23", "Un", "chrUn_random", ""])
    def test_invalid(self, raw: str):
        assert _normalize_chrom(raw) is None


class TestExtractGeneSymbol:
    def test_single_gene(self):
        assert _extract_gene_symbol("APOE:348") == "APOE"

    def test_multiple_genes(self):
        assert _extract_gene_symbol("BRCA1:672|BRCA2:675") == "BRCA1"

    def test_empty(self):
        assert _extract_gene_symbol("") is None

    def test_none_value(self):
        assert _extract_gene_symbol(None) is None


# ═══════════════════════════════════════════════════════════════════════
# Unit tests — VCF line parsing
# ═══════════════════════════════════════════════════════════════════════


class TestParseClinvarVcfLine:
    def test_pathogenic_variant(self):
        line = (
            "17\t43091983\t17661\tCTC\tC\t.\t.\t"
            "RS=80357906;CLNSIG=Pathogenic;"
            "CLNREVSTAT=reviewed_by_expert_panel;"
            "CLNDN=Hereditary_breast_and_ovarian_cancer_syndrome;"
            "GENEINFO=BRCA1:672;CLNVCID=17661"
        )
        rec, skip = parse_clinvar_vcf_line(line)
        assert skip is None
        assert rec is not None
        assert rec.rsid == "rs80357906"
        assert rec.chrom == "17"
        assert rec.pos == 43091983
        assert rec.ref == "CTC"
        assert rec.alt == "C"
        assert rec.significance == "Pathogenic"
        assert rec.review_stars == 3
        assert rec.accession == "VCV000017661"
        assert rec.conditions == "Hereditary breast and ovarian cancer syndrome"
        assert rec.gene_symbol == "BRCA1"
        assert rec.variation_id == 17661

    def test_benign_variant(self):
        line = (
            "22\t19963748\t16312\tG\tA\t.\t.\t"
            "RS=4680;CLNSIG=Benign;"
            "CLNREVSTAT=criteria_provided,_multiple_submitters,_no_conflicts;"
            "CLNDN=not_specified;GENEINFO=COMT:1312;CLNVCID=16312"
        )
        rec, skip = parse_clinvar_vcf_line(line)
        assert skip is None
        assert rec is not None
        assert rec.rsid == "rs4680"
        assert rec.significance == "Benign"
        assert rec.review_stars == 2
        assert rec.gene_symbol == "COMT"

    def test_practice_guideline_4_stars(self):
        line = (
            "11\t5227002\t15333\tT\tA\t.\t.\t"
            "RS=334;CLNSIG=Pathogenic;"
            "CLNREVSTAT=practice_guideline;"
            "CLNDN=Sickle_cell_disease;GENEINFO=HBB:3043;CLNVCID=15333"
        )
        rec, skip = parse_clinvar_vcf_line(line)
        assert skip is None
        assert rec is not None
        assert rec.review_stars == 4

    def test_skips_no_rs_with_reason(self):
        """Lines without RS in INFO should return skip_reason=NO_RSID."""
        line = (
            "3\t12345\t100001\tC\tT\t.\t.\t"
            "CLNSIG=Pathogenic;CLNREVSTAT=no_assertion_criteria_provided;"
            "CLNDN=Some_disease;GENEINFO=FAKEGENE:99"
        )
        rec, skip = parse_clinvar_vcf_line(line)
        assert rec is None
        assert skip == SkipReason.NO_RSID

    def test_skips_invalid_chrom_with_reason(self):
        line = "Un\t12345\t100\tA\tG\t.\t.\tRS=999;CLNSIG=Benign;CLNREVSTAT=no_assertion_provided"
        rec, skip = parse_clinvar_vcf_line(line)
        assert rec is None
        assert skip == SkipReason.INVALID_CHROM

    def test_skips_short_line_with_reason(self):
        rec, skip = parse_clinvar_vcf_line("too\tfew\tfields")
        assert rec is None
        assert skip == SkipReason.MALFORMED

    def test_skips_non_numeric_pos(self):
        """Non-numeric POS should return skip_reason=MALFORMED."""
        line = "1\tNOTANUM\t42\tA\tG\t.\t.\tRS=99;CLNSIG=Benign;CLNREVSTAT=no_assertion_provided"
        rec, skip = parse_clinvar_vcf_line(line)
        assert rec is None
        assert skip == SkipReason.MALFORMED

    def test_skips_non_numeric_rs(self):
        """Non-numeric RS values should return skip_reason=MALFORMED, mirroring
        the POS check — dbSNP rs numbers are bare positive integers, so a value
        like ``abc``/``12abc``/``-5`` must be skipped, never built into a junk
        ``rs{value}`` rsid."""
        for bad in ("abc", "12abc", "-5"):
            line = (
                f"1\t100\t42\tA\tG\t.\t.\tRS={bad};CLNSIG=Benign;CLNREVSTAT=no_assertion_provided"
            )
            rec, skip = parse_clinvar_vcf_line(line)
            assert rec is None, f"RS={bad} should be rejected"
            assert skip == SkipReason.MALFORMED, f"RS={bad} skip reason"

    def test_clnacc_fallback(self):
        """When CLNVCID is absent, CLNACC is used for accession."""
        line = (
            "1\t100\t42\tA\tG\t.\t.\t"
            "RS=99;CLNSIG=Benign;CLNREVSTAT=no_assertion_provided;"
            "CLNACC=RCV000012345|RCV000012346"
        )
        rec, skip = parse_clinvar_vcf_line(line)
        assert skip is None
        assert rec is not None
        assert rec.accession == "RCV000012345"

    def test_multi_allelic_uses_first_alt(self):
        line = "1\t100\t42\tA\tG,T\t.\t.\tRS=99;CLNSIG=Benign;CLNREVSTAT=no_assertion_provided"
        rec, _ = parse_clinvar_vcf_line(line)
        assert rec is not None
        assert rec.alt == "G"

    def test_chr_prefix_stripped(self):
        line = (
            "chr19\t44908684\t17864\tT\tC\t.\t.\t"
            "RS=429358;CLNSIG=risk_factor;"
            "CLNREVSTAT=criteria_provided,_single_submitter;"
            "CLNDN=Alzheimer_disease;GENEINFO=APOE:348"
        )
        rec, _ = parse_clinvar_vcf_line(line)
        assert rec is not None
        assert rec.chrom == "19"

    def test_significance_slash_takes_first(self):
        """Multi-significance separated by / should use first."""
        line = (
            "1\t100\t42\tA\tG\t.\t.\t"
            "RS=99;CLNSIG=Pathogenic/Likely_pathogenic;"
            "CLNREVSTAT=criteria_provided,_single_submitter"
        )
        rec, _ = parse_clinvar_vcf_line(line)
        assert rec is not None
        assert rec.significance == "Pathogenic"

    def test_gene_with_rs_substring_still_no_rsid(self):
        """Gene name containing 'RS' substring should not trick skip counting."""
        line = (
            "1\t100\t42\tA\tG\t.\t.\t"
            "CLNSIG=Benign;CLNREVSTAT=no_assertion_provided;"
            "GENEINFO=MARS:4141"
        )
        rec, skip = parse_clinvar_vcf_line(line)
        assert rec is None
        assert skip == SkipReason.NO_RSID


# ═══════════════════════════════════════════════════════════════════════
# Integration tests — full VCF parsing
# ═══════════════════════════════════════════════════════════════════════


class TestParseClinvarVcf:
    def test_parse_mini_fixture(self):
        """Parse the mini ClinVar VCF fixture and verify stats."""
        rows, stats = parse_clinvar_vcf(MINI_CLINVAR_VCF)

        # 12 data lines total, 1 has no RS → 11 loaded
        assert stats.total_lines == 12
        assert stats.variants_loaded == 11
        assert stats.skipped_no_rsid == 1
        assert stats.file_date == "20260301"

    def test_known_variant_present(self):
        """rs28897696 (HBB Pathogenic) should be in parsed output."""
        rows, _ = parse_clinvar_vcf(MINI_CLINVAR_VCF)
        hbb = [r for r in rows if r["rsid"] == "rs28897696"]
        assert len(hbb) == 1
        assert hbb[0]["significance"] == "Pathogenic"
        assert hbb[0]["gene_symbol"] == "HBB"
        assert hbb[0]["review_stars"] == 3

    def test_sickle_cell_4_stars(self):
        """rs334 (Sickle cell) should have 4 review stars."""
        rows, _ = parse_clinvar_vcf(MINI_CLINVAR_VCF)
        scd = [r for r in rows if r["rsid"] == "rs334"]
        assert len(scd) == 1
        assert scd[0]["review_stars"] == 4
        assert scd[0]["conditions"] == "Sickle cell disease"

    def test_parse_gzipped(self, tmp_path: Path):
        """Parsing a gzipped VCF should produce the same results."""
        gz_path = tmp_path / "clinvar.vcf.gz"
        with open(MINI_CLINVAR_VCF, "rb") as f_in:
            with gzip.open(gz_path, "wb") as f_out:
                f_out.write(f_in.read())

        rows, stats = parse_clinvar_vcf(gz_path)
        assert stats.variants_loaded == 11

    def test_progress_callback(self):
        """Progress callback should not raise (even if lines < 10k)."""
        cb = MagicMock()
        rows, stats = parse_clinvar_vcf(MINI_CLINVAR_VCF, progress_callback=cb)
        # Mini fixture has only 12 lines — below 10k threshold
        assert stats.variants_loaded == 11


class TestIterClinvarVcf:
    def test_iter_yields_correct_count(self):
        """Iterator should yield 11 rows from mini fixture."""
        count = 0
        stats = None
        for _, stats in iter_clinvar_vcf(MINI_CLINVAR_VCF):
            count += 1
        assert count == 11
        assert stats is not None
        assert stats.variants_loaded == 11

    def test_iter_matches_parse(self):
        """Iterator output should match parse_clinvar_vcf output."""
        rows_iter = [row for row, _ in iter_clinvar_vcf(MINI_CLINVAR_VCF)]
        rows_parse, _ = parse_clinvar_vcf(MINI_CLINVAR_VCF)
        assert rows_iter == rows_parse


class TestMalformedVcfData:
    def test_binary_garbage_line(self):
        """Binary garbage should be handled gracefully."""
        line = "1\t100\t42\tA\tG\t.\t.\t\x00\x01\x02"
        rec, skip = parse_clinvar_vcf_line(line)
        assert rec is None
        assert skip == SkipReason.NO_RSID

    def test_truncated_info_field(self):
        """Truncated INFO with an empty RS value is skipped as NO_RSID: the
        ``if not rs_val`` guard fires before any rsid is built, so the parser
        never yields ``rsid == "rs"``."""
        rec, skip = parse_clinvar_vcf_line("1\t100\t42\tA\tG\t.\t.\tRS=")
        assert rec is None
        assert skip == SkipReason.NO_RSID

    def test_empty_line(self):
        rec, skip = parse_clinvar_vcf_line("")
        assert rec is None
        assert skip == SkipReason.MALFORMED

    def test_malformed_vcf_file(self, tmp_path: Path):
        """A VCF with malformed lines should skip them gracefully."""
        vcf = tmp_path / "bad.vcf"
        vcf.write_text(
            "##fileformat=VCFv4.1\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\tNOTNUM\t42\tA\tG\t.\t.\tRS=99;CLNSIG=Benign\n"
            "1\t100\t42\tA\tG\t.\t.\tRS=99;CLNSIG=Benign;CLNREVSTAT=no_assertion_provided\n"
        )
        rows, stats = parse_clinvar_vcf(vcf)
        assert stats.total_lines == 2
        assert stats.variants_loaded == 1
        assert stats.skipped_malformed == 1


# ═══════════════════════════════════════════════════════════════════════
# Integration tests — SQLite loading
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def ref_engine() -> sa.Engine:
    """In-memory SQLite engine with reference tables."""
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    return engine


class TestLoadClinvarIntoDb:
    def test_load_mini_fixture(self, ref_engine: sa.Engine):
        """Load mini VCF into DB and verify row count."""
        rows, stats = parse_clinvar_vcf(MINI_CLINVAR_VCF)
        load_clinvar_into_db(rows, ref_engine, stats=stats)

        with ref_engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(clinvar_variants)).scalar()
        assert count == 11

    def test_known_pathogenic_variant(self, ref_engine: sa.Engine):
        """Verify rs28897696 is queryable after loading (T1-11 spec)."""
        rows, stats = parse_clinvar_vcf(MINI_CLINVAR_VCF)
        load_clinvar_into_db(rows, ref_engine, stats=stats)

        with ref_engine.connect() as conn:
            row = conn.execute(
                sa.select(clinvar_variants).where(clinvar_variants.c.rsid == "rs28897696")
            ).first()

        assert row is not None
        assert row.significance == "Pathogenic"
        assert row.review_stars == 3
        assert row.conditions == "Hemoglobin C disease"
        assert row.gene_symbol == "HBB"
        assert row.variation_id == 5128

    def test_chrom_pos_index_lookup(self, ref_engine: sa.Engine):
        """Lookup by (chrom, pos) should work efficiently."""
        rows, stats = parse_clinvar_vcf(MINI_CLINVAR_VCF)
        load_clinvar_into_db(rows, ref_engine, stats=stats)

        with ref_engine.connect() as conn:
            row = conn.execute(
                sa.select(clinvar_variants).where(
                    sa.and_(
                        clinvar_variants.c.chrom == "19",
                        clinvar_variants.c.pos == 44908684,
                    )
                )
            ).first()

        assert row is not None
        assert row.rsid == "rs429358"
        assert row.gene_symbol == "APOE"

    def test_clear_existing_replaces_data(self, ref_engine: sa.Engine):
        """Loading with clear_existing=True should replace old rows."""
        rows, stats = parse_clinvar_vcf(MINI_CLINVAR_VCF)
        load_clinvar_into_db(rows, ref_engine, stats=stats)
        load_clinvar_into_db(rows, ref_engine, stats=stats, clear_existing=True)

        with ref_engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(clinvar_variants)).scalar()
        # Should not double — old rows deleted
        assert count == 11

    def test_append_without_clear(self, ref_engine: sa.Engine):
        """Loading with clear_existing=False should append."""
        rows, stats = parse_clinvar_vcf(MINI_CLINVAR_VCF)
        load_clinvar_into_db(rows, ref_engine, stats=stats, clear_existing=True)
        load_clinvar_into_db(rows, ref_engine, stats=stats, clear_existing=False)

        with ref_engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(clinvar_variants)).scalar()
        assert count == 22  # doubled

    def test_empty_rows(self, ref_engine: sa.Engine):
        """Loading empty list should not error."""
        stats = load_clinvar_into_db([], ref_engine)
        assert stats.variants_loaded == 0


class TestLoadClinvarFromIter:
    def test_stream_load(self, ref_engine: sa.Engine):
        """Stream loading from iterator should match list-based loading."""
        row_iter = iter_clinvar_vcf(MINI_CLINVAR_VCF)
        stats = load_clinvar_from_iter(row_iter, ref_engine)

        assert stats.variants_loaded == 11
        with ref_engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(clinvar_variants)).scalar()
        assert count == 11

    def test_stream_load_known_variant(self, ref_engine: sa.Engine):
        """Stream-loaded data should be queryable by rsid."""
        row_iter = iter_clinvar_vcf(MINI_CLINVAR_VCF)
        load_clinvar_from_iter(row_iter, ref_engine)

        with ref_engine.connect() as conn:
            row = conn.execute(
                sa.select(clinvar_variants).where(clinvar_variants.c.rsid == "rs28897696")
            ).first()
        assert row is not None
        assert row.significance == "Pathogenic"


class TestRecordClinvarVersion:
    def test_insert_new_version(self, ref_engine: sa.Engine):
        record_clinvar_version(ref_engine, version="20260301", file_path="/tmp/clinvar.vcf.gz")
        with ref_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "clinvar")
            ).first()
        assert row is not None
        assert row.version == "20260301"

    def test_update_existing_version(self, ref_engine: sa.Engine):
        record_clinvar_version(ref_engine, version="20260201")
        record_clinvar_version(ref_engine, version="20260301")

        with ref_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "clinvar")
            ).first()
        assert row.version == "20260301"

    def test_checksum_stored(self, ref_engine: sa.Engine):
        record_clinvar_version(
            ref_engine,
            version="20260301",
            checksum="abc123",
            file_size_bytes=1024,
        )
        with ref_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "clinvar")
            ).first()
        assert row.checksum_sha256 == "abc123"
        assert row.file_size_bytes == 1024


# ═══════════════════════════════════════════════════════════════════════
# Download tests (mocked HTTP)
# ═══════════════════════════════════════════════════════════════════════


def _fake_stream_download(*, content: bytes = b"", headers=None, exc: BaseException | None = None):
    """Build a fake ``stream_download`` that writes ``content`` (or raises ``exc``).

    The wrapper functions delegate the actual transfer to
    ``backend.annotation.http_download.stream_download`` (exhaustively tested in
    ``test_http_download.py``); here we only verify the wrapper's own work:
    filenames, ``meta`` capture, and the atomic rename.
    """
    from backend.annotation.http_download import DownloadOutcome

    def _impl(url, tmp_path, *, progress_callback=None, **kwargs):
        if exc is not None:
            raise exc
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_bytes(content)
        if progress_callback is not None:
            progress_callback(len(content), len(content))
        return DownloadOutcome(
            path=tmp_path,
            total_bytes=len(content),
            expected_total=len(content),
            headers=httpx.Headers(headers or {}),  # case-insensitive, like the real one
            attempts=1,
            resumed=False,
        )

    return _impl


class TestDownloadClinvarVcf:
    def test_download_writes_file(self, tmp_path: Path):
        """download_clinvar_vcf should write the file and return its path."""
        fake_content = b"##fileformat=VCFv4.1\nfake data\n"

        with patch(
            "backend.annotation.clinvar.stream_download",
            _fake_stream_download(content=fake_content),
        ):
            result = download_clinvar_vcf(tmp_path / "downloads")

        assert result.exists()
        assert result.name == "clinvar_GRCh37.vcf.gz"
        assert result.read_bytes() == fake_content

    def test_no_temp_file_on_success(self, tmp_path: Path):
        """After successful download, no .tmp file should remain."""
        dl_dir = tmp_path / "downloads"

        with patch(
            "backend.annotation.clinvar.stream_download",
            _fake_stream_download(content=b"data"),
        ):
            download_clinvar_vcf(dl_dir)

        assert not (dl_dir / "clinvar_GRCh37.vcf.gz.tmp").exists()
        assert (dl_dir / "clinvar_GRCh37.vcf.gz").exists()

    def test_download_progress_callback(self, tmp_path: Path):
        """Progress callback should be called during download."""
        cb = MagicMock()

        with patch(
            "backend.annotation.clinvar.stream_download",
            _fake_stream_download(content=b"data"),
        ):
            download_clinvar_vcf(tmp_path / "dl", progress_callback=cb)

        cb.assert_called_once_with(4, 4)

    def test_http_error_preserves_partial_for_resume(self, tmp_path: Path):
        """A failed download preserves the partial .tmp so the next run resumes
        via Range (resumable=True) instead of re-downloading from zero (#756)."""
        dl_dir = tmp_path / "downloads"
        dl_dir.mkdir(parents=True)
        partial = dl_dir / "clinvar_GRCh37.vcf.gz.tmp"
        partial.write_bytes(b"partial-from-prior-run")
        err = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404)
        )

        with (
            patch("backend.annotation.clinvar.stream_download", _fake_stream_download(exc=err)),
            pytest.raises(httpx.HTTPStatusError),
        ):
            download_clinvar_vcf(dl_dir)

        # The downloader does not delete the partial on failure (preserve-for-resume).
        assert partial.exists()
        assert not (dl_dir / "clinvar_GRCh37.vcf.gz").exists()

    def test_wires_resumable_and_validator_sidecar(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """download_clinvar_vcf opts into cross-run resume + validator sidecar (#756)."""
        from types import SimpleNamespace

        dl_dir = tmp_path / "dl"
        dl_dir.mkdir()
        tmp_file = dl_dir / "clinvar_GRCh37.vcf.gz.tmp"
        sidecar = dl_dir / "clinvar_GRCh37.vcf.gz.tmp.validator"
        tmp_file.write_bytes(b"partial")
        sidecar.write_text('"etag-v1"', encoding="utf-8")
        captured: dict = {}

        def fake(url, tmp_path_arg, **kwargs):
            captured.update(kwargs)
            tmp_path_arg.write_bytes(b"done")
            return SimpleNamespace(total_bytes=4)

        monkeypatch.setattr("backend.annotation.clinvar.stream_download", fake)
        dest = download_clinvar_vcf(dl_dir)

        assert dest.read_bytes() == b"done"
        assert captured["resumable"] is True
        assert captured["validator"] == '"etag-v1"'
        assert callable(captured["on_validator"])
        assert not sidecar.exists()  # cleared on success


# ═══════════════════════════════════════════════════════════════════════
# End-to-end pipeline test (mocked download)
# ═══════════════════════════════════════════════════════════════════════


class TestDownloadAndLoadClinvar:
    def test_full_pipeline(self, ref_engine: sa.Engine, tmp_path: Path):
        """Full pipeline: download (mocked) → parse → load → version."""
        dest_dir = tmp_path / "downloads"
        dest_dir.mkdir()

        with (
            patch("backend.annotation.clinvar.download_clinvar_vcf") as mock_dl,
            patch("backend.annotation.clinvar._get_clinvar_last_modified_version") as mock_ver,
        ):
            mock_dl.return_value = MINI_CLINVAR_VCF
            mock_ver.return_value = "20260301"

            stats = download_and_load_clinvar(ref_engine, dest_dir)

        assert stats.variants_loaded == 11
        assert stats.file_date == "20260301"
        assert stats.sha256 is not None

        # Verify data in DB
        with ref_engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(clinvar_variants)).scalar()
            assert count == 11

            # Verify version recorded
            ver = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "clinvar")
            ).first()
            assert ver is not None
            assert ver.version == "20260301"
