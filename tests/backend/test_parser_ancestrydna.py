"""Smoke tests for the AncestryDNA TSV parser (ADNA-04; step 30; Plan §8.5, §8.6).

Locks the implementation contract for ``parser_ancestrydna``: version
detection, canonical genotype form (element-wise no-call → ``"--"``, sorted
pair, mixed-case uppercase, indel ordering), PAR collapse (``chr25 → X``),
hemizygous X/Y handling, trailing blank-line tolerance, CRLF handling, and
``errors="replace"`` on stray non-UTF-8 bytes.

The bulk of positive-path + raise-path coverage lives in steps 36 and 37
(`test_parser_ancestrydna.py` extension + `test_parser_ancestrydna_raise_paths.py`).
Step 30 shipped a smoke surface against the legacy `sample_ancestrydna.txt`
fixture + inline `io.StringIO` payloads; step 33 retired the legacy file in
favor of ``sample_ancestrydna_v2.txt`` (the §8.6 edge-case-covering fixture
landed inline per step 33's ordering note while PR-2 ships ahead of PR-3 —
step 34 will expand it to bio-validator's 500–1000 rsID curation).
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from backend.ingestion.base import (
    MalformedDataError,
    ParseResult,
    SourceVendor,
    UnrecognizedVersionError,
)
from backend.ingestion.parser_ancestrydna import (
    _canonical_genotype,
    detect_version,
    parse_ancestrydna,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FIXTURE_V2 = FIXTURES / "sample_ancestrydna_v2.txt"
FIXTURE_CRLF = FIXTURES / "sample_ancestrydna_crlf.txt"
FIXTURE_NON_UTF8 = FIXTURES / "sample_ancestrydna_non_utf8_byte.txt"


# --------------------------------------------------------------------------- #
# detect_version
# --------------------------------------------------------------------------- #


class TestDetectVersion:
    def test_array_version_comment_wins(self) -> None:
        comments = [
            "#AncestryDNA raw data download",
            "# AncestryDNA array version: V2.0",
        ]
        assert detect_version(comments, has_uncommented_5col_header=True) == "v2.0"

    def test_array_version_comment_case_insensitive(self) -> None:
        comments = ["# ancestrydna ARRAY version: V3.5"]
        assert detect_version(comments, has_uncommented_5col_header=False) == "v3.5"

    def test_signature_plus_header_falls_back_to_v2(self) -> None:
        comments = ["#AncestryDNA raw data download", "# unrelated comment line"]
        assert detect_version(comments, has_uncommented_5col_header=True) == "v2.0"

    def test_no_signal_returns_unknown(self) -> None:
        assert detect_version([], has_uncommented_5col_header=True) == "unknown"
        assert detect_version(["#AncestryDNA"], has_uncommented_5col_header=False) == "unknown"


# --------------------------------------------------------------------------- #
# _canonical_genotype
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "a1,a2,expected",
    [
        # Plan §8.6 #1 — both alleles "0" → no-call.
        ("0", "0", "--"),
        # Plan §8.6 #2 — element-wise no-call rule.
        ("A", "0", "--"),
        ("0", "G", "--"),
        ("", "T", "--"),
        # Sorted-pair canonicalization for diploid SNVs.
        ("A", "G", "AG"),
        ("G", "A", "AG"),
        ("T", "C", "CT"),
        # Mixed-case uppercase (Plan §8.6 #7).
        ("a", "g", "AG"),
        ("g", "a", "AG"),
        # Hemizygous-on-haploid (Plan §8.6 #3) — both columns identical.
        ("A", "A", "AA"),
        # Indels — both orderings collapse to the same canonical pair
        # (Plan §8.6 #6).
        ("I", "D", "DI"),
        ("D", "I", "DI"),
        ("I", "I", "II"),
        ("D", "D", "DD"),
    ],
)
def test_canonical_genotype(a1: str, a2: str, expected: str) -> None:
    assert _canonical_genotype(a1, a2) == expected


# --------------------------------------------------------------------------- #
# Happy path on legacy fixture
# --------------------------------------------------------------------------- #


class TestParseV2Fixture:
    """Locks parse contract against ``sample_ancestrydna_v2.txt``.

    Step 33 retired the legacy ``sample_ancestrydna.txt`` and migrated all
    references here. The v2 fixture keeps the legacy row contract intact
    (APOE, MTHFR, chrX, sorted-pair locus) and layers in the §8.6 edge cases
    (``00`` no-call, chr25 PAR, chr26 MT, indels in both ``I/D`` and ``D/I``
    orderings, legacy ``kgp*`` passthrough, hemizygous chrY).
    """

    def test_result_shape(self) -> None:
        result = parse_ancestrydna(FIXTURE_V2)
        assert isinstance(result, ParseResult)
        assert result.vendor is SourceVendor.ANCESTRYDNA
        assert result.version == "v2.0"
        assert result.build == "GRCh37"
        assert isinstance(result.version, str)
        # Composed file_format per Plan §8.7.
        assert f"{result.vendor.value}_{result.version}" == "ancestrydna_v2.0"

    def test_variant_count_matches_data_rows(self) -> None:
        result = parse_ancestrydna(FIXTURE_V2)
        # v2 fixture (step 34 curation) has 8 comment + 1 header + 589 data
        # rows = 598 lines: a 35-row locked block (APOE, MTHFR, chrX, chrY,
        # chr25 PAR, chr26 MT, indels in both ID/DI orderings, legacy kgp*,
        # full + partial no-calls) followed by 554 hand-curated rsIDs sampled
        # from the real AncestryDNA V2.0 export — coordinates only, with
        # every genotype field synthesized deterministically per Plan §16.2.
        # total_lines counts every line in the body loop (matching the
        # 23andMe parser contract); skipped_lines counts comments + header.
        assert len(result.variants) == 589
        assert result.total_lines == 598
        assert result.skipped_lines == 9
        assert result.total_lines == result.skipped_lines + len(result.variants)

    def test_no_calls_canonicalized(self) -> None:
        """§8.6 #1 + #2 — both the partial (C/0 on chrY) and full (0/0 on
        chr1) no-call rows canonicalize to ``"--"`` and contribute to
        ``nocall_count``."""
        result = parse_ancestrydna(FIXTURE_V2)
        nocalls = {v.rsid for v in result.variants if v.genotype == "--"}
        assert nocalls == {"rsYELIZTLI2003", "rsYELIZTLI2005"}
        assert result.nocall_count == 2
        by_rsid = {v.rsid: v for v in result.variants}
        assert by_rsid["rsYELIZTLI2003"].chrom == "Y"
        assert by_rsid["rsYELIZTLI2005"].chrom == "1"

    def test_apoe_rsids_present(self) -> None:
        result = parse_ancestrydna(FIXTURE_V2)
        by_rsid = {v.rsid: v for v in result.variants}
        assert by_rsid["rs429358"].chrom == "19"
        assert by_rsid["rs429358"].genotype == "TT"
        assert by_rsid["rs7412"].chrom == "19"
        assert by_rsid["rs7412"].genotype == "CC"

    def test_chr_x_call_preserved(self) -> None:
        """rsYELIZTLI2004 (chrX 2699555 A G) is sorted to "AG" on chromosome X."""
        result = parse_ancestrydna(FIXTURE_V2)
        by_rsid = {v.rsid: v for v in result.variants}
        assert by_rsid["rsYELIZTLI2004"].chrom == "X"
        assert by_rsid["rsYELIZTLI2004"].genotype == "AG"

    def test_sorted_pair_canonicalization_on_data(self) -> None:
        """rs3892097 (G, A) must canonicalize to "AG" (sorted)."""
        result = parse_ancestrydna(FIXTURE_V2)
        by_rsid = {v.rsid: v for v in result.variants}
        assert by_rsid["rs3892097"].genotype == "AG"

    def test_chr25_par_rows_collapse_to_x(self) -> None:
        """Plan §8.6 #4 — every chr25 PAR row in the fixture lands on chrX.

        The fixture carries five PAR rows (rsYELIZTLI2006, rsYELIZTLI2019, rsYELIZTLI2020,
        rsYELIZTLI2021, rsYELIZTLI2022). None should appear as chrom "25" after parsing.
        """
        result = parse_ancestrydna(FIXTURE_V2)
        by_rsid = {v.rsid: v for v in result.variants}
        par_rsids = (
            "rsYELIZTLI2006",
            "rsYELIZTLI2019",
            "rsYELIZTLI2020",
            "rsYELIZTLI2021",
            "rsYELIZTLI2022",
        )
        for rsid in par_rsids:
            assert by_rsid[rsid].chrom == "X", rsid
        assert not any(v.chrom == "25" for v in result.variants)

    def test_chr26_rows_normalize_to_mt(self) -> None:
        """AncestryDNA mitochondrial encoding (chrom column = 26) → "MT".

        Asserts on every chr26 fixture row, not just one — locks the parser
        normalization against silent regressions that might leave chrom="26"
        for a subset of rows.
        """
        result = parse_ancestrydna(FIXTURE_V2)
        mt_rsids = (
            "rsYELIZTLI2007",
            "rsYELIZTLI2023",
            "rs199474657",
            "rs199476119",
            "rs199474672",
            "rs111033173",
            "rsYELIZTLI2024",
            "rs3899188",
            "rs2853497",
            "rs28359175",
            "rs193302985",
        )
        by_rsid = {v.rsid: v for v in result.variants}
        for rsid in mt_rsids:
            assert by_rsid[rsid].chrom == "MT", rsid
        assert not any(v.chrom == "26" for v in result.variants)

    def test_full_00_no_call_increments_nocall_count(self) -> None:
        """Plan §8.6 #1 — explicit full-`0`/`0` row contributes to nocall_count.

        Sibling of `test_no_calls_canonicalized`; locks the specific full-no-call
        row (rsYELIZTLI2005 on chr1 with raw alleles `0`/`0`) so a regression that
        flips the count without changing the canonical "--" output still trips.
        """
        result = parse_ancestrydna(FIXTURE_V2)
        by_rsid = {v.rsid: v for v in result.variants}
        assert by_rsid["rsYELIZTLI2005"].genotype == "--"
        assert by_rsid["rsYELIZTLI2005"].chrom == "1"
        # nocall_count covers BOTH no-call rows (full `0`/`0` + partial `C`/`0`).
        assert result.nocall_count == 2

    def test_kgp_rsids_in_fixture_pass_through_verbatim(self) -> None:
        """Plan §8.5 — kgp* IDs in the committed fixture survive untouched.

        Complements the inline ``test_kgp_rsids_pass_through_verbatim`` smoke
        test by asserting against the real fixture rows used in downstream
        annotation-engine coverage tests (step 40), so any mutation of kgp*
        IDs at parse time would break the documented fall-through-to-coordinate
        annotation path.
        """
        result = parse_ancestrydna(FIXTURE_V2)
        by_rsid = {v.rsid: v for v in result.variants}
        assert by_rsid["kgp12345678"].chrom == "1"
        assert by_rsid["kgp12345678"].pos == 2000000
        assert by_rsid["kgp12345678"].genotype == "AG"
        assert by_rsid["kgp98765432"].chrom == "2"
        assert by_rsid["kgp98765432"].pos == 3000000
        assert by_rsid["kgp98765432"].genotype == "CT"


# --------------------------------------------------------------------------- #
# In-memory edge cases — PAR collapse, CRLF, trailing blanks, kgp* passthrough
# --------------------------------------------------------------------------- #


_MIN_HEAD = (
    "#AncestryDNA raw data download\n"
    "#Fields are TAB-separated.\n"
    "rsid\tchromosome\tposition\tallele1\tallele2\n"
)


def _stream(*data_lines: str, newline: str = "\n") -> io.StringIO:
    header = _MIN_HEAD if newline == "\n" else _MIN_HEAD.replace("\n", newline)
    body = newline.join(data_lines)
    return io.StringIO(header + body + newline)


def test_par_25_collapses_to_x() -> None:
    """Plan §8.6 #4 — chr25 (PAR) collapses to X."""
    result = parse_ancestrydna(_stream("rs1\t25\t2700000\tA\tG"))
    assert len(result.variants) == 1
    assert result.variants[0].chrom == "X"
    assert result.variants[0].pos == 2700000
    assert result.variants[0].genotype == "AG"


