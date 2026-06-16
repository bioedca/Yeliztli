"""Tests for dbSNP rsid validation and cross-reference (P1-12).

Covers:
- RsMergeArch BCP line parsing
- File iteration (gzipped and plain)
- SQLite loading (bulk + streaming)
- Version tracking in database_versions
- Download (mocked HTTP)
- rsid validation (valid, merged, i_prefix, invalid)
- Sample annotation (upsert into annotated_variants)
- Re-annotation idempotency
- End-to-end pipeline
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from backend.annotation.dbsnp import (
    LoadStats,
    ValidationStatus,
    annotate_sample_dbsnp,
    download_and_load_rsmerge,
    download_rsmerge_arch,
    iter_rsmerge_file,
    load_rsmerge_from_iter,
    load_rsmerge_into_db,
    lookup_merged_rsids,
    parse_rsmerge_line,
    record_dbsnp_version,
    validate_rsids,
)
from backend.db.tables import annotated_variants, database_versions, dbsnp_merges
from tests.backend.conftest import SEED_RAW_VARIANTS

# Path to test fixtures
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
MINI_RSMERGE_GZ = FIXTURES_DIR / "mini_rsmerge.bcp.gz"
MINI_RSMERGE = FIXTURES_DIR / "mini_rsmerge.bcp"


# ═══════════════════════════════════════════════════════════════════════
# parse_rsmerge_line
# ═══════════════════════════════════════════════════════════════════════


class TestParseRsmergeLine:
    """Tests for individual BCP line parsing."""

    def test_standard_line(self):
        line = "3219489\t1805007\t137\t0\t2009-01-01\t2009-01-01\t1805007\t0\t\n"
        record = parse_rsmerge_line(line)
        assert record is not None
        assert record.old_rsid == "rs3219489"
        assert record.current_rsid == "rs1805007"
        assert record.build_id == 137

    def test_rscurrent_preferred_over_rslow(self):
        """rsCurrent (col 6) is preferred over rsLow (col 1)."""
        line = "100\t200\t140\t0\t2012-01-01\t2012-01-01\t300\t0\t\n"
        record = parse_rsmerge_line(line)
        assert record is not None
        assert record.old_rsid == "rs100"
        assert record.current_rsid == "rs300"

    def test_fallback_to_rslow_when_rscurrent_empty(self):
        line = "666666\t777777\t140\t0\t2018-01-01\t2018-01-01\t\t0\t\n"
        record = parse_rsmerge_line(line)
        assert record is not None
        assert record.current_rsid == "rs777777"

    def test_self_merge_skipped(self):
        line = "555555\t555555\t130\t0\t2007-01-01\t2007-01-01\t555555\t0\t\n"
        record = parse_rsmerge_line(line)
        assert record is None

    def test_malformed_too_few_columns(self):
        record = parse_rsmerge_line("bad_line\n")
        assert record is None

    def test_empty_fields(self):
        record = parse_rsmerge_line("\t\t\t\t\t\t\t\t\n")
        assert record is None

    def test_no_build_id(self):
        line = "100\t200\t\t0\t2012-01-01\t2012-01-01\t300\t0\t\n"
        record = parse_rsmerge_line(line)
        assert record is not None
        assert record.build_id is None

    def test_rsid_already_prefixed(self):
        """Handles rs-prefixed values in the BCP (shouldn't happen but be safe)."""
        line = "rs100\trs200\t140\t0\t2012-01-01\t2012-01-01\trs300\t0\t\n"
        record = parse_rsmerge_line(line)
        assert record is not None
        assert record.old_rsid == "rs100"
        assert record.current_rsid == "rs300"


# ═══════════════════════════════════════════════════════════════════════
# iter_rsmerge_file
# ═══════════════════════════════════════════════════════════════════════


class TestIterRsmergeFile:
    """Tests for file iteration."""

    def test_gzipped_file(self):
        rows = []
        stats = LoadStats()
        for row, stats in iter_rsmerge_file(MINI_RSMERGE_GZ):
            rows.append(row)
        # 10 lines total, 1 self-merge, 1 malformed, 1 empty = 7 valid
        assert stats.total_lines == 10
        assert len(rows) == 7
        assert stats.merges_loaded == 7
        assert stats.skipped_malformed == 3

    def test_plain_file(self):
        rows = []
        stats = LoadStats()
        for row, stats in iter_rsmerge_file(MINI_RSMERGE):
            rows.append(row)
        assert stats.total_lines == 10
        assert len(rows) == 7

    def test_row_dict_shape(self):
        for row, _ in iter_rsmerge_file(MINI_RSMERGE_GZ):
            assert "old_rsid" in row
            assert "current_rsid" in row
            assert "build_id" in row
            break  # only check first row

    def test_progress_callback(self):
        callback = MagicMock()
        # Our fixture has only 10 lines, so the 100k threshold won't trigger
        for _ in iter_rsmerge_file(MINI_RSMERGE_GZ, progress_callback=callback):
            pass
        # No callback for < 100k lines
        callback.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# load_rsmerge_into_db
# ═══════════════════════════════════════════════════════════════════════


class TestLoadRsmergeIntoDb:
    """Tests for bulk loading into dbsnp_merges table."""

    def test_basic_load(self, reference_engine: sa.Engine):
        rows = [
            {"old_rsid": "rs100", "current_rsid": "rs200", "build_id": 140},
            {"old_rsid": "rs300", "current_rsid": "rs400", "build_id": 145},
        ]
        stats = load_rsmerge_into_db(rows, reference_engine)
        assert stats.merges_loaded == 2

        with reference_engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(dbsnp_merges)).scalar()
        assert count == 2

    def test_clear_existing(self, reference_engine: sa.Engine):
        rows1 = [{"old_rsid": "rs100", "current_rsid": "rs200", "build_id": 140}]
        rows2 = [{"old_rsid": "rs300", "current_rsid": "rs400", "build_id": 145}]

        load_rsmerge_into_db(rows1, reference_engine)
        load_rsmerge_into_db(rows2, reference_engine, clear_existing=True)

        with reference_engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(dbsnp_merges)).scalar()
        assert count == 1

    def test_append_mode(self, reference_engine: sa.Engine):
        rows1 = [{"old_rsid": "rs100", "current_rsid": "rs200", "build_id": 140}]
        rows2 = [{"old_rsid": "rs300", "current_rsid": "rs400", "build_id": 145}]

        load_rsmerge_into_db(rows1, reference_engine)
        load_rsmerge_into_db(rows2, reference_engine, clear_existing=False)

        with reference_engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(dbsnp_merges)).scalar()
        assert count == 2

    def test_duplicate_old_rsid_updates(self, reference_engine: sa.Engine):
        """Duplicate old_rsid should update (ON CONFLICT DO UPDATE)."""
        rows = [
            {"old_rsid": "rs100", "current_rsid": "rs200", "build_id": 140},
            {"old_rsid": "rs100", "current_rsid": "rs300", "build_id": 145},
        ]
        load_rsmerge_into_db(rows, reference_engine)

        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(dbsnp_merges.c.current_rsid).where(dbsnp_merges.c.old_rsid == "rs100")
            ).first()
        assert row is not None
        assert row.current_rsid == "rs300"


