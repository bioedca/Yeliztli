"""AncestryDNA raw-data TSV parser.

Parses AncestryDNA V2.0 raw exports (5-column TSV: ``rsid chromosome position
allele1 allele2``) into the shared :class:`backend.ingestion.base.ParseResult`
shape used by every vendor parser. Vendor dispatch (rejection of
non-AncestryDNA inputs, head-line precedence) is handled upstream by
:mod:`backend.ingestion.dispatcher` — this module assumes its caller has
already identified the file as AncestryDNA.

Plan references
---------------
- §8.5 — module skeleton (`detect_version`, `_validate_line`,
  parse-time canonicalization).
- §8.6 — the nine edge cases this parser must handle:
    1. ``00`` no-calls → canonical ``"--"`` + ``nocall_count`` increment.
    2. Element-wise partial no-call (e.g. ``A`` / ``0``) → also ``"--"`` —
       the rule is per-allele, not per-pair.
    3. Hemizygous X/Y calls on XY individuals (``A<TAB>A``) → ``"AA"`` —
       the downstream zygosity classifier handles haploid homozygous.
    4. PAR (``25``) → X collapse — wired through
       :func:`backend.ingestion.chromosomes.normalize_for` with the
       AncestryDNA map.
    5. Trailing blank lines are tolerated.
    6. Indels (``I`` / ``D``) — sorted-pair form (``DI``, ``DD``, ``II``).
       Real exports carry both ``I<TAB>D`` and ``D<TAB>I`` orderings, so
       the parse-time sort is load-bearing.
    7. Mixed-case alleles uppercased before sorting.
    8. CRLF line endings handled by stripping ``\\r\\n`` per-line.
    9. ``encoding="utf-8", errors="replace"`` — stray non-UTF-8 bytes in
       comment lines surface as replacement characters rather than
       aborting the parse.

Legacy ``kgp*`` IDs (1000 Genomes proxies) pass through verbatim — the
downstream VEP-bundle rsid index misses them but the coordinate-fallback
path picks them up (Plan §8.5 ``kgp*`` clause).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TextIO

from backend.ingestion.base import (
    MalformedDataError,
    ParsedVariant,
    ParseResult,
    SourceVendor,
    UnrecognizedVersionError,
)
from backend.ingestion.chromosomes import normalize_for

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ANCESTRYDNA_HEADER_TOKENS: tuple[str, ...] = (
    "rsid",
    "chromosome",
    "position",
    "allele1",
    "allele2",
)
_VENDOR_SIGNATURE = "#ancestrydna"
_BUILD = "GRCh37"

_DETECT_LINE_LIMIT = 50

_NO_CALL_SENTINELS: frozenset[str] = frozenset({"0", ""})

_ERR_VERSION = (
    "Header pattern not recognized as AncestryDNA v2.0. "
    "Expected a 'rsid\\tchromosome\\tposition\\tallele1\\tallele2' header "
    "after an '#AncestryDNA' comment block. "
    "Please file a GitHub issue at "
    "https://github.com/bioedca/Yeliztli/issues"
)

_ARRAY_VERSION_RE = re.compile(r"array\s+version:\s*V([0-9.]+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Version detection
# ---------------------------------------------------------------------------


def detect_version(comment_lines: list[str], has_uncommented_5col_header: bool) -> str:
    """Determine the AncestryDNA format version.

    Resolution order (Plan §8.5):

    1. Explicit ``AncestryDNA array version: V<X>`` comment → use that value.
    2. ``#AncestryDNA`` vendor signature *and* an uncommented 5-column header
       → ``"v2.0"`` (covers real exports, which usually omit the explicit
       array-version line).
    3. Fall through to ``"unknown"`` only when neither signal is present.

    The dispatcher already ensures the file looks like AncestryDNA before
    calling the parser; ``"unknown"`` here therefore represents a malformed
    AncestryDNA-shaped file rather than an unrelated vendor.
    """
    for line in comment_lines:
        match = _ARRAY_VERSION_RE.search(line)
        if match:
            return f"v{match.group(1)}"

    if has_uncommented_5col_header and any(
        _VENDOR_SIGNATURE in line.lower() for line in comment_lines
    ):
        return "v2.0"

    return "unknown"


# ---------------------------------------------------------------------------
# Line validation
# ---------------------------------------------------------------------------


def _canonical_genotype(a1: str, a2: str) -> str:
    """Canonicalize a raw allele pair.

    Element-wise no-call (Plan §8.6 #2): if either allele is empty or ``"0"``
    the call collapses to ``"--"``. Otherwise both alleles uppercase and the
    pair is sorted to absorb ``I<TAB>D`` vs ``D<TAB>I`` ordering noise from
    real exports (Plan §8.6 #6, #7).
    """
    if a1 in _NO_CALL_SENTINELS or a2 in _NO_CALL_SENTINELS:
        return "--"
    pair = sorted((a1.upper(), a2.upper()))
    return f"{pair[0]}{pair[1]}"


def _validate_line(parts: list[str], line_num: int) -> ParsedVariant:
    """Validate a single tab-split data line and return a ``ParsedVariant``."""
    if len(parts) != 5:
        raise MalformedDataError(f"Line {line_num}: expected 5 columns, got {len(parts)}")

    rsid, chrom_raw, pos_raw, a1, a2 = (p.strip() for p in parts)

    if not rsid:
        raise MalformedDataError(f"Line {line_num}: empty rsid")

    chrom = normalize_for(SourceVendor.ANCESTRYDNA, chrom_raw)

    try:
        pos = int(pos_raw)
    except ValueError:
        raise MalformedDataError(f"Line {line_num}: non-numeric position {pos_raw!r}") from None
    if pos < 0:
        raise MalformedDataError(f"Line {line_num}: negative position {pos}")

    genotype = _canonical_genotype(a1, a2)
    return ParsedVariant(rsid=rsid, chrom=chrom, pos=pos, genotype=genotype)


# ---------------------------------------------------------------------------
# Internal IO helpers
# ---------------------------------------------------------------------------


def _open_input(file_or_path: str | Path | TextIO) -> tuple[TextIO, bool]:
    """Return ``(readable text stream, should_close)``.

    Accepts a path (str / :class:`pathlib.Path`) or an already-open text
    stream. Vendor dispatch (binary rejection, format detection) is the
    dispatcher's job; this helper assumes the caller already determined the
    file is AncestryDNA.
    """
    if isinstance(file_or_path, (str, Path)):
        return (
            open(Path(file_or_path), encoding="utf-8", errors="replace"),
            True,
        )
    return file_or_path, False


def _read_head_lines(
    file_or_path: str | Path | TextIO,
    limit: int = _DETECT_LINE_LIMIT,
) -> list[str]:
    """Read up to *limit* lines from the head of the file (seekable rewind)."""
    if isinstance(file_or_path, (str, Path)):
        with open(Path(file_or_path), encoding="utf-8", errors="replace") as fh:
            return [fh.readline() for _ in range(limit)]

    stream: TextIO = file_or_path
    seekable = hasattr(stream, "seekable") and stream.seekable()
    if seekable:
        pos = stream.tell()
    lines = [stream.readline() for _ in range(limit)]
    if seekable:
        stream.seek(pos)
    return lines


def _is_5col_header(line: str) -> bool:
    """True if *line* is the uncommented 5-column AncestryDNA header."""
    cleaned = line.rstrip("\r\n")
    if not cleaned or cleaned.startswith("#"):
        return False
    cols = tuple(c.strip().lower() for c in cleaned.split("\t"))
    return cols == ANCESTRYDNA_HEADER_TOKENS


def _detect_format(file_or_path: str | Path | TextIO) -> str:
    """Inspect the file head and return the resolved version string."""
    head = _read_head_lines(file_or_path)

    comment_lines: list[str] = []
    has_uncommented_5col_header = False

    for raw_line in head:
        line = raw_line.rstrip("\r\n")
        if not line:
            continue
        if line.startswith("#"):
            comment_lines.append(line)
            continue
        if _is_5col_header(line):
            has_uncommented_5col_header = True
        break  # first non-empty / non-comment line ends the head scan

    version = detect_version(comment_lines, has_uncommented_5col_header)
    if version == "unknown":
        raise UnrecognizedVersionError(_ERR_VERSION)
    return version


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_ancestrydna(file_or_path: str | Path | TextIO) -> ParseResult:
    """Parse an AncestryDNA raw-data file and return a ``base.ParseResult``.

    The function is **pure**: it reads the file and produces an in-memory
    result — no database writes, no side effects. ``ParseResult.version`` is
    a plain string (``"v2.0"`` for the v2.0 array); callers compose
    ``f"{vendor.value}_{version}"`` into ``samples.file_format``
    (Plan §8.7).

    Raises
    ------
    ValueError
        If *file_or_path* is a non-seekable :class:`io.TextIOBase` stream.
    UnrecognizedVersionError
        If the file head carries no recognizable AncestryDNA signature.
    MalformedDataError
        If a data line has an invalid structure (wrong column count, bad
        chromosome, non-numeric position, etc.).
    """
    if not isinstance(file_or_path, (str, Path)):
        if not (hasattr(file_or_path, "seekable") and file_or_path.seekable()):
            raise ValueError(
                "TextIO streams must be seekable. Wrap non-seekable streams "
                "in io.StringIO(stream.read()) before calling "
                "parse_ancestrydna."
            )

    version = _detect_format(file_or_path)

    stream, should_close = _open_input(file_or_path)
    try:
        variants: list[ParsedVariant] = []
        nocall_count = 0
        total_lines = 0
        skipped_lines = 0
        header_seen = False

        for line_num, raw_line in enumerate(stream, start=1):
            total_lines += 1
            line = raw_line.rstrip("\r\n")

            if not line:
                skipped_lines += 1
                continue
            if line.startswith("#"):
                skipped_lines += 1
                continue
            if not header_seen and _is_5col_header(line):
                header_seen = True
                skipped_lines += 1
                continue

            parts = line.split("\t")
            variant = _validate_line(parts, line_num)
            variants.append(variant)

            if variant.genotype == "--":
                nocall_count += 1

        return ParseResult(
            vendor=SourceVendor.ANCESTRYDNA,
            version=version,
            build=_BUILD,
            variants=variants,
            nocall_count=nocall_count,
            total_lines=total_lines,
            skipped_lines=skipped_lines,
        )
    finally:
        if should_close:
            stream.close()


__all__ = [
    "detect_version",
    "parse_ancestrydna",
]