def test_chr_26_normalizes_to_mt() -> None:
    """AncestryDNA mitochondrial encoding 26 → MT."""
    result = parse_ancestrydna(_stream("rs2\t26\t100\tA\tA"))
    assert result.variants[0].chrom == "MT"


def test_crlf_line_endings(tmp_path: Path) -> None:
    """Plan §8.6 #8 — CRLF tolerance, identical result to LF."""
    lf = _MIN_HEAD + "rs1\t1\t100\tA\tG\nrs2\t1\t200\tC\tT\n"
    crlf = lf.replace("\n", "\r\n")
    lf_path = tmp_path / "lf.txt"
    crlf_path = tmp_path / "crlf.txt"
    lf_path.write_text(lf, encoding="utf-8")
    crlf_path.write_text(crlf, encoding="utf-8")

    lf_result = parse_ancestrydna(lf_path)
    crlf_result = parse_ancestrydna(crlf_path)

    assert lf_result.vendor == crlf_result.vendor
    assert lf_result.version == crlf_result.version
    assert lf_result.build == crlf_result.build
    assert lf_result.variants == crlf_result.variants
    assert lf_result.nocall_count == crlf_result.nocall_count


def test_trailing_blank_lines_tolerated() -> None:
    """Plan §8.6 #5 — trailing blank lines do not break the parse."""
    stream = io.StringIO(
        _MIN_HEAD + "rs1\t1\t100\tA\tG\n" + "\n" + "\n" + "rs2\t1\t200\tC\tT\n" + "\n"
    )
    result = parse_ancestrydna(stream)
    assert len(result.variants) == 2
    # 3 head lines (2 comments + 1 header) + 3 trailing blanks = 6 skipped.
    assert result.skipped_lines == 6
    assert result.total_lines == result.skipped_lines + len(result.variants)


