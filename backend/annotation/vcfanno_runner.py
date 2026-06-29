"""vcfanno integration: user-supplied BED/VCF annotation overlays (P4-12).

Pure-Python implementation that intersects user-supplied BED or VCF
annotation files with a sample's variants (by chrom + position overlap
for BED, or exact chrom + pos match for VCF). Results are stored in
the ``variant_overlays`` table in the per-sample database.

Overlay configs (metadata + column names) are stored in the
``overlay_configs`` table in reference.db.

Usage::

    from backend.annotation.vcfanno_runner import (
        parse_overlay_file,
        apply_overlay,
        list_overlays,
        get_overlay,
        delete_overlay,
    )
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
import structlog

from backend.db.tables import overlay_configs, variant_overlays

logger = structlog.get_logger(__name__)

# Maximum overlay file size (5 MB)
MAX_OVERLAY_FILE_SIZE = 5_242_880

# Maximum regions/records per overlay file
MAX_OVERLAY_RECORDS = 50_000

# Valid chromosome pattern
_CHROM_RE = re.compile(r"^(chr)?\d{1,2}$|^(chr)?[XYM]$|^(chr)?MT$", re.IGNORECASE)


# ── Data classes ─────────────────────────────────────────────────────


@dataclass
class OverlayRecord:
    """A single annotation record from an overlay file."""

    chrom: str
    start: int
    end: int
    annotations: dict[str, Any]
    # VCF overlays retain the record's alleles so an allele-specific INFO annotation
    # attaches only to the matching variant, not to every variant at the coordinate
    # (issue #1228). The VCF parser emits one record per ALT allele so Number=A/R
    # INFO values can be projected by allele index (#1268). BED records leave both
    # ``None``.
    ref: str | None = None
    alt: str | None = None
    # When a sample has no allele data, VCF overlays fall back to position-only
    # matching. In that mode there is no ALT index to choose, so preserve the
    # original unprojected INFO dict instead of attaching the first ALT's value.
    position_annotations: dict[str, Any] | None = None


@dataclass
class ParsedOverlay:
    """Result of parsing an overlay file."""

    file_type: str  # "bed" or "vcf"
    column_names: list[str]
    records: list[OverlayRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def record_count(self) -> int:
        return len(self.records)


@dataclass
class OverlayConfig:
    """A stored overlay configuration."""

    id: int
    name: str
    description: str
    file_type: str
    column_names: list[str]
    region_count: int
    created_at: str | None = None


@dataclass
class ApplyResult:
    """Statistics from applying an overlay to a sample."""

    overlay_id: int
    overlay_name: str
    variants_matched: int = 0
    records_checked: int = 0


# ── Chromosome normalisation ────────────────────────────────────────


def _normalise_chrom(chrom: str) -> str:
    """Normalise chromosome name by stripping 'chr' prefix.

    Our variant tables use bare chromosome names (1, 2, ..., X, Y, MT).
    """
    c = chrom.strip()
    if c.lower().startswith("chr"):
        c = c[3:]
    # Normalise 'M' to 'MT'
    if c.upper() == "M":
        c = "MT"
    return c.upper()


# ── BED overlay parser ──────────────────────────────────────────────


def parse_bed_overlay(content: str) -> ParsedOverlay:
    """Parse a BED file with annotation columns.

    Expected format: chrom, start, end, name, [score, strand, ...extra columns].
    The header comment line ``#chrom start end name col1 col2 ...`` is used
    to name extra annotation columns. If no header is found, columns are
    named ``bed_col_4``, ``bed_col_5``, etc.

    Args:
        content: Raw text of the BED overlay file.

    Returns:
        ParsedOverlay with records and column metadata.

    Raises:
        ValueError: If no valid records found.
    """
    warnings: list[str] = []
    records: list[OverlayRecord] = []
    column_names: list[str] = []
    header_cols: list[str] | None = None

    for line_num, line in enumerate(content.splitlines(), 1):
        line = line.strip()
        if not line:
            continue

        # Header line defines column names
        if line.startswith("#"):
            parts = line.lstrip("#").strip().split("\t")
            if not parts:
                parts = line.lstrip("#").strip().split()
            if len(parts) >= 4:
                # Columns beyond chrom/start/end are annotation columns
                header_cols = [p.strip() for p in parts[3:]]
            continue

        if line.lower().startswith(("track", "browser")):
            continue

        fields = line.split("\t")
        if len(fields) < 3:
            fields = line.split()
        if len(fields) < 3:
            warnings.append(f"Line {line_num}: Skipped (fewer than 3 columns)")
            continue

        chrom_raw = fields[0].strip()
        if not _CHROM_RE.match(chrom_raw):
            warnings.append(f"Line {line_num}: Skipped invalid chromosome '{chrom_raw}'")
            continue

        try:
            start = int(fields[1].strip())
            end = int(fields[2].strip())
        except ValueError:
            warnings.append(f"Line {line_num}: Skipped (non-integer coordinates)")
            continue

        if start < 0 or end < 0 or end <= start:
            warnings.append(f"Line {line_num}: Skipped (invalid coordinates {start}-{end})")
            continue

        # Build annotation dict from extra columns
        annot: dict[str, Any] = {}
        extra_fields = fields[3:]
        for i, val in enumerate(extra_fields):
            val = val.strip()
            if header_cols and i < len(header_cols):
                col_name = header_cols[i]
            else:
                col_name = f"bed_col_{i + 4}"

            # Try numeric conversion
            annot[col_name] = _try_numeric(val)

        chrom = _normalise_chrom(chrom_raw)
        records.append(OverlayRecord(chrom=chrom, start=start, end=end, annotations=annot))

        if len(records) > MAX_OVERLAY_RECORDS:
            raise ValueError(
                f"Overlay file exceeds maximum of {MAX_OVERLAY_RECORDS} records. "
                "Please reduce the file size."
            )

    if not records:
        raise ValueError(
            "No valid BED records found in the overlay file. "
            "Expected BED format: chrom<tab>start<tab>end[<tab>col1<tab>col2...]"
        )

    # Collect all unique column names across records
    all_cols: dict[str, bool] = {}
    for rec in records:
        for k in rec.annotations:
            all_cols[k] = True
    column_names = list(all_cols.keys())

    return ParsedOverlay(
        file_type="bed",
        column_names=column_names,
        records=records,
        warnings=warnings,
    )


# ── VCF overlay parser ──────────────────────────────────────────────


def parse_vcf_overlay(content: str) -> ParsedOverlay:
    """Parse a VCF file and extract INFO field annotations as overlay columns.

    Reads ##INFO header lines to determine annotation column names,
    then extracts values from the INFO field of each record.

    Args:
        content: Raw text of the VCF overlay file.

    Returns:
        ParsedOverlay with records and column metadata.

    Raises:
        ValueError: If no valid records found.
    """
    warnings: list[str] = []
    records: list[OverlayRecord] = []
    info_ids: list[str] = []
    info_id_set: set[str] = set()
    info_numbers: dict[str, str] = {}

    for line_num, line in enumerate(content.splitlines(), 1):
        line = line.strip()
        if not line:
            continue

        # Meta-info lines: extract INFO field IDs
        if line.startswith("##"):
            if line.startswith("##INFO=<"):
                m = re.search(r"ID=([^,>]+)", line)
                if m:
                    info_id = m.group(1)
                    if info_id not in info_id_set:
                        info_ids.append(info_id)
                        info_id_set.add(info_id)
                    number_match = re.search(r"Number=([^,>]+)", line)
                    if number_match:
                        info_numbers[info_id] = number_match.group(1)
            continue

        # Header line
        if line.startswith("#CHROM") or line.startswith("#chrom"):
            continue

        # Data line: CHROM POS ID REF ALT QUAL FILTER INFO ...
        fields = line.split("\t")
        if len(fields) < 8:
            warnings.append(f"Line {line_num}: Skipped (fewer than 8 VCF columns)")
            continue

        chrom_raw = fields[0].strip()
        if not _CHROM_RE.match(chrom_raw):
            warnings.append(f"Line {line_num}: Skipped invalid chromosome '{chrom_raw}'")
            continue

        try:
            pos = int(fields[1].strip())
        except ValueError:
            warnings.append(f"Line {line_num}: Skipped (non-integer POS)")
            continue

        info_str = fields[7].strip()

        # Retain REF/ALT so allele-specific INFO attaches to the matching allele (#1228);
        # fields 3 and 4 are guaranteed present by the ``len(fields) < 8`` check above.
        ref = fields[3].strip()
        alt_field = fields[4].strip()
        alts = [alt.strip() for alt in alt_field.split(",") if alt.strip()]
        if not alts:
            alts = [alt_field]
        chrom = _normalise_chrom(chrom_raw)
        position_annot = _parse_vcf_info(info_str)

        for alt_index, alt in enumerate(alts):
            annot = _parse_vcf_info(
                info_str,
                info_numbers=info_numbers,
                alt_index=alt_index,
                alt_count=len(alts),
            )
            # VCF positions are 1-based, use pos as both start and end (point match)
            records.append(
                OverlayRecord(
                    chrom=chrom,
                    start=pos,
                    end=pos + 1,
                    annotations=annot,
                    ref=ref,
                    alt=alt,
                    position_annotations=dict(position_annot),
                )
            )

            if len(records) > MAX_OVERLAY_RECORDS:
                raise ValueError(
                    f"Overlay file exceeds maximum of {MAX_OVERLAY_RECORDS} records. "
                    "Please reduce the file size."
                )

    if not records:
        raise ValueError(
            "No valid VCF records found in the overlay file. "
            "Expected VCF format with at least "
            "CHROM, POS, ID, REF, ALT, QUAL, FILTER, INFO columns."
        )

    # Determine column names: use INFO header IDs if available, else from data
    if not info_ids:
        all_cols: dict[str, bool] = {}
        for rec in records:
            for k in rec.annotations:
                all_cols[k] = True
        info_ids = list(all_cols.keys())

    return ParsedOverlay(
        file_type="vcf",
        column_names=info_ids,
        records=records,
        warnings=warnings,
    )


def _parse_vcf_info(
    info_str: str,
    *,
    info_numbers: dict[str, str] | None = None,
    alt_index: int | None = None,
    alt_count: int | None = None,
) -> dict[str, Any]:
    """Parse a VCF INFO field into a dict of key-value pairs."""
    if info_str == "." or not info_str:
        return {}

    result: dict[str, Any] = {}
    for item in info_str.split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            key, val = item.split("=", 1)
            if info_numbers is not None and alt_index is not None and alt_count is not None:
                val = _project_vcf_info_value(
                    key,
                    val,
                    info_numbers=info_numbers,
                    alt_index=alt_index,
                    alt_count=alt_count,
                )
            result[key] = _try_numeric(val)
        else:
            # Flag field (no value)
            result[item] = True

    return result


def _project_vcf_info_value(
    key: str,
    val: str,
    *,
    info_numbers: dict[str, str],
    alt_index: int,
    alt_count: int,
) -> str:
    """Project allele-counted INFO values to the current ALT allele."""
    number = info_numbers.get(key)
    if number not in {"A", "R"}:
        return val

    values = [part.strip() for part in val.split(",")]
    if number == "A" and len(values) == alt_count:
        return values[alt_index]
    if number == "R" and len(values) == alt_count + 1:
        return values[alt_index + 1]
    return val


# ── Auto-detection ──────────────────────────────────────────────────


def detect_and_parse_overlay(content: str, filename: str) -> ParsedOverlay:
    """Auto-detect file format (BED or VCF) and parse.

    Detection order:
    1. File extension (.vcf, .vcf.gz → VCF; .bed → BED)
    2. Content heuristic (##fileformat=VCF → VCF; otherwise BED)

    Args:
        content: Raw file content.
        filename: Original filename for format detection.

    Returns:
        ParsedOverlay result.
    """
    lower_name = filename.lower()
    if lower_name.endswith((".vcf", ".vcf.gz")):
        return parse_vcf_overlay(content)
    if lower_name.endswith(".bed"):
        return parse_bed_overlay(content)

    # Heuristic: check for VCF header
    for line in content.splitlines()[:20]:
        if line.startswith("##fileformat=VCF"):
            return parse_vcf_overlay(content)

    # Default to BED
    return parse_bed_overlay(content)


# ── Helpers ─────────────────────────────────────────────────────────


def _try_numeric(val: str) -> Any:
    """Try to convert a string to int or float, else return string."""
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


# ── Database operations: overlay configs ────────────────────────────


def save_overlay_config(
    name: str,
    description: str,
    parsed: ParsedOverlay,
    engine: sa.Engine,
) -> int:
    """Save overlay config to reference.db.

    Args:
        name: Overlay display name.
        description: Overlay description.
        parsed: Parsed overlay data.
        engine: Reference DB engine.

    Returns:
        ID of the inserted overlay config row.
    """
    with engine.begin() as conn:
        result = conn.execute(
            sa.insert(overlay_configs).values(
                name=name,
                description=description,
                file_type=parsed.file_type,
                column_names=json.dumps(parsed.column_names),
                region_count=parsed.record_count,
            )
        )
        overlay_id = result.lastrowid

    logger.info(
        "overlay_config_saved",
        overlay_id=overlay_id,
        name=name,
        file_type=parsed.file_type,
        columns=parsed.column_names,
        region_count=parsed.record_count,
    )
    return overlay_id  # type: ignore[return-value]


def list_overlays(engine: sa.Engine) -> list[OverlayConfig]:
    """List all saved overlay configs."""
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(overlay_configs).order_by(overlay_configs.c.created_at.desc())
        ).fetchall()

    return [
        OverlayConfig(
            id=row.id,
            name=row.name,
            description=row.description or "",
            file_type=row.file_type,
            column_names=json.loads(row.column_names) if row.column_names else [],
            region_count=row.region_count,
            created_at=str(row.created_at) if row.created_at else None,
        )
        for row in rows
    ]


def get_overlay(overlay_id: int, engine: sa.Engine) -> OverlayConfig | None:
    """Get a single overlay config by ID."""
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(overlay_configs).where(overlay_configs.c.id == overlay_id)
        ).fetchone()

    if row is None:
        return None

    return OverlayConfig(
        id=row.id,
        name=row.name,
        description=row.description or "",
        file_type=row.file_type,
        column_names=json.loads(row.column_names) if row.column_names else [],
        region_count=row.region_count,
        created_at=str(row.created_at) if row.created_at else None,
    )


def delete_overlay(overlay_id: int, engine: sa.Engine) -> bool:
    """Delete an overlay config by ID."""
    with engine.begin() as conn:
        result = conn.execute(sa.delete(overlay_configs).where(overlay_configs.c.id == overlay_id))
    deleted = result.rowcount > 0
    if deleted:
        logger.info("overlay_config_deleted", overlay_id=overlay_id)
    return deleted


# ── Apply overlay to sample ─────────────────────────────────────────


def apply_overlay(
    parsed: ParsedOverlay,
    overlay_id: int,
    overlay_name: str,
    sample_engine: sa.Engine,
) -> ApplyResult:
    """Apply a parsed overlay to a sample's variants.

    For BED overlays: matches variants whose (chrom, pos) falls within
    any overlay region [start, end).
    For VCF overlays: matches variants allele-aware by exact (chrom, pos, ref, alt)
    when sample alleles are available (annotated_variants), so an allele-specific INFO
    value attaches only to the matching allele (#1228); falls back to (chrom, pos)-only
    matching when only raw_variants (no ref/alt) are present.

    Matched annotations are stored in the ``variant_overlays`` table.

    Args:
        parsed: Parsed overlay with records.
        overlay_id: The overlay config ID in reference.db.
        overlay_name: Display name for logging.
        sample_engine: Engine for the per-sample database.

    Returns:
        ApplyResult with match statistics.
    """
    from backend.db.tables import annotated_variants as av_table
    from backend.db.tables import raw_variants as rv_table

    result = ApplyResult(overlay_id=overlay_id, overlay_name=overlay_name)

    # Delete any previous results for this overlay on this sample. Shares the single
    # source of truth for overlay-row removal with the delete route (#854).
    delete_overlay_results(overlay_id, sample_engine)

    # Load sample variants for intersection. Prefer annotated_variants — it carries
    # ref/alt, which lets VCF overlays match allele-aware so an allele-specific INFO
    # value attaches only to the matching allele (#1228). Fall back to raw_variants
    # (no ref/alt), where only position-only matching is possible.
    alleles_available = False
    with sample_engine.connect() as conn:
        try:
            rows = conn.execute(
                sa.select(
                    av_table.c.rsid,
                    av_table.c.chrom,
                    av_table.c.pos,
                    av_table.c.ref,
                    av_table.c.alt,
                )
            ).fetchall()
            # Enable allele-aware matching only if at least one row actually carries
            # ref/alt. If annotated_variants exists but its ref/alt are all null/empty,
            # keep the (chrom, pos) fallback rather than silently dropping every VCF
            # match to an empty allele index (CodeRabbit #1228).
            alleles_available = any((row.ref or "") and (row.alt or "") for row in rows)
        except (sa.exc.NoSuchTableError, sa.exc.OperationalError):
            rows = []

        # Fall back to raw_variants if annotated_variants is empty or missing
        if not rows:
            try:
                rows = conn.execute(
                    sa.select(rv_table.c.rsid, rv_table.c.chrom, rv_table.c.pos)
                ).fetchall()
            except (sa.exc.NoSuchTableError, sa.exc.OperationalError):
                rows = []

    if not rows:
        return result

    # Position index (chrom, pos) -> rsids: used by BED ranges, and by VCF overlays
    # only when alleles are unavailable (raw_variants fallback). When ref/alt are
    # available, the allele index keys on (chrom, pos, REF, ALT) for allele-aware VCF
    # matching (#1228).
    pos_index: dict[tuple[str, int], list[str]] = {}
    allele_index: dict[tuple[str, int, str, str], list[str]] = {}
    for row in rows:
        pos_index.setdefault((row.chrom, row.pos), []).append(row.rsid)
        if alleles_available:
            ref = (row.ref or "").upper()
            alt = (row.alt or "").upper()
            if ref and alt:
                allele_index.setdefault((row.chrom, row.pos, ref, alt), []).append(row.rsid)

    result.records_checked = len(parsed.records)

    # Intersect overlay records with sample variants
    matches: list[dict] = []

    for rec in parsed.records:
        if parsed.file_type == "vcf":
            if alleles_available and rec.ref is not None and rec.alt is not None:
                # Allele-aware: attach only to sample variant(s) whose exact
                # (chrom, pos, ref, alt) matches this record. The parser emits one
                # record per ALT; split here as a compatibility guard for manually
                # constructed ParsedOverlay values that still carry multi-ALT text.
                ref_u = rec.ref.upper()
                rsids = [
                    rsid
                    for alt_tok in rec.alt.split(",")
                    if (alt_u := alt_tok.strip().upper())
                    for rsid in allele_index.get((rec.chrom, rec.start, ref_u, alt_u), [])
                ]
                annotations = rec.annotations
            else:
                # No sample alleles available (raw_variants) — position-only match.
                rsids = pos_index.get((rec.chrom, rec.start), [])
                annotations = (
                    rec.position_annotations
                    if rec.position_annotations is not None
                    else rec.annotations
                )
            for rsid in rsids:
                matches.append(
                    {
                        "rsid": rsid,
                        "overlay_id": overlay_id,
                        "annotations": json.dumps(annotations),
                    }
                )
        else:
            # BED: range overlap [start, end)
            for (chrom, pos), rsids in pos_index.items():
                if chrom == rec.chrom and rec.start <= pos < rec.end:
                    for rsid in rsids:
                        matches.append(
                            {
                                "rsid": rsid,
                                "overlay_id": overlay_id,
                                "annotations": json.dumps(rec.annotations),
                            }
                        )

    # Deduplicate by (rsid, overlay_id) — keep first match
    seen: set[str] = set()
    unique_matches: list[dict] = []
    for m in matches:
        if m["rsid"] not in seen:
            seen.add(m["rsid"])
            unique_matches.append(m)

    # Bulk insert matched overlays
    if unique_matches:
        # Batch to stay under SQLite variable limit
        batch_size = max(1, 999 // 3)  # 3 columns per row
        with sample_engine.begin() as conn:
            for i in range(0, len(unique_matches), batch_size):
                batch = unique_matches[i : i + batch_size]
                conn.execute(sa.insert(variant_overlays), batch)

    result.variants_matched = len(unique_matches)

    logger.info(
        "overlay_applied",
        overlay_id=overlay_id,
        overlay_name=overlay_name,
        variants_matched=result.variants_matched,
        records_checked=result.records_checked,
    )

    return result


def get_overlay_results(
    overlay_id: int,
    sample_engine: sa.Engine,
) -> list[dict]:
    """Get overlay results for a specific overlay on a sample.

    Returns:
        List of dicts with rsid + annotation values.
    """
    with sample_engine.connect() as conn:
        rows = conn.execute(
            sa.select(variant_overlays).where(variant_overlays.c.overlay_id == overlay_id)
        ).fetchall()

    results: list[dict] = []
    for row in rows:
        annot = json.loads(row.annotations) if row.annotations else {}
        results.append(
            {
                "rsid": row.rsid,
                "overlay_id": row.overlay_id,
                **annot,
            }
        )

    return results


def delete_overlay_results(
    overlay_id: int,
    sample_engine: sa.Engine,
) -> int:
    """Delete overlay results for a specific overlay from a sample.

    Returns:
        Number of rows deleted.
    """
    with sample_engine.begin() as conn:
        result = conn.execute(
            sa.delete(variant_overlays).where(variant_overlays.c.overlay_id == overlay_id)
        )
    return result.rowcount
