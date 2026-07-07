"""Tests for the 23andMe TSV parser (P1-04, refactored in step 29).

Covers test IDs: T1-01, T1-01a, T1-01c, T1-02, T1-03, T1-04, T1-05.

Vendor dispatch (rejection of VCF / AncestryDNA / CSV / binary inputs with
format-specific guidance) lives in ``backend.ingestion.dispatcher`` and is
covered by ``tests/backend/test_dispatcher.py``; the post-step-29 parser
assumes its caller has already identified the file as 23andMe.

The single-class ``TestDispatcherFlipForAncestryDNA`` at the bottom of the
file is step 33's flip of the historical ``test_rejects_ancestrydna_with_message``
test: where the legacy assertion locked that ``parse_23andme(<AncestryDNA>)``
*raises*, the flipped assertion locks that ``dispatcher.parse(<AncestryDNA>)``
*succeeds*, with the bare 23andMe parser still raising
``UnrecognizedVersionError`` on the same input.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants
from backend.ingestion import dispatcher, parser_23andme
from backend.ingestion.base import SourceVendor
from backend.ingestion.parser_23andme import (
    MalformedDataError,
    ParsedVariant,
    ParserError,
    UnrecognizedVersionError,
    UnsupportedFormatError,
    _canonical_genotype,
    normalize_chromosome,
    parse_23andme,
)

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

V5_FILE = FIXTURES / "sample_23andme_v5.txt"
V3_FILE = FIXTURES / "sample_23andme_v3.txt"
V4_FILE = FIXTURES / "sample_23andme_v4.txt"
ANCESTRY_V2_FILE = FIXTURES / "sample_ancestrydna_v2.txt"


# ═══════════════════════════════════════════════════════════════════════════
# T1-01: Parser reads valid v5 file, extracts 4 fields, assigns version
# ═══════════════════════════════════════════════════════════════════════════


class TestParseV5:
    """T1-01: Parser correctly reads valid 23andMe v5 file."""

    def test_parse_returns_v5_version(self) -> None:
        result = parse_23andme(V5_FILE)
        assert result.version == "v5"
        assert isinstance(result.version, str)
        assert result.vendor is SourceVendor.TWENTYTHREEANDME
        assert result.build == "GRCh37"

    def test_variant_count(self) -> None:
        result = parse_23andme(V5_FILE)
        assert len(result.variants) == 1000

    def test_variant_has_four_fields(self) -> None:
        result = parse_23andme(V5_FILE)
        v = result.variants[0]
        assert isinstance(v, ParsedVariant)
        assert isinstance(v.rsid, str) and v.rsid
        assert isinstance(v.chrom, str) and v.chrom
        assert isinstance(v.pos, int) and v.pos >= 0
        assert isinstance(v.genotype, str) and v.genotype

    def test_skipped_lines_counted(self) -> None:
        result = parse_23andme(V5_FILE)
        # v5 has 18 comment lines (including column header)
        assert result.skipped_lines > 0
        assert result.total_lines == result.skipped_lines + len(result.variants)

    def test_apoe_snps_present(self) -> None:
        """Verify APOE-relevant SNPs are parsed correctly."""
        result = parse_23andme(V5_FILE)
        rsid_map = {v.rsid: v for v in result.variants}

        rs429358 = rsid_map.get("rs429358")
        assert rs429358 is not None
        assert rs429358.chrom == "19"
        assert rs429358.pos == 44908684
        assert rs429358.genotype == "TT"

        rs7412 = rsid_map.get("rs7412")
        assert rs7412 is not None
        assert rs7412.chrom == "19"
        assert rs7412.pos == 44908822
        assert rs7412.genotype == "CC"

    def test_heterozygous_calls_stored_in_canonical_sorted_order(self) -> None:
        """Het calls must be stored as the cross-vendor canonical *sorted* pair.

        The v5 fixture carries two unsorted heterozygous rows — rs4244285 and
        rs3892097, both raw ``GA`` (CYP2C19 / CYP2D6 PGx SNPs) — that the parser
        must reorder to ``AG``. Every other ``.genotype`` assertion in this file
        is homozygous (``TT``/``CC``) or a no-call (``--``), all sort-invariant,
        so the GA→AG canonicalization was previously exercised by the fixture
        and verified by nothing (#688). If ``_canonical_genotype`` stopped
        sorting, a 23andMe ``GA`` would no longer match an AncestryDNA ``AG`` for
        the same call (cross-vendor merge/dedup, PGx star-allele calling, export).
        """
        result = parse_23andme(V5_FILE)
        rsid_map = {v.rsid: v for v in result.variants}

        rs4244285 = rsid_map.get("rs4244285")
        assert rs4244285 is not None
        assert rs4244285.genotype == "AG"  # raw fixture row is "GA"

        rs3892097 = rsid_map.get("rs3892097")
        assert rs3892097 is not None
        assert rs3892097.genotype == "AG"  # raw fixture row is "GA"


# ═══════════════════════════════════════════════════════════════════════════
# _canonical_genotype: the cross-vendor sorted-pair invariant (#688)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Sort-discriminating het pairs: raw order ≠ sorted order. These are the
        # only inputs that catch a dropped/half sort — the gap #688 closes.
        ("GA", "AG"),
        ("TC", "CT"),
        ("CA", "AC"),
        ("TG", "GT"),
        # Lowercase: uppercase *then* sort.
        ("ga", "AG"),
        ("tc", "CT"),
        # Already-sorted het is idempotent (and lowercase still uppercases).
        ("AG", "AG"),
        ("ag", "AG"),
        # Homozygous / no-call / hemizygous are sort-invariant but must be
        # preserved exactly (no spurious reordering or dropping).
        ("TT", "TT"),
        ("cc", "CC"),
        ("--", "--"),
        ("A", "A"),  # single-char hemizygous (X/Y) — uppercased, not reordered
        ("g", "G"),
    ],
)
def test_canonical_genotype_uppercases_and_sorts_the_pair(raw: str, expected: str) -> None:
    assert _canonical_genotype(raw) == expected


# ═══════════════════════════════════════════════════════════════════════════
# T1-01a: Auto-detect v3 and v4 formats, i-prefixed rsid handling
# ═══════════════════════════════════════════════════════════════════════════


class TestParseV3V4:
    """T1-01a: Parser auto-detects v3/v4 formats."""

    def test_parse_v3_variant_count(self) -> None:
        result = parse_23andme(V3_FILE)
        assert result.version == "v3"
        assert result.build == "GRCh36"
        assert result.vendor is SourceVendor.TWENTYTHREEANDME
        assert len(result.variants) == 100

    def test_parse_v4_variant_count(self) -> None:
        result = parse_23andme(V4_FILE)
        assert result.version == "v4"
        assert result.build == "GRCh37"
        assert result.vendor is SourceVendor.TWENTYTHREEANDME
        assert len(result.variants) == 100

    def test_v3_i_prefixed_rsids_preserved(self) -> None:
        """i-prefixed rsids (23andMe internal IDs) are kept as-is."""
        result = parse_23andme(V3_FILE)
        i_rsids = [v for v in result.variants if v.rsid.startswith("i")]
        assert len(i_rsids) > 0
        for v in i_rsids:
            assert v.rsid[0] == "i"
            assert v.rsid[1:].isdigit()

    def test_v5_i_prefixed_rsids_preserved(self) -> None:
        result = parse_23andme(V5_FILE)
        i_rsids = [v for v in result.variants if v.rsid.startswith("i")]
        assert len(i_rsids) == 40


# ═══════════════════════════════════════════════════════════════════════════
# T1-01c: Unrecognized 23andMe version → specific guidance
# ═══════════════════════════════════════════════════════════════════════════


class TestUnrecognizedVersion:
    """T1-01c: Parser rejects ambiguous 23andMe-like files.

    Post-step-29, the parser raises ``UnrecognizedVersionError`` for any input
    whose head lines lack both the canonical column header and a recognizable
    build string. Vendor-dispatch rejection of VCF / AncestryDNA / CSV / binary
    moved to the dispatcher.
    """

    def test_rejects_unknown_version_with_guidance(self) -> None:
        """File has the column header but no build string."""
        content = (
            "# This data file generated by 23andMe\n"
            "#\n"
            "# rsid\tchromosome\tposition\tgenotype\n"
            "rs123\t1\t100\tAA\n"
        )
        with pytest.raises(UnrecognizedVersionError, match="GitHub issue"):
            parse_23andme(io.StringIO(content))

    def test_rejects_file_without_column_header(self) -> None:
        """A non-23andMe file presented to the bare parser raises Unrecognized."""
        content = "some random text\nwithout any structure\nat all\n"
        with pytest.raises(UnrecognizedVersionError):
            parse_23andme(io.StringIO(content))


# ═══════════════════════════════════════════════════════════════════════════
# T1-02: Parser rejects malformed files
# ═══════════════════════════════════════════════════════════════════════════


class TestMalformedData:
    """T1-02: Parser rejects malformed files."""

    def _make_v5_stream(self, *data_lines: str) -> io.StringIO:
        """Create a minimal v5-like stream with custom data lines."""
        header = (
            "# This data file generated by 23andMe\n"
            "# reference human assembly build 37\n"
            "# rsid\tchromosome\tposition\tgenotype\n"
        )
        return io.StringIO(header + "\n".join(data_lines) + "\n")

    def test_wrong_column_count_too_few(self) -> None:
        stream = self._make_v5_stream("rs123\t1\t100")
        with pytest.raises(MalformedDataError, match="expected 4 columns"):
            parse_23andme(stream)

    def test_wrong_column_count_too_many(self) -> None:
        stream = self._make_v5_stream("rs123\t1\t100\tAA\textra")
        with pytest.raises(MalformedDataError, match="expected 4 columns"):
            parse_23andme(stream)

    def test_concatenated_ancestrydna_marker_has_actionable_message(self) -> None:
        stream = self._make_v5_stream(
            "rs1\t1\t100\tAA",
            "#This file was generated by AncestryDNA at: Tue Jan 14 2025",
            "#Data was collected using AncestryDNA array version: V2.0",
            "rsid\tchromosome\tposition\tallele1\tallele2",
            "rs2\t1\t200\tA\tG",
        )

        with pytest.raises(MalformedDataError) as excinfo:
            parse_23andme(stream)

        message = str(excinfo.value)
        assert "multiple concatenated raw data files" in message
        assert "23andMe section followed by an AncestryDNA section" in message
        assert "single-vendor" in message
        assert "expected 4 columns" not in message

    def test_concatenated_ancestrydna_header_has_actionable_message(self) -> None:
        stream = self._make_v5_stream(
            "rs1\t1\t100\tAA",
            "rsid\tchromosome\tposition\tallele1\tallele2",
            "rs2\t1\t200\tA\tG",
        )

        with pytest.raises(MalformedDataError) as excinfo:
            parse_23andme(stream)

        message = str(excinfo.value)
        assert "multiple concatenated raw data files" in message
        assert "single-vendor" in message
        assert "expected 4 columns" not in message

    def test_invalid_chromosome(self) -> None:
        stream = self._make_v5_stream("rs123\t99\t100\tAA")
        with pytest.raises(MalformedDataError, match="Invalid chromosome"):
            parse_23andme(stream)

    def test_non_numeric_position(self) -> None:
        stream = self._make_v5_stream("rs123\t1\tabc\tAA")
        with pytest.raises(MalformedDataError, match="non-numeric position"):
            parse_23andme(stream)

    def test_empty_rsid(self) -> None:
        stream = self._make_v5_stream("\t1\t100\tAA")
        with pytest.raises(MalformedDataError, match="empty rsid"):
            parse_23andme(stream)

    def test_empty_genotype(self) -> None:
        stream = self._make_v5_stream("rs123\t1\t100\t")
        with pytest.raises(MalformedDataError, match="empty genotype"):
            parse_23andme(stream)

    def test_negative_position(self) -> None:
        stream = self._make_v5_stream("rs123\t1\t-5\tAA")
        with pytest.raises(MalformedDataError, match="negative position"):
            parse_23andme(stream)


# ═══════════════════════════════════════════════════════════════════════════
# T1-03: No-call (--) genotypes flagged separately
# ═══════════════════════════════════════════════════════════════════════════


class TestNoCalls:
    """T1-03: Parser flags no-call genotypes."""

    def test_nocall_counted(self) -> None:
        result = parse_23andme(V5_FILE)
        assert result.nocall_count == 25

    def test_nocall_variants_have_dash_genotype(self) -> None:
        result = parse_23andme(V5_FILE)
        nocalls = [v for v in result.variants if v.genotype == "--"]
        assert len(nocalls) == result.nocall_count

    def test_nocalls_still_in_variants_list(self) -> None:
        """No-calls are not filtered out — they appear in the variants list."""
        result = parse_23andme(V5_FILE)
        assert result.nocall_count > 0
        assert len(result.variants) == 1000  # all lines including no-calls


# ═══════════════════════════════════════════════════════════════════════════
# T1-04: Chromosome notation normalized
# ═══════════════════════════════════════════════════════════════════════════


class TestChromosomeNormalization:
    """T1-04: Chromosome notation normalized: 23→X, 24→Y, 25→MT, 26→MT."""

    def test_normalize_23_to_x(self) -> None:
        assert normalize_chromosome("23") == "X"

    def test_normalize_24_to_y(self) -> None:
        assert normalize_chromosome("24") == "Y"

    def test_normalize_25_to_mt(self) -> None:
        assert normalize_chromosome("25") == "MT"

    def test_normalize_26_to_mt(self) -> None:
        assert normalize_chromosome("26") == "MT"

    def test_normalize_standard_chroms_unchanged(self) -> None:
        for c in ["1", "10", "22", "X", "Y", "MT"]:
            assert normalize_chromosome(c) == c

    def test_normalize_case_insensitive(self) -> None:
        assert normalize_chromosome("x") == "X"
        assert normalize_chromosome("y") == "Y"
        assert normalize_chromosome("mt") == "MT"

    def test_v3_numeric_chroms_normalized(self) -> None:
        """v3 file uses 23/24/25/26 — parser normalizes them."""
        result = parse_23andme(V3_FILE)
        chroms = {v.chrom for v in result.variants}
        # After normalization, no numeric 23-26 should remain
        assert "23" not in chroms
        assert "24" not in chroms
        assert "25" not in chroms
        assert "26" not in chroms
        # Should have X, Y, MT instead
        assert "X" in chroms
        assert "Y" in chroms
        assert "MT" in chroms

    def test_invalid_chromosome_raises(self) -> None:
        with pytest.raises(MalformedDataError):
            normalize_chromosome("99")
        with pytest.raises(MalformedDataError):
            normalize_chromosome("Z")


# ═══════════════════════════════════════════════════════════════════════════
# T1-05: Integration — parse → write to SQLite → read back
# ═══════════════════════════════════════════════════════════════════════════


class TestIntegration:
    """T1-05: Parse sample file → write to per-sample SQLite → verify."""

    @pytest.fixture()
    def sample_engine(self, tmp_path: Path) -> sa.Engine:
        """Create a per-sample SQLite database engine."""
        db_path = tmp_path / "sample_test.db"
        engine = sa.create_engine(f"sqlite:///{db_path}")
        create_sample_tables(engine)
        return engine

    def test_roundtrip_v5(self, sample_engine: sa.Engine) -> None:
        """Parse v5 → bulk insert → SELECT back and verify."""
        result = parse_23andme(V5_FILE)

        # Bulk insert using executemany
        rows = [
            {"rsid": v.rsid, "chrom": v.chrom, "pos": v.pos, "genotype": v.genotype}
            for v in result.variants
        ]
        with sample_engine.connect() as conn:
            conn.execute(raw_variants.insert(), rows)
            conn.commit()

        # Read back
        with sample_engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(raw_variants)).scalar()
            assert count == 1000

            # Verify a specific variant
            row = conn.execute(
                sa.select(raw_variants).where(raw_variants.c.rsid == "rs429358")
            ).fetchone()
            assert row is not None
            assert row.chrom == "19"
            assert row.pos == 44908684
            assert row.genotype == "TT"

    def test_roundtrip_v3(self, sample_engine: sa.Engine) -> None:
        """Parse v3 → bulk insert → verify row count and chrom normalization."""
        result = parse_23andme(V3_FILE)

        rows = [
            {"rsid": v.rsid, "chrom": v.chrom, "pos": v.pos, "genotype": v.genotype}
            for v in result.variants
        ]
        with sample_engine.connect() as conn:
            conn.execute(raw_variants.insert(), rows)
            conn.commit()

        with sample_engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(raw_variants)).scalar()
            assert count == 100

            # Verify chromosome normalization in DB
            x_rows = conn.execute(
                sa.select(raw_variants).where(raw_variants.c.chrom == "X")
            ).fetchall()
            assert len(x_rows) > 0

    def test_nocalls_stored_in_db(self, sample_engine: sa.Engine) -> None:
        """No-call variants are stored with genotype='--'."""
        result = parse_23andme(V5_FILE)

        rows = [
            {"rsid": v.rsid, "chrom": v.chrom, "pos": v.pos, "genotype": v.genotype}
            for v in result.variants
        ]
        with sample_engine.connect() as conn:
            conn.execute(raw_variants.insert(), rows)
            conn.commit()

        with sample_engine.connect() as conn:
            nocalls = conn.execute(
                sa.select(raw_variants).where(raw_variants.c.genotype == "--")
            ).fetchall()
            assert len(nocalls) == result.nocall_count


# ═══════════════════════════════════════════════════════════════════════════
# Edge cases & TextIO support
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Additional edge-case coverage."""

    def test_parse_from_string_io(self) -> None:
        """Parser works with TextIO (not just file paths)."""
        content = V5_FILE.read_text()
        result = parse_23andme(io.StringIO(content))
        assert result.version == "v5"
        assert len(result.variants) == 1000

    def test_all_exception_types_are_parser_errors(self) -> None:
        assert issubclass(UnsupportedFormatError, ParserError)
        assert issubclass(MalformedDataError, ParserError)
        assert issubclass(UnrecognizedVersionError, ParserError)

    def test_parsed_variant_is_frozen(self) -> None:
        v = ParsedVariant(rsid="rs1", chrom="1", pos=100, genotype="AA")
        with pytest.raises((AttributeError, Exception)):
            v.rsid = "rs2"  # type: ignore[misc]

    def test_position_zero_allowed(self) -> None:
        """Position 0 is valid (some markers use it)."""
        header = (
            "# This data file generated by 23andMe\n"
            "# reference human assembly build 37\n"
            "# rsid\tchromosome\tposition\tgenotype\n"
        )
        stream = io.StringIO(header + "rs123\t1\t0\tAA\n")
        result = parse_23andme(stream)
        assert result.variants[0].pos == 0


# ═══════════════════════════════════════════════════════════════════════════
# Step 29 invariant: parser_23andme no longer carries module-level dispatch
# ═══════════════════════════════════════════════════════════════════════════


class TestNoDispatchHelpers:
    """Step 29 contract: vendor dispatch lives in `dispatcher`, not here.

    Locks the surface so a future refactor cannot silently re-import the
    legacy `_reject_non_23andme` / `_check_binary` / public `detect_format`
    helpers back into `parser_23andme`. Step 38 layers the
    `dispatcher.parse(sample_23andme_v5) ≡ parse_23andme(...)` regression
    assertion on top.
    """

    @pytest.mark.parametrize(
        "name",
        ["_reject_non_23andme", "_check_binary", "detect_format"],
    )
    def test_dispatch_helpers_removed(self, name: str) -> None:
        assert not hasattr(parser_23andme, name), (
            f"`parser_23andme.{name}` should have been retired in step 29 — "
            "vendor dispatch belongs in `backend.ingestion.dispatcher`."
        )

    def test_parse_23andme_is_only_public_entry_point(self) -> None:
        """`__all__` exposes the parser + shared symbols, nothing dispatch-shaped."""
        assert "parse_23andme" in parser_23andme.__all__
        assert "normalize_chromosome" in parser_23andme.__all__
        for forbidden in ("detect_format", "_reject_non_23andme", "_check_binary"):
            assert forbidden not in parser_23andme.__all__

    def test_parse_result_version_is_string(self) -> None:
        """Public `ParseResult.version` is a plain string post-step-29."""
        result = parse_23andme(V5_FILE)
        assert isinstance(result.version, str)
        assert result.version == "v5"


# ═══════════════════════════════════════════════════════════════════════════
# Step 38 (ADNA-08d) — 23andMe regression via the dispatcher
# ═══════════════════════════════════════════════════════════════════════════


class TestDispatcherRegressionFor23andMe:
    """Step 38 / ADNA-08d: routing a 23andMe input through ``dispatcher.parse``
    returns the same ``ParseResult`` as calling ``parse_23andme`` directly.

    The dispatcher's only job for a 23andMe file is (a) head-line inspection
    to identify the vendor and (b) handing off to ``parse_23andme``. Once
    routing has occurred, the dispatcher must add zero transformation on top
    of the parser — same vendor, same string ``version``, same build, same
    variant list. This class locks that invariant on the load-bearing
    ``sample_23andme_v5.txt`` fixture called out in Plan §13.1 ADNA-08d, plus
    the v3 + v4 fixtures so the post-step-29 string-version contract is
    asserted across every 23andMe version the parser auto-detects.

    Sits alongside ``TestNoDispatchHelpers`` (step 29), which locks that
    ``parser_23andme`` itself does not carry its own vendor-dispatch helpers;
    this class extends that surface audit to ``dispatcher``'s ``_looks_like_*``
    helpers, which must stay in the dispatcher and not migrate back into the
    bare 23andMe parser.
    """

    @pytest.mark.parametrize(
        ("fixture", "expected_version", "expected_build", "expected_variant_count"),
        [
            (V5_FILE, "v5", "GRCh37", 1000),
            (V4_FILE, "v4", "GRCh37", 100),
            (V3_FILE, "v3", "GRCh36", 100),
        ],
    )
    def test_dispatcher_parse_matches_direct_call(
        self,
        fixture: Path,
        expected_version: str,
        expected_build: str,
        expected_variant_count: int,
    ) -> None:
        legacy = parse_23andme(fixture)
        via_dispatcher = dispatcher.parse(fixture)

        # Load-bearing Plan §13.1 ADNA-08d trio: vendor, string version, count.
        assert via_dispatcher.vendor is SourceVendor.TWENTYTHREEANDME
        assert via_dispatcher.vendor is legacy.vendor
        assert isinstance(via_dispatcher.version, str)
        assert via_dispatcher.version == expected_version
        assert via_dispatcher.version == legacy.version
        assert len(via_dispatcher.variants) == expected_variant_count
        assert len(via_dispatcher.variants) == len(legacy.variants)

        # Build + full variant-list parity: dispatcher must not re-order or
        # transform parsed variants on its way through.
        assert via_dispatcher.build == expected_build
        assert via_dispatcher.build == legacy.build
        assert via_dispatcher.variants == legacy.variants

        # Auxiliary counters round-trip identically too — guards against a
        # regression that drops or double-counts header / no-call lines
        # between the dispatcher's head-line read and the parser's body read.
        assert via_dispatcher.nocall_count == legacy.nocall_count
        assert via_dispatcher.total_lines == legacy.total_lines
        assert via_dispatcher.skipped_lines == legacy.skipped_lines

    def test_v5_file_format_composition_matches_plan_8_7(self) -> None:
        """Plan §8.7: the ingest route composes ``samples.file_format`` as
        ``f"{vendor.value}_{version}"``. Lock the v5 case so a future change
        to either the vendor enum value or the parser version string trips
        the ADNA-08d regression at the caller-visible surface, not just
        inside the parser."""
        result = dispatcher.parse(V5_FILE)
        assert f"{result.vendor.value}_{result.version}" == "23andme_v5"

    @pytest.mark.parametrize(
        "name",
        ["_looks_like_23andme", "_looks_like_ancestrydna", "_reject_with_guidance"],
    )
    def test_dispatcher_side_helpers_absent_from_parser_23andme(self, name: str) -> None:
        """ADNA-08d second clause: vendor-dispatch helpers stay on the
        dispatcher. ``parser_23andme`` may keep its vendor-*internal* version
        detector (``_detect_format``) since that's a 23andMe-only concern, but
        the vendor-routing predicates ``_looks_like_23andme`` /
        ``_looks_like_ancestrydna`` and the format-guidance fallback
        ``_reject_with_guidance`` must not have been re-imported here."""
        assert not hasattr(parser_23andme, name), (
            f"`parser_23andme.{name}` belongs in `backend.ingestion.dispatcher` — "
            "vendor dispatch was relocated there in step 29 and must not "
            "drift back into the bare 23andMe parser."
        )

    @pytest.mark.parametrize(
        "name", ["_looks_like_23andme", "_looks_like_ancestrydna", "detect_vendor"]
    )
    def test_dispatcher_side_helpers_present_on_dispatcher(self, name: str) -> None:
        """Negative sibling: the helpers do still exist on ``dispatcher``. A
        regression that moved any of them out (rather than back into
        ``parser_23andme``) trips here, so the absence-on-parser assertion
        above cannot trivially pass by way of the helpers having simply
        vanished from the codebase."""
        assert hasattr(dispatcher, name), (
            f"`dispatcher.{name}` should still live in the dispatcher — "
            "did vendor dispatch get moved out of `backend.ingestion.dispatcher`?"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Step 33 (ADNA-08a) — flipped rejection test for AncestryDNA inputs
# ═══════════════════════════════════════════════════════════════════════════


class TestDispatcherFlipForAncestryDNA:
    """The historical ``test_rejects_ancestrydna_with_message`` test asserted
    that ``parse_23andme(<AncestryDNA fixture>)`` raises a vendor-rejection
    error. After step 29 moved vendor dispatch into
    ``backend.ingestion.dispatcher`` and step 30 landed
    ``parser_ancestrydna``, the dispatcher now successfully routes
    AncestryDNA inputs to the new parser. Step 33 retired the legacy
    fixture (`sample_ancestrydna.txt`) in favor of
    `sample_ancestrydna_v2.txt` and flipped the assertion shape:

    - ``dispatcher.parse(<AncestryDNA v2 fixture>)`` succeeds with
      ``vendor == ANCESTRYDNA``, ``version == "v2.0"``, ``build ==
      "GRCh37"`` and the Plan §8.7 composed ``file_format`` shape.
    - The bare 23andMe parser still raises ``UnrecognizedVersionError`` on
      the same input — vendor dispatch is no longer the parser's job.
    """

    def test_dispatcher_parses_ancestrydna_v2_successfully(self) -> None:
        result = dispatcher.parse(ANCESTRY_V2_FILE)
        assert result.vendor is SourceVendor.ANCESTRYDNA
        assert result.version == "v2.0"
        assert result.build == "GRCh37"
        assert isinstance(result.version, str)
        assert len(result.variants) > 0
        # Plan §8.7 composed file_format shape (samples.file_format column).
        assert f"{result.vendor.value}_{result.version}" == "ancestrydna_v2.0"

    def test_parse_23andme_still_raises_on_ancestrydna_input(self) -> None:
        """Vendor dispatch is the dispatcher's job — the bare parser must
        not silently accept non-23andMe content. Locks the post-step-29
        contract on the new v2 fixture."""
        with pytest.raises(UnrecognizedVersionError):
            parse_23andme(ANCESTRY_V2_FILE)
