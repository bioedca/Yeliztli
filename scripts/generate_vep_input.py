#!/usr/bin/env python3
"""Generate a VEP-ready sites-only VCF.

Three modes:

* **Genotype mode (default)** — parses a vendor raw-data file (23andMe v3/v4/v5
  or AncestryDNA v2.0) via ``backend.ingestion.dispatcher.parse`` when present
  and falls back to ``parse_23andme`` otherwise. Homozygous calls emit
  ``REF=observed``, ``ALT='.'``; heterozygous calls emit the first allele as
  REF and the second as ALT. No-calls and indel codes are skipped.

* **rsID-list mode (``--rsid-list``)** — consumes the union catalog
  (``rsid<TAB>chrom<TAB>pos`` TSV) and emits a deduped, sorted list of ``rs*``
  IDs, one per line — the input for ``vep --format id``, which resolves each
  rsID and annotates *all* of its alleles independent of any donor's genotype.
  **⚠️ ``--format id`` requires a live Ensembl Variation database
  (``--database``); it does NOT work with ``--offline``/``--cache`` (VEP aborts
  with "Cannot use ID format in offline mode").** For a fully-offline rebuild,
  resolve these rsIDs to alleles and GRCh37 coordinates (for example from
  Ensembl ``variation_feature`` or a dbSNP GRCh37 VCF) and run
  ``vep --offline --cache`` on the resulting coordinate/default-format input.
  Only ``rs*`` IDs are emitted (the same filter as
  ``build_vep_bundle._load_catalog_rsids``, so the emitted set stays in lockstep
  with the coverage-gate denominator); non-``rs*`` markers have no dbSNP entry
  and rely on the runtime coordinate-fallback. The release runbook's VEP command
  must include ``--canonical`` so GRCh37 output carries the ``CANONICAL`` CSQ
  field used as the preferred-transcript tiebreaker.

* **Catalog mode (``--rsid-catalog``)** — consumes the same catalog and emits a
  sites-only VCF with ``REF=N``, ``ALT='.'`` per row. NOTE: standard ``vep
  --vcf`` cannot allele-annotate ``ALT='.'`` records — use ``--rsid-list`` +
  ``vep --format id`` for the rebuild. This mode is retained for callers that
  post-process the coordinate VCF themselves.

Output of the VCF modes is sites-only (no FORMAT/SAMPLE columns) since VEP only
needs CHROM, POS, ID, REF, ALT.

Usage::

    python scripts/generate_vep_input.py input.txt -o vep_input.vcf
    python scripts/generate_vep_input.py input.txt -o vep_input.vcf.gz
    python scripts/generate_vep_input.py --rsid-list union_sites.tsv -o vep_rsids.txt
    python scripts/generate_vep_input.py --rsid-catalog catalog.tsv -o vep_input.vcf
"""

from __future__ import annotations

import argparse
import gzip
import sys
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import IO

# Allow running from repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.ingestion.parser_23andme import parse_23andme  # noqa: E402
from backend.ingestion.vcf_export import (  # noqa: E402
    _CHROM_ORDER,
    _VALID_BASES,
    _chrom_sort_key,
)

# The Phase 1 dispatcher (`backend/ingestion/dispatcher.py`) doesn't exist yet
# (lands in step 27). Feature-flag the import so this script ships now and
# auto-picks up AncestryDNA support the moment the dispatcher is in place.
try:
    from backend.ingestion.dispatcher import parse as _dispatcher_parse  # noqa: E402

    _HAS_DISPATCHER = True
except ModuleNotFoundError as exc:  # pragma: no cover - exercised only post-step-27
    if exc.name != "backend.ingestion.dispatcher":
        raise
    _dispatcher_parse = None
    _HAS_DISPATCHER = False


# ---------------------------------------------------------------------------
# VCF generation (sites-only for VEP)
# ---------------------------------------------------------------------------

_VCF_COLUMNS_SITES = ("#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO")