class TestLoadRsmergeFromIter:
    """Tests for streaming load from iterator."""

    def test_streaming_load(self, reference_engine: sa.Engine):
        row_iter = iter_rsmerge_file(MINI_RSMERGE_GZ)
        stats = load_rsmerge_from_iter(row_iter, reference_engine)

        assert stats.merges_loaded == 7

        with reference_engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(dbsnp_merges)).scalar()
        # May be fewer than 7 due to ON CONFLICT DO UPDATE for duplicates
        assert count is not None
        assert count >= 1


# ═══════════════════════════════════════════════════════════════════════
# record_dbsnp_version
# ═══════════════════════════════════════════════════════════════════════


class TestRecordDbsnpVersion:
    """Tests for version tracking."""

    def test_insert_new_version(self, reference_engine: sa.Engine):
        record_dbsnp_version(
            reference_engine,
            version="b155",
            file_path="/tmp/rsmerge.bcp.gz",
            file_size_bytes=1000000,
            checksum="abc123",
        )
        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "dbsnp")
            ).first()
        assert row is not None
        assert row.version == "b155"
        assert row.checksum_sha256 == "abc123"

    def test_update_existing_version(self, reference_engine: sa.Engine):
        record_dbsnp_version(reference_engine, version="b151")
        record_dbsnp_version(reference_engine, version="b155")

        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "dbsnp")
            ).first()
        assert row is not None
        assert row.version == "b155"


# ═══════════════════════════════════════════════════════════════════════
# download_rsmerge_arch
# ═══════════════════════════════════════════════════════════════════════


def _fake_stream_download(*, content: bytes = b"", headers=None, exc: BaseException | None = None):
    """Fake ``stream_download`` writing ``content`` (or raising ``exc``).

    The real transfer/resume logic is covered by ``test_http_download.py``; the
    wrapper tests here verify only filenames, ``meta`` capture and the rename.
    """
    import httpx

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