def test_kgp_rsids_pass_through_verbatim() -> None:
    """Plan §8.5 — legacy kgp* IDs are emitted as-is (no rewrite)."""
    result = parse_ancestrydna(_stream("kgp12345678\t1\t100\tA\tG"))
    assert result.variants[0].rsid == "kgp12345678"


def test_non_utf8_byte_in_comment_replaced(tmp_path: Path) -> None:
    """Plan §8.6 #9 — `errors='replace'` on stray bytes in comment lines."""
    p = tmp_path / "non_utf8.txt"
    payload = (
        b"#AncestryDNA raw data download\n"
        b"#Stray byte follows: \xff\n"
        b"rsid\tchromosome\tposition\tallele1\tallele2\n"
        b"rs1\t1\t100\tA\tG\n"
    )
    p.write_bytes(payload)
    result = parse_ancestrydna(p)
    assert len(result.variants) == 1
    assert result.variants[0].rsid == "rs1"


# --------------------------------------------------------------------------- #
# Committed-fixture parity — CRLF + non-UTF-8 byte (step 36; Plan §8.6 #8–9)
# --------------------------------------------------------------------------- #


class TestCommittedFixtureParity:
    """Parses the committed CRLF and stray-byte fixtures and asserts every
    ``ParseResult`` field matches the canonical LF fixture.

    The inline tmp_path versions above (``test_crlf_line_endings``,
    ``test_non_utf8_byte_in_comment_replaced``) lock the parser against
    *synthetic* CRLF / stray-byte payloads. These tests lock it against the
    *committed* fixture files referenced by Plan §16.1, so a fixture-curation
    regression (someone re-saving the CRLF fixture as LF, or fixing the stray
    byte under the impression that it was a mistake) is caught immediately
    rather than silently re-asserting parser behavior against a passing copy.
    """

    def test_crlf_fixture_parses_identically_to_lf(self) -> None:
        """Plan §8.6 #8 — every ``ParseResult`` field matches the LF fixture.

        Vendor, version, build, the full variants list (order + values), and
        every count field. The CRLF fixture is `sample_ancestrydna_v2.txt`
        verbatim with `\\r\\n` line endings; ADNA-08 mandates that the parser
        treat the two as interchangeable.
        """
        lf = parse_ancestrydna(FIXTURE_V2)
        crlf = parse_ancestrydna(FIXTURE_CRLF)
        assert lf.vendor == crlf.vendor
        assert lf.version == crlf.version
        assert lf.build == crlf.build
        assert lf.variants == crlf.variants
        assert lf.nocall_count == crlf.nocall_count
        assert lf.total_lines == crlf.total_lines
        assert lf.skipped_lines == crlf.skipped_lines

    def test_non_utf8_byte_fixture_variants_byte_identical_to_lf(self) -> None:
        """Plan §8.6 #9 — the `errors='replace'` contract is locked.

        The committed non-UTF-8 fixture is the LF fixture with a single stray
        `\\xff` inside the first comment line. The parser must complete and
        return a variants list byte-identical to the LF fixture (the stray
        byte sits in a comment, not in any variant row).
        """
        lf = parse_ancestrydna(FIXTURE_V2)
        bad = parse_ancestrydna(FIXTURE_NON_UTF8)
        assert bad.vendor is SourceVendor.ANCESTRYDNA
        assert bad.version == "v2.0"
        assert bad.build == "GRCh37"
        assert bad.variants == lf.variants
        assert bad.nocall_count == lf.nocall_count
        # total_lines / skipped_lines match too — replacement char sits in a
        # comment row, which contributes one skipped line on each side.
        assert bad.total_lines == lf.total_lines
        assert bad.skipped_lines == lf.skipped_lines