def _dispatch_parse(input_path: Path):
    """Parse a vendor raw-data file via the dispatcher when available, else
    fall back to the legacy 23andMe parser. See step 1's Dep clause for why
    the dispatcher import is feature-flagged.
    """
    if _dispatcher_parse is not None:
        return _dispatcher_parse(input_path)
    return parse_23andme(input_path)


def _format_version_str(result) -> str:
    """Stringify ``ParseResult.version`` regardless of enum-vs-str shape.

    The legacy 23andMe parser returns a ``FormatVersion`` enum; the Phase 1
    dispatcher returns a plain string (per step 26).
    """
    version = getattr(result, "version", None)
    if version is None:
        return ""
    return version.value if hasattr(version, "value") else str(version)


def _genotype_to_ref_alt(genotype: str) -> tuple[str, str] | None:
    """Convert a vendor genotype to (REF, ALT) for VEP input.

    Returns None for no-calls, indels, and otherwise invalid genotypes.
    """
    if not genotype or genotype == "--":
        return None
    if len(genotype) not in (1, 2):
        return None
    if not all(c in _VALID_BASES for c in genotype):
        return None

    if len(genotype) == 1:
        return genotype, "."

    a1, a2 = genotype[0], genotype[1]
    if a1 == a2:
        return a1, "."
    return a1, a2


def _build_header_lines(source_label: str) -> list[str]:
    today = date.today().strftime("%Y%m%d")
    header: list[str] = [
        "##fileformat=VCFv4.2",
        f"##fileDate={today}",
        f"##source={source_label}",
        "##reference=GRCh37",
    ]
    for chrom in sorted(_CHROM_ORDER, key=_chrom_sort_key):
        header.append(f"##contig=<ID={chrom}>")
    return header


def _open_output(output_path: Path | None) -> tuple[IO[str], bool]:
    if output_path is None:
        return sys.stdout, False
    if str(output_path).endswith(".gz"):
        return gzip.open(output_path, "wt", encoding="utf-8"), True
    return open(output_path, "w", encoding="utf-8"), True


def generate_vep_vcf(
    input_path: Path,
    output_path: Path | None = None,
    *,
    print_stats: bool = False,
) -> dict:
    """Parse a vendor raw-data file and write a sites-only VCF for VEP.

    Args:
        input_path: Path to vendor raw data file (23andMe or AncestryDNA).
        output_path: Output VCF path (.vcf or .vcf.gz). None for stdout.
        print_stats: Print summary statistics to stderr.

    Returns:
        Dict with counts: format_version, total_parsed, written, skipped.
    """
    result = _dispatch_parse(input_path)

    variants = sorted(
        result.variants,
        key=lambda v: (_chrom_sort_key(v.chrom), v.pos),
    )

    stats = {
        "format_version": _format_version_str(result),
        "total_parsed": len(variants),
        "written": 0,
        "skipped": 0,
    }

    header_lines = _build_header_lines("Yeliztli-VEP-input-generator")
    header_lines.append(
        '##INFO=<ID=23AM,Number=0,Type=Flag,Description="Variant from vendor raw data">'
    )
    header_lines.append("\t".join(_VCF_COLUMNS_SITES))

    fh, close_fh = _open_output(output_path)
    try:
        for line in header_lines:
            fh.write(line + "\n")

        for v in variants:
            ref_alt = _genotype_to_ref_alt(v.genotype)
            if ref_alt is None:
                stats["skipped"] += 1
                continue

            ref, alt = ref_alt
            fh.write(f"{v.chrom}\t{v.pos}\t{v.rsid}\t{ref}\t{alt}\t.\tPASS\t23AM\n")
            stats["written"] += 1
    finally:
        if close_fh:
            fh.close()

    if print_stats:
        print(f"Format:   {stats['format_version']}", file=sys.stderr)
        print(f"Parsed:   {stats['total_parsed']:,} variants", file=sys.stderr)
        print(f"Written:  {stats['written']:,} sites", file=sys.stderr)
        print(f"Skipped:  {stats['skipped']:,} (no-call/indel)", file=sys.stderr)
        if output_path:
            print(f"Output:   {output_path}", file=sys.stderr)

    return stats