class TestDownloadRsmergeArch:
    """Tests for downloading (transfer delegated to stream_download)."""

    def test_download_success(self, tmp_path: Path):
        content = b"fake bcp content"

        with patch(
            "backend.annotation.dbsnp.stream_download",
            _fake_stream_download(content=content),
        ):
            path = download_rsmerge_arch(tmp_path, url="https://example.com/test.bcp.gz")

        assert path.exists()
        assert path.name == "RsMergeArch.bcp.gz"
        assert path.read_bytes() == content

    def test_meta_version_from_last_modified(self, tmp_path: Path):
        meta: dict = {}
        with patch(
            "backend.annotation.dbsnp.stream_download",
            _fake_stream_download(
                content=b"x", headers={"Last-Modified": "Wed, 21 Oct 2015 07:28:00 GMT"}
            ),
        ):
            download_rsmerge_arch(tmp_path, url="https://example.com/test.bcp.gz", meta=meta)
        assert meta["version"] == "20151021"

    def test_download_error_preserves_partial_for_resume(self, tmp_path: Path):
        """A failed download preserves the partial .tmp for cross-run resume (#756)."""
        import httpx

        partial = tmp_path / "RsMergeArch.bcp.gz.tmp"
        partial.write_bytes(b"partial-from-prior-run")
        err = httpx.HTTPError("fail")
        with (
            patch("backend.annotation.dbsnp.stream_download", _fake_stream_download(exc=err)),
            pytest.raises(httpx.HTTPError),
        ):
            download_rsmerge_arch(tmp_path, url="https://example.com/test.bcp.gz")

        assert partial.exists()  # preserve-for-resume, not deleted on failure

    def test_wires_resumable_and_validator_sidecar(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """download_rsmerge_arch opts into cross-run resume + validator sidecar (#756)."""
        from types import SimpleNamespace

        tmp_file = tmp_path / "RsMergeArch.bcp.gz.tmp"
        sidecar = tmp_path / "RsMergeArch.bcp.gz.tmp.validator"
        tmp_file.write_bytes(b"partial")
        sidecar.write_text('"etag-v1"', encoding="utf-8")
        captured: dict = {}

        def fake(url, tmp_path_arg, **kwargs):
            captured.update(kwargs)
            tmp_path_arg.write_bytes(b"done")
            return SimpleNamespace(headers={})

        monkeypatch.setattr("backend.annotation.dbsnp.stream_download", fake)
        dest = download_rsmerge_arch(tmp_path, url="https://example.com/test.bcp.gz")

        assert dest.read_bytes() == b"done"
        assert captured["resumable"] is True
        assert captured["validator"] == '"etag-v1"'
        assert callable(captured["on_validator"])
        assert not sidecar.exists()

    def test_progress_callback_called(self, tmp_path: Path):
        content = b"data"
        callback = MagicMock()

        with patch(
            "backend.annotation.dbsnp.stream_download",
            _fake_stream_download(content=content),
        ):
            download_rsmerge_arch(
                tmp_path,
                url="https://example.com/test.bcp.gz",
                progress_callback=callback,
            )

        callback.assert_called_once_with(len(content), len(content))


# ═══════════════════════════════════════════════════════════════════════
# lookup_merged_rsids
# ═══════════════════════════════════════════════════════════════════════


class TestLookupMergedRsids:
    """Tests for merged rsid lookup from reference.db."""

    def test_finds_merged_rsids(self, seeded_reference_engine: sa.Engine):
        results = lookup_merged_rsids(
            ["rs3219489", "rs12345", "rs429358"],
            seeded_reference_engine,
        )
        # rs3219489 and rs12345 are in SEED_DBSNP_MERGES
        assert "rs3219489" in results
        assert results["rs3219489"].current_rsid == "rs1805007"
        assert results["rs3219489"].build_id == 137

        assert "rs12345" in results
        assert results["rs12345"].current_rsid == "rs67890"

        # rs429358 is NOT a merged rsid (it's a current rsid)
        assert "rs429358" not in results

    def test_empty_input(self, seeded_reference_engine: sa.Engine):
        results = lookup_merged_rsids([], seeded_reference_engine)
        assert results == {}

    def test_no_matches(self, seeded_reference_engine: sa.Engine):
        results = lookup_merged_rsids(["rs1", "rs2"], seeded_reference_engine)
        assert results == {}

    def test_batch_handling(self, reference_engine: sa.Engine):
        """Test that batching works for >500 rsids."""
        # Insert 600 merge records
        rows = [
            {"old_rsid": f"rs{i}", "current_rsid": f"rs{i + 1000}", "build_id": 140}
            for i in range(600)
        ]
        load_rsmerge_into_db(rows, reference_engine)

        rsids = [f"rs{i}" for i in range(600)]
        results = lookup_merged_rsids(rsids, reference_engine)
        assert len(results) == 600


# ═══════════════════════════════════════════════════════════════════════
# validate_rsids
# ═══════════════════════════════════════════════════════════════════════


class TestValidateRsids:
    """Tests for rsid validation logic."""

    def test_valid_rsid(self, seeded_reference_engine: sa.Engine):
        results = validate_rsids(["rs429358"], seeded_reference_engine)
        assert len(results) == 1
        assert results[0].status == ValidationStatus.VALID
        assert results[0].current_rsid is None

    def test_merged_rsid(self, seeded_reference_engine: sa.Engine):
        results = validate_rsids(["rs3219489"], seeded_reference_engine)
        assert len(results) == 1
        assert results[0].status == ValidationStatus.MERGED
        assert results[0].current_rsid == "rs1805007"
        assert results[0].build_id == 137

    def test_i_prefix_rsid(self, seeded_reference_engine: sa.Engine):
        results = validate_rsids(["i7001525"], seeded_reference_engine)
        assert len(results) == 1
        assert results[0].status == ValidationStatus.I_PREFIX

    def test_invalid_rsid(self, seeded_reference_engine: sa.Engine):
        results = validate_rsids(["INVALID_ID", "123", ""], seeded_reference_engine)
        assert all(r.status == ValidationStatus.INVALID for r in results)

    def test_mixed_rsids(self, seeded_reference_engine: sa.Engine):
        rsids = ["rs429358", "rs3219489", "i7001525", "BADID"]
        results = validate_rsids(rsids, seeded_reference_engine)

        assert results[0].status == ValidationStatus.VALID
        assert results[1].status == ValidationStatus.MERGED
        assert results[2].status == ValidationStatus.I_PREFIX
        assert results[3].status == ValidationStatus.INVALID

    def test_order_preserved(self, seeded_reference_engine: sa.Engine):
        rsids = ["i001", "rs429358", "NOPE", "rs3219489"]
        results = validate_rsids(rsids, seeded_reference_engine)
        assert [r.rsid for r in results] == rsids

    def test_empty_input(self, seeded_reference_engine: sa.Engine):
        results = validate_rsids([], seeded_reference_engine)
        assert results == []


# ═══════════════════════════════════════════════════════════════════════
# annotate_sample_dbsnp
# ═══════════════════════════════════════════════════════════════════════


class TestAnnotateSampleDbsnp:
    """Tests for full sample annotation pipeline."""

    def test_basic_annotation(
        self,
        sample_with_variants: sa.Engine,
        seeded_reference_engine: sa.Engine,
    ):
        result = annotate_sample_dbsnp(sample_with_variants, seeded_reference_engine)

        num_variants = len(SEED_RAW_VARIANTS)
        assert result.total_variants == num_variants
        assert result.rows_written == num_variants

        # rs12345 is in SEED_DBSNP_MERGES as a merged rsid
        assert result.merged_rsids >= 1

        # All variants should have been written
        with sample_with_variants.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count()).select_from(annotated_variants)
            ).scalar()
        assert count == num_variants

    def test_merged_rsid_annotation(
        self,
        sample_with_variants: sa.Engine,
        seeded_reference_engine: sa.Engine,
    ):
        annotate_sample_dbsnp(sample_with_variants, seeded_reference_engine)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(
                    annotated_variants.c.dbsnp_validation,
                    annotated_variants.c.dbsnp_rsid_current,
                    annotated_variants.c.dbsnp_build,
                ).where(annotated_variants.c.rsid == "rs12345")
            ).first()

        assert row is not None
        assert row.dbsnp_validation == "merged"
        assert row.dbsnp_rsid_current == "rs67890"
        assert row.dbsnp_build == 144

    def test_valid_rsid_annotation(
        self,
        sample_with_variants: sa.Engine,
        seeded_reference_engine: sa.Engine,
    ):
        annotate_sample_dbsnp(sample_with_variants, seeded_reference_engine)

        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(
                    annotated_variants.c.dbsnp_validation,
                    annotated_variants.c.dbsnp_rsid_current,
                ).where(annotated_variants.c.rsid == "rs429358")
            ).first()

        assert row is not None
        assert row.dbsnp_validation == "valid"
        assert row.dbsnp_rsid_current is None

    def test_empty_sample(
        self,
        sample_engine: sa.Engine,
        seeded_reference_engine: sa.Engine,
    ):
        result = annotate_sample_dbsnp(sample_engine, seeded_reference_engine)
        assert result.total_variants == 0
        assert result.rows_written == 0

    def test_reannotation_idempotent(
        self,
        sample_with_variants: sa.Engine,
        seeded_reference_engine: sa.Engine,
    ):
        """Running annotation twice should not duplicate rows."""
        result1 = annotate_sample_dbsnp(sample_with_variants, seeded_reference_engine)
        result2 = annotate_sample_dbsnp(sample_with_variants, seeded_reference_engine)

        assert result1.rows_written == result2.rows_written

        with sample_with_variants.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count()).select_from(annotated_variants)
            ).scalar()
        assert count == len(SEED_RAW_VARIANTS)

    def test_preserves_existing_clinvar_annotation(
        self,
        sample_with_variants: sa.Engine,
        seeded_reference_engine: sa.Engine,
    ):
        """dbSNP annotation should not overwrite ClinVar columns."""
        from backend.annotation.clinvar import annotate_sample_clinvar

        # First annotate with ClinVar
        annotate_sample_clinvar(sample_with_variants, seeded_reference_engine)

        # Then annotate with dbSNP
        annotate_sample_dbsnp(sample_with_variants, seeded_reference_engine)

        # ClinVar data should still be present
        with sample_with_variants.connect() as conn:
            row = conn.execute(
                sa.select(
                    annotated_variants.c.clinvar_significance,
                    annotated_variants.c.dbsnp_validation,
                ).where(annotated_variants.c.rsid == "rs429358")
            ).first()

        assert row is not None
        assert row.clinvar_significance is not None
        assert row.dbsnp_validation == "valid"