# --------------------------------------------------------------------------- #
# Truncated fixture parses cleanly (step 36; Plan §13.1 ADNA-08)
# --------------------------------------------------------------------------- #


def test_truncated_fixture_parses_to_expected_counts(tmp_path: Path) -> None:
    """ADNA-08 — a truncated slice of the V2 fixture still parses cleanly.

    Streams the head 50 lines of `sample_ancestrydna_v2.txt` (8 comment + 1
    header + 41 data rows) into a tmp file, parses it, and asserts the
    variant count matches the data-row count exactly. Locks the parser
    against a regression where a truncated/in-progress download would emit
    a misleading ``total_lines`` / ``len(variants)`` mismatch instead of a
    clean partial parse.
    """
    head_lines = FIXTURE_V2.read_text(encoding="utf-8").splitlines(keepends=True)[:50]
    truncated = tmp_path / "truncated_ancestrydna.txt"
    truncated.write_text("".join(head_lines), encoding="utf-8")

    result = parse_ancestrydna(truncated)
    # 8 comment lines + 1 header + 41 data rows = 50 total.
    assert result.total_lines == 50
    assert result.skipped_lines == 9  # 8 comments + 1 header
    assert len(result.variants) == 41
    assert result.total_lines == result.skipped_lines + len(result.variants)
    # First and last variant rows in the slice are deterministic.
    assert result.variants[0].rsid == "rs4477212"
    assert result.variants[0].chrom == "1"
    # Last row of the head-50 slice is the second kgp* row at line 38 (data
    # row 29), so the parsed list must include both kgp* IDs.
    rsids = {v.rsid for v in result.variants}
    assert {"kgp12345678", "kgp98765432"} <= rsids
    # Vendor / version / build still resolve correctly on the truncated head.
    assert result.vendor is SourceVendor.ANCESTRYDNA
    assert result.version == "v2.0"
    assert result.build == "GRCh37"