# ---------------------------------------------------------------------------
# rsid-catalog mode
# ---------------------------------------------------------------------------


def _iter_catalog_rows(input_path: Path) -> Iterator[tuple[str, str, int]]:
    """Yield ``(rsid, chrom, pos)`` from a bare rsid+chrom+pos TSV.

    Blank lines and ``#``-prefixed comment lines are skipped. Chromosome
    strings are left as-is (no vendor-specific PAR/MT mapping); the union
    catalog feeding this mode is already normalized upstream.
    """
    with open(input_path, encoding="utf-8") as fh:
        for line_num, raw in enumerate(fh, start=1):
            line = raw.rstrip("\n\r")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                raise ValueError(
                    f"Line {line_num}: expected 3 tab-separated columns "
                    f"(rsid, chrom, pos), got {len(parts)}"
                )
            rsid, chrom, pos_raw = (p.strip() for p in parts)
            if not rsid:
                raise ValueError(f"Line {line_num}: empty rsid")
            if not chrom:
                raise ValueError(f"Line {line_num}: empty chrom")
            try:
                pos = int(pos_raw)
            except ValueError:
                raise ValueError(f"Line {line_num}: non-numeric position {pos_raw!r}") from None
            if pos <= 0:
                raise ValueError(
                    f"Line {line_num}: non-positive position {pos_raw!r} "
                    "(VCF positions are 1-based)"
                )
            yield rsid, chrom, pos


def generate_catalog_vcf(
    input_path: Path,
    output_path: Path | None = None,
    *,
    print_stats: bool = False,
) -> dict:
    """Emit a sites-only VCF from a bare rsid+chrom+pos TSV catalog.

    REF is set to ``N`` and ALT to ``.`` for every row, since the catalog
    carries no allele information. The downstream offline VEP run resolves
    alleles via rsid lookup against the Ensembl cache.
    """
    rows = list(_iter_catalog_rows(input_path))
    rows.sort(key=lambda r: (_chrom_sort_key(r[1]), r[2]))

    stats = {"total_parsed": len(rows), "written": 0}

    header_lines = _build_header_lines("Yeliztli-rsid-catalog")
    header_lines.append("\t".join(_VCF_COLUMNS_SITES))

    fh, close_fh = _open_output(output_path)
    try:
        for line in header_lines:
            fh.write(line + "\n")
        for rsid, chrom, pos in rows:
            fh.write(f"{chrom}\t{pos}\t{rsid}\tN\t.\t.\tPASS\t.\n")
            stats["written"] += 1
    finally:
        if close_fh:
            fh.close()

    if print_stats:
        print("Mode:     rsid-catalog", file=sys.stderr)
        print(f"Parsed:   {stats['total_parsed']:,} sites", file=sys.stderr)
        print(f"Written:  {stats['written']:,} sites", file=sys.stderr)
        if output_path:
            print(f"Output:   {output_path}", file=sys.stderr)

    return stats


# ---------------------------------------------------------------------------
# rsid-list mode (for VEP --format id)
# ---------------------------------------------------------------------------


