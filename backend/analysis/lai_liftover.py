"""LAI rsID-to-GRCh38 lookup parsing and chromosome normalization.

The runtime's downstream Beagle resources use ``chr1`` through ``chr22``.
Legacy lookup tables may spell those same autosomes as bare numbers, so this
module owns the one normalization and validation contract shared by bundle
availability checks and :class:`backend.analysis.lai_runner.LAIRunner`.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

LIFTOVER_FILENAMES = ("array_site_mapping.tsv", "rsid_to_grch38.tsv")

_AUTOSOMAL_CHROM_ALIASES = {
    alias: f"chr{chrom}" for chrom in range(1, 23) for alias in (str(chrom), f"chr{chrom}")
}


def canonical_lai_autosome(chrom: str) -> str | None:
    """Return canonical ``chrN`` for supported LAI autosome spellings."""
    return _AUTOSOMAL_CHROM_ALIASES.get(chrom)


def resolve_lai_liftover_path(bundle_path: str | Path) -> Path | None:
    """Return the newest supported liftover table present in *bundle_path*."""
    liftover_dir = Path(bundle_path) / "liftover"
    for name in LIFTOVER_FILENAMES:
        candidate = liftover_dir / name
        if candidate.exists():
            return candidate
    return None


def _iter_lai_liftover_rows(
    path: str | Path,
) -> Iterator[tuple[str, str, int, bool]]:
    """Yield validated rows and whether each row maps a supported autosome."""
    mapping_path = Path(path)
    with mapping_path.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            if not raw_line.strip():
                continue
            fields = raw_line.rstrip("\r\n").split("\t")
            if len(fields) != 3:
                raise ValueError(
                    f"Invalid LAI liftover table {mapping_path} line {line_number}: "
                    "expected 3 tab-delimited fields"
                )

            rsid, raw_chrom, raw_pos = fields
            chrom = canonical_lai_autosome(raw_chrom)
            try:
                pos = int(raw_pos)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid LAI liftover table {mapping_path} line {line_number}: "
                    f"position must be an integer, got {raw_pos!r}"
                ) from exc
            if not rsid:
                raise ValueError(
                    f"Invalid LAI liftover table {mapping_path} line {line_number}: "
                    "rsID must not be empty"
                )

            yield rsid, chrom or raw_chrom, pos, chrom is not None


def load_lai_rsid_lookup(path: str | Path) -> dict[str, tuple[str, int]]:
    """Parse an LAI rsID lookup and canonicalize supported autosomes.

    The published v2 table includes a small number of alternate/random-contig
    mappings in addition to its primary autosomal rows. Those labels are kept
    so the existing VCF grouping filter can drop them; ``N`` and ``chrN`` rows
    are canonicalized to ``chrN``. A table with no usable autosomal mappings is
    invalid because it cannot support LAI.
    """
    mapping_path = Path(path)
    lookup: dict[str, tuple[str, int]] = {}
    for rsid, chrom, pos, _is_autosomal in _iter_lai_liftover_rows(mapping_path):
        lookup[rsid] = (chrom, pos)

    if not lookup:
        raise ValueError(f"Invalid LAI liftover table {mapping_path}: no mapping rows")
    if not any(canonical_lai_autosome(chrom) is not None for chrom, _pos in lookup.values()):
        raise ValueError(
            f"Invalid LAI liftover table {mapping_path}: no supported autosomal mappings"
        )
    return lookup


def _has_effective_autosomal_mapping(path: Path) -> bool:
    """Check last-write-wins rows from the end without retaining the whole table."""
    seen_rsids: set[bytes] = set()
    remainder = b""
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        while position:
            chunk_size = min(position, 64 * 1024)
            position -= chunk_size
            handle.seek(position)
            chunk = handle.read(chunk_size) + remainder
            lines = chunk.split(b"\n")
            remainder = lines[0]

            for line in reversed(lines[1:]):
                fields = line.rstrip(b"\r").split(b"\t")
                if len(fields) != 3 or not fields[0]:
                    continue
                rsid, raw_chrom, _raw_pos = fields
                if rsid in seen_rsids:
                    continue
                seen_rsids.add(rsid)
                if canonical_lai_autosome(raw_chrom.decode("utf-8")) is not None:
                    return True

        if remainder:
            fields = remainder.rstrip(b"\r").split(b"\t")
            if len(fields) == 3 and fields[0] not in seen_rsids:
                return canonical_lai_autosome(fields[1].decode("utf-8")) is not None
    return False


@lru_cache(maxsize=8)
def _validate_lai_liftover_file(path: Path, signature: tuple[int, int, int, int]) -> bool:
    """Validate one table; file metadata keys the bounded process cache."""
    if signature[0] <= 0:
        return False
    try:
        has_mapping = False
        for _rsid, _chrom, _pos, _is_autosomal in _iter_lai_liftover_rows(path):
            has_mapping = True
        return has_mapping and _has_effective_autosomal_mapping(path)
    except (OSError, UnicodeError, ValueError):
        return False


def validate_lai_liftover_bundle(bundle_path: str | Path) -> bool:
    """Return whether a bundle contains a supported, parseable liftover table."""
    path = resolve_lai_liftover_path(bundle_path)
    if path is None:
        return False
    try:
        stat = path.stat()
    except OSError:
        return False
    signature = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
    return _validate_lai_liftover_file(path, signature)