# --------------------------------------------------------------------------- #
# Negative paths — must raise, must not silently succeed
# --------------------------------------------------------------------------- #


def test_wrong_column_count_raises() -> None:
    bad = io.StringIO(
        _MIN_HEAD + "rs1\t1\t100\tA\n"  # 4 columns, not 5
    )
    with pytest.raises(MalformedDataError, match="expected 5 columns"):
        parse_ancestrydna(bad)


def test_empty_rsid_raises() -> None:
    bad = io.StringIO(_MIN_HEAD + "\t1\t100\tA\tG\n")
    with pytest.raises(MalformedDataError, match="empty rsid"):
        parse_ancestrydna(bad)


def test_invalid_chromosome_raises() -> None:
    bad = io.StringIO(_MIN_HEAD + "rs1\t27\t100\tA\tG\n")
    with pytest.raises(MalformedDataError, match="Invalid chromosome value"):
        parse_ancestrydna(bad)


def test_non_numeric_position_raises() -> None:
    bad = io.StringIO(_MIN_HEAD + "rs1\t1\tabc\tA\tG\n")
    with pytest.raises(MalformedDataError, match="non-numeric position"):
        parse_ancestrydna(bad)


def test_unknown_version_raises_when_no_signature() -> None:
    bad = io.StringIO(
        "# something else entirely\nrs1\t1\t100\tA\tG\n"  # no #AncestryDNA, no 5-col header
    )
    with pytest.raises(UnrecognizedVersionError):
        parse_ancestrydna(bad)


def test_non_seekable_stream_rejected() -> None:
    class _NonSeekable(io.StringIO):
        def seekable(self) -> bool:  # type: ignore[override]
            return False

    bad = _NonSeekable(_MIN_HEAD + "rs1\t1\t100\tA\tG\n")
    with pytest.raises(ValueError, match="seekable"):
        parse_ancestrydna(bad)