def generate_rsid_list(
    input_path: Path,
    output_path: Path | None = None,
    *,
    print_stats: bool = False,
) -> dict:
    """Emit a bare rsID list (one ``rs*`` ID per line) from a site catalog.

    Reads the same ``rsid<TAB>chrom<TAB>pos`` catalog as ``--rsid-catalog`` but
    writes the **rsID list** that ``vep --format id`` consumes: VEP looks up each
    dbSNP rsID in the offline cache and annotates *all* of its alleles, so the
    rebuilt bundle covers every user regardless of genotype. This is what a
    ``REF=N`` / ``ALT='.'`` coordinate VCF (``--rsid-catalog``) cannot do — VEP
    has no alternate allele to annotate there and drops the record.

    Only ``rs*`` IDs are emitted. Non-``rs*`` catalog markers (``i*`` internal,
    ``kgp*`` / ``VG*`` proxies, and coordinate-style ``chr:posREF>ALT`` chip IDs)
    have no dbSNP identifier VEP can resolve; they are intentionally skipped and
    rely on the runtime coordinate-fallback in ``backend/annotation/engine.py``.
    Output is deduplicated and lexicographically sorted for a byte-stable result.
    """
    rsids: set[str] = set()
    total = 0
    for rsid, _chrom, _pos in _iter_catalog_rows(input_path):
        total += 1
        # rs*-only, the same prefix test as build_vep_bundle._load_catalog_rsids
        # (the coverage-gate denominator) so this emitted set stays in lockstep
        # with it — do NOT tighten here alone. Non-rs* markers have no dbSNP
        # entry VEP can resolve.
        if rsid.startswith("rs"):
            rsids.add(rsid)

    ordered = sorted(rsids)
    stats = {
        "total_catalog_rows": total,
        "rs_written": len(ordered),
        "non_rs_skipped": total - len(ordered),
    }

    fh, close_fh = _open_output(output_path)
    try:
        for rsid in ordered:
            fh.write(rsid + "\n")
    finally:
        if close_fh:
            fh.close()

    if print_stats:
        print("Mode:     rsid-list (for VEP --format id)", file=sys.stderr)
        print(f"Catalog:  {total:,} rows", file=sys.stderr)
        print(f"rs* IDs:  {len(ordered):,} written", file=sys.stderr)
        print(f"Skipped:  {stats['non_rs_skipped']:,} non-rs* markers", file=sys.stderr)
        if output_path:
            print(f"Output:   {output_path}", file=sys.stderr)

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate VEP-ready sites-only VCF from vendor raw data or an rsid catalog.",
        epilog=(
            "Examples:\n"
            "  %(prog)s data.txt -o vep_input.vcf\n"
            "  %(prog)s --rsid-list union_sites.tsv -o vep_rsids.txt\n"
            "  %(prog)s --rsid-catalog union_sites.tsv -o vep_input.vcf\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Input file (vendor raw data, or rsid+chrom+pos TSV when --rsid-catalog is set)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output VCF path (default: stdout)",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print summary statistics to stderr",
    )
    parser.add_argument(
        "--rsid-catalog",
        dest="rsid_catalog",
        action="store_true",
        help=(
            "Treat input as a bare rsid+chrom+pos TSV catalog (the union "
            "site list produced by the bundle rebuild) instead of a vendor "
            "raw-data file."
        ),
    )
    parser.add_argument(
        "--rsid-list",
        dest="rsid_list",
        action="store_true",
        help=(
            "Treat input as an rsid+chrom+pos catalog and emit a bare rsID list "
            "(one rs* ID per line) for `vep --format id`. Non-rs* markers are "
            "skipped (they use the runtime coord-fallback). Mutually exclusive "
            "with --rsid-catalog."
        ),
    )

    args = parser.parse_args(argv)

    if args.rsid_list and args.rsid_catalog:
        print(
            "Error: --rsid-list and --rsid-catalog are mutually exclusive",
            file=sys.stderr,
        )
        sys.exit(1)

    if not args.input.is_file():
        print(f"Error: not a regular file: {args.input}", file=sys.stderr)
        sys.exit(1)

    # Auto-emit summary stats to stderr when writing to a file (PR-0z UX),
    # in addition to the explicit --stats flag. stderr never pollutes the VCF.
    print_stats = args.stats or args.output is not None

    try:
        if args.rsid_list:
            generate_rsid_list(args.input, args.output, print_stats=print_stats)
        elif args.rsid_catalog:
            generate_catalog_vcf(args.input, args.output, print_stats=print_stats)
        else:
            generate_vep_vcf(args.input, args.output, print_stats=print_stats)
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