# ═══════════════════════════════════════════════════════════════════════
# download_and_load_rsmerge (end-to-end with mocked download)
# ═══════════════════════════════════════════════════════════════════════


class TestDownloadAndLoadRsmerge:
    """End-to-end test with mocked HTTP."""

    def test_full_pipeline(self, reference_engine: sa.Engine, tmp_path: Path):
        # Copy our fixture to simulate a download
        import shutil

        fixture = MINI_RSMERGE_GZ

        def mock_download(dest_dir, **kwargs):
            dest = dest_dir / "RsMergeArch.bcp.gz"
            shutil.copy(fixture, dest)
            return dest

        with patch(
            "backend.annotation.dbsnp.download_rsmerge_arch",
            side_effect=mock_download,
        ):
            stats = download_and_load_rsmerge(
                reference_engine,
                tmp_path,
                url="https://example.com/fake",
            )

        assert stats.merges_loaded == 7
        assert stats.sha256 is not None

        # Version recorded
        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "dbsnp")
            ).first()
        assert row is not None


# ═══════════════════════════════════════════════════════════════════════
# Edge cases and integration
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge cases for rsid validation."""

    def test_i_prefix_various_formats(self, reference_engine: sa.Engine):
        results = validate_rsids(
            ["i7001525", "I123", "i0001"],
            reference_engine,
        )
        assert all(r.status == ValidationStatus.I_PREFIX for r in results)

    def test_numeric_only_is_invalid(self, reference_engine: sa.Engine):
        results = validate_rsids(["123456"], reference_engine)
        assert results[0].status == ValidationStatus.INVALID

    def test_empty_string_is_invalid(self, reference_engine: sa.Engine):
        results = validate_rsids([""], reference_engine)
        assert results[0].status == ValidationStatus.INVALID

    def test_rs_without_number_is_invalid(self, reference_engine: sa.Engine):
        results = validate_rsids(["rs", "rsABC"], reference_engine)
        assert all(r.status == ValidationStatus.INVALID for r in results)

    def test_sample_with_i_prefix_variants(
        self,
        sample_engine: sa.Engine,
        reference_engine: sa.Engine,
    ):
        """Sample containing i-prefixed variants gets them classified correctly."""
        from backend.db.tables import raw_variants

        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [
                    {"rsid": "i7001525", "chrom": "1", "pos": 100, "genotype": "AA"},
                    {"rsid": "rs429358", "chrom": "19", "pos": 44908684, "genotype": "TC"},
                ],
            )

        result = annotate_sample_dbsnp(sample_engine, reference_engine)
        assert result.total_variants == 2
        assert result.i_prefix_rsids == 1
        assert result.valid_rsids == 1

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants.c.dbsnp_validation).where(
                    annotated_variants.c.rsid == "i7001525"
                )
            ).first()
        assert row is not None
        assert row.dbsnp_validation == "i_prefix"
