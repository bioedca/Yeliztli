"""dbNSFP SQLite loader and annotation lookup.

Downloads dbNSFP 5.x academic TSV (ZIP archive), extracts missense in-silico prediction
scores, and builds an indexed SQLite database (``dbnsfp.db``) with a
composite ``(chrom, pos, ref, alt)`` key.  Also provides batch lookup
functions used by the annotation engine.

The ``dbnsfp_scores`` table stores one row per variant with columns:
rsid, chrom, pos, ref, alt, cadd_phred, sift_score, sift_pred,
polyphen2_hsvar_score, polyphen2_hsvar_pred, revel, mutpred2, vest4,
metasvm, metalr, gerp_rs, phylop, mpc, primateai.

Usage::

    from backend.annotation.dbnsfp import (
        download_dbnsfp,
        load_dbnsfp_from_tsv,
        lookup_dbnsfp_by_rsids,
    )

    tsv_path = download_dbnsfp(dest_dir)
    stats = load_dbnsfp_from_tsv(tsv_path, dbnsfp_engine)
    matches = lookup_dbnsfp_by_rsids(["rs429358"], dbnsfp_engine)

(CSV is **not** a production input — see ``load_dbnsfp_from_csv``, which is a
test-fixture seed loader only.)
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import sqlalchemy as sa
import structlog

from backend.annotation.bulk_load import (
    bulk_write_connection,
    execute_write,
    insert_batch,
    retry_on_locked,
)
from backend.annotation.http_download import (
    clear_validator_sidecar,
    read_validator_sidecar,
    stream_download,
    write_validator_sidecar,
)
from backend.annotation.insilico_axes import assess_insilico_axes
from backend.annotation.sqlite_limits import SQLITE_MAX_VARIABLE_NUMBER as _SQLITE_VAR_LIMIT

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────

# dbNSFP 5.x academic download URL (TSV archive, ~50 GB)
# The distribution is a ZIP containing per-chromosome TSV files.
# download_and_load_dbnsfp() streams these through csv.DictReader.
DBNSFP_TSV_URL = "https://dist.genos.us/academic/e55b09/dbNSFP5.3.1a.zip"

# Batch sizes
BATCH_SIZE = 10_000
# Default lookup batch sizes; upgraded at module load when SQLite supports
# a higher SQLITE_MAX_VARIABLE_NUMBER (typically 32766 on Linux vs 999 on
# macOS system SQLite).  Larger batches reduce round-trip overhead.
LOOKUP_BATCH_SIZE = max(500, _SQLITE_VAR_LIMIT - 10)
POSITION_LOOKUP_BATCH_SIZE = max(249, (_SQLITE_VAR_LIMIT - 10) // 4)

# Chromosomes we accept (matching 23andMe scope)
VALID_CHROMS = {str(i) for i in range(1, 23)} | {"X", "Y", "MT"}

# dbNSFP annotation bitmask bit (bit 3, value 8)
DBNSFP_BITMASK = 0b001000

# All dbNSFP score field names (for lookup results)
DBNSFP_FIELDS = (
    "cadd_phred",
    "sift_score",
    "sift_pred",
    "polyphen2_hsvar_score",
    "polyphen2_hsvar_pred",
    "revel",
    "mutpred2",
    "vest4",
    "metasvm",
    "metalr",
    "gerp_rs",
    "phylop",
    "mpc",
    "primateai",
)


# ── SQL for dbnsfp_scores table creation ──────────────────────────────────

CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS dbnsfp_scores (
    rsid                    TEXT,
    chrom                   TEXT NOT NULL,
    pos                     INTEGER NOT NULL,
    ref                     TEXT NOT NULL,
    alt                     TEXT NOT NULL,
    cadd_phred              REAL,
    sift_score              REAL,
    sift_pred               TEXT,
    polyphen2_hsvar_score   REAL,
    polyphen2_hsvar_pred    TEXT,
    revel                   REAL,
    mutpred2                REAL,
    vest4                   REAL,
    metasvm                 REAL,
    metalr                  REAL,
    gerp_rs                 REAL,
    phylop                  REAL,
    mpc                     REAL,
    primateai               REAL,
    PRIMARY KEY (chrom, pos, ref, alt)
)
"""

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_dbnsfp_rsid ON dbnsfp_scores (rsid)",
    "CREATE INDEX IF NOT EXISTS idx_dbnsfp_chrom_pos ON dbnsfp_scores (chrom, pos)",
    # Covering index for rsid lookups (P4-22): includes all score columns so
    # the query can be satisfied entirely from the index without hitting the
    # main table.  This eliminates random I/O on the ~1.5 GB main table for
    # the primary (rsid-based) lookup path.
    (
        "CREATE INDEX IF NOT EXISTS idx_dbnsfp_rsid_covering ON dbnsfp_scores "
        "(rsid, chrom, pos, ref, alt, cadd_phred, sift_score, sift_pred, "
        "polyphen2_hsvar_score, polyphen2_hsvar_pred, revel, mutpred2, vest4, "
        "metasvm, metalr, gerp_rs, phylop, mpc, primateai)"
    ),
]

# Bulk-insert statement (idempotent upsert on the composite primary key).
_INSERT_DBNSFP_SQL = sa.text(
    "INSERT OR REPLACE INTO dbnsfp_scores "
    "(rsid, chrom, pos, ref, alt, cadd_phred, "
    "sift_score, sift_pred, polyphen2_hsvar_score, "
    "polyphen2_hsvar_pred, revel, mutpred2, vest4, "
    "metasvm, metalr, gerp_rs, phylop, mpc, primateai) "
    "VALUES (:rsid, :chrom, :pos, :ref, :alt, :cadd_phred, "
    ":sift_score, :sift_pred, :polyphen2_hsvar_score, "
    ":polyphen2_hsvar_pred, :revel, :mutpred2, :vest4, "
    ":metasvm, :metalr, :gerp_rs, :phylop, :mpc, :primateai)"
)


# ── Data classes ─────────────────────────────────────────────────────────


@dataclass
class DbNSFPRecord:
    """A single parsed dbNSFP variant record."""

    rsid: str | None
    chrom: str
    pos: int
    ref: str
    alt: str
    cadd_phred: float | None = None
    sift_score: float | None = None
    sift_pred: str | None = None
    polyphen2_hsvar_score: float | None = None
    polyphen2_hsvar_pred: str | None = None
    revel: float | None = None
    mutpred2: float | None = None
    vest4: float | None = None
    metasvm: float | None = None
    metalr: float | None = None
    gerp_rs: float | None = None
    phylop: float | None = None
    mpc: float | None = None
    primateai: float | None = None


@dataclass
class LoadStats:
    """Statistics from a dbNSFP load operation."""

    total_lines: int = 0
    variants_loaded: int = 0
    skipped_no_rsid: int = 0
    skipped_invalid_chrom: int = 0
    skipped_malformed: int = 0
    skipped_no_scores: int = 0
    sha256: str | None = None


@dataclass
class DbNSFPAnnotation(DbNSFPRecord):
    """dbNSFP annotation data with computed ensemble vote counts."""

    #: Independent in-silico axes voting deleterious (0–4, F24).
    deleterious_count: int = field(init=False)
    #: Independent in-silico axes actually assessed — the k-of-present
    #: denominator for the ensemble flag (0–4, F25).
    deleterious_total_assessed: int = field(init=False)

    def __post_init__(self) -> None:
        self.deleterious_count, self.deleterious_total_assessed = assess_ensemble(self)


# ── Ensemble pathogenicity helpers ──────────────────────────────────────


# F24: the in-silico ensemble counts *independent* evidence axes, not raw tools.
# REVEL, MetaSVM and MetaLR are meta-predictors trained on the component scores
# (REVEL ensembles 13 tools; MetaLR is MetaSVM's sibling — pairwise call
# concordance ~90%), so counting each as a separate vote triple-counts the same
# signal and the deleterious tally spikes at its maximum. The four independent
# axes are:
#   • SIFT       — sequence conservation
#   • PolyPhen-2 — protein structure
#   • CADD       — genome-wide integrative score
#   • META       — meta-predictor family (REVEL / MetaSVM / MetaLR), collapsed
# Each axis votes deleterious / not-deleterious, or is *absent* when no
# underlying predictor is present (F25).


def assess_ensemble(annot: DbNSFPRecord) -> tuple[int, int]:
    """Return ``(deleterious_axes, assessed_axes)`` over the four independent axes.

    F24 collapses the correlated meta-predictors into a single axis; F25 makes
    the denominator the axes *actually assessed* so the ensemble flag is
    k-of-present, never k-of-a-fixed-5 that silently penalises a variant for
    predictors dbNSFP simply does not cover.

    Returns:
        ``(deleterious, assessed)`` — axes voting deleterious and axes with data,
        each 0–4.
    """
    return assess_insilico_axes(annot)


#: Minimum independent axes that must be assessable before the ensemble flag can
#: fire — a "majority" of a single axis is not corroborating evidence (F25).
ENSEMBLE_MIN_AXES = 2


def is_ensemble_pathogenic_from_counts(deleterious: int, assessed: int) -> bool:
    """Ensemble rule: a strict majority of the *present* axes vote deleterious.

    Requires at least :data:`ENSEMBLE_MIN_AXES` axes assessed, so the flag never
    fires on a single predictor's say-so (F24/F25).
    """
    if assessed < ENSEMBLE_MIN_AXES:
        return False
    return deleterious * 2 > assessed


def is_ensemble_pathogenic(annot: DbNSFPRecord) -> bool:
    """Whether the in-silico ensemble supports pathogenicity (F24/F25)."""
    deleterious, assessed = assess_ensemble(annot)
    return is_ensemble_pathogenic_from_counts(deleterious, assessed)


# ── Helpers ──────────────────────────────────────────────────────────────


def _normalize_chrom(chrom: str) -> str | None:
    """Normalize chromosome name. Returns None for invalid chromosomes."""
    c = chrom.removeprefix("chr").upper()
    if c in VALID_CHROMS:
        return c
    return None


def _parse_float(value: str | None) -> float | None:
    """Parse a float, returning None on failure or missing sentinel."""
    if value is None or value in (".", "", "-"):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_dbnsfp_float(value: str | None) -> float | None:
    """Parse a dbNSFP float value that may contain multiple semicolon-delimited scores.

    dbNSFP stores multiple transcript-level scores separated by semicolons.
    We take the first non-missing value (first-transcript approach).
    """
    if value is None or value in (".", "", "-"):
        return None
    # dbNSFP uses ';' to separate multiple transcript scores
    if ";" in value:
        for part in value.split(";"):
            result = _parse_float(part.strip())
            if result is not None:
                return result
        return None
    return _parse_float(value)


# dbNSFP transcript-specific predictor fields (SIFT4G_score/pred,
# Polyphen2_HVAR_score/pred, …) separate the per-transcript values with ``;``
# WITHIN a single record, but when a variant matches multiple dbNSFP records the
# matched values are joined with ``,`` (e.g. SIFT4G_score "0.2,0.01"). Both
# delimiters can appear — even mixed — so split on either before aggregating;
# splitting on ``;`` alone left a comma-joined damaging transcript un-parsed,
# under-calling the in-silico axis (#983). These fields are numeric scores or
# single-letter prediction codes, so they never carry an intra-value ``,``/``;``
# — splitting on both is lossless.
_DBNSFP_MULTIVALUE_DELIM = re.compile(r"[;,]")


def _split_dbnsfp_multivalue(value: str) -> list[str]:
    """Split a dbNSFP multi-transcript field on either delimiter (``;`` or ``,``)."""
    return _DBNSFP_MULTIVALUE_DELIM.split(value)


def _parse_dbnsfp_float_extreme(value: str | None, *, pick: str) -> float | None:
    """Parse a multi-transcript dbNSFP score and choose the requested extreme."""
    if value is None or value in (".", "", "-"):
        return None
    parsed = [
        result
        for part in _split_dbnsfp_multivalue(value)
        if (result := _parse_float(part.strip())) is not None
    ]
    if not parsed:
        return None
    if pick == "min":
        return min(parsed)
    if pick == "max":
        return max(parsed)
    raise ValueError(f"unsupported dbNSFP float aggregation: {pick}")


def _parse_dbnsfp_pred_by_severity(
    value: str | None,
    *,
    severity: dict[str, int],
) -> str | None:
    """Parse a multi-transcript dbNSFP prediction and choose the worst prediction."""
    if value is None or value in (".", "", "-"):
        return None

    fallback: str | None = None
    best_value: str | None = None
    best_rank = -1
    for part in _split_dbnsfp_multivalue(value):
        pred = part.strip()
        if not pred or pred in {".", "-"}:
            continue
        if fallback is None:
            fallback = pred
        rank = severity.get(pred.lower())
        if rank is not None and rank > best_rank:
            best_value = pred
            best_rank = rank

    return best_value if best_value is not None else fallback


def _compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 checksum of a file."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


def _wal_checkpoint(engine: sa.Engine) -> None:
    """Run WAL checkpoint if the engine is file-backed."""
    url = str(engine.url)
    if url == "sqlite://" or ":memory:" in url:
        return
    with engine.connect() as conn:
        conn.execute(sa.text("PRAGMA wal_checkpoint(TRUNCATE)"))
        conn.commit()


# ── TSV parsing ──────────────────────────────────────────────────────────


def parse_dbnsfp_tsv_line(
    fields: dict[str, str],
) -> tuple[DbNSFPRecord | None, str | None]:
    """Parse a single dbNSFP TSV row (as a dict from DictReader).

    Returns:
        Tuple of (record, skip_reason). If record is None, skip_reason
        indicates why the line was skipped.
    """
    # Extract chromosome
    chrom_raw = fields.get("#chr", "")
    chrom = _normalize_chrom(chrom_raw)
    if chrom is None:
        return None, "invalid_chrom"

    # Extract position
    try:
        pos = int(fields.get("pos(1-based)", ""))
    except (ValueError, TypeError):
        return None, "malformed"

    ref = fields.get("ref", "")
    alt = fields.get("alt", "")
    if not ref or not alt:
        return None, "malformed"

    # Extract rsid
    rsid_raw = fields.get("rs_dbSNP", "")
    rsid: str | None = None
    if rsid_raw and rsid_raw != ".":
        # May have multiple rsids; take first
        if ";" in rsid_raw:
            for part in rsid_raw.split(";"):
                if part.startswith("rs"):
                    rsid = part
                    break
        elif rsid_raw.startswith("rs"):
            rsid = rsid_raw

    # Parse scores
    cadd_phred = _parse_dbnsfp_float(fields.get("CADD_phred"))
    sift_score = _parse_dbnsfp_float_extreme(fields.get("SIFT4G_score"), pick="min")
    sift_pred = _parse_dbnsfp_pred_by_severity(
        fields.get("SIFT4G_pred"),
        severity={"t": 0, "d": 1},
    )
    polyphen2_score = _parse_dbnsfp_float_extreme(fields.get("Polyphen2_HVAR_score"), pick="max")
    polyphen2_pred = _parse_dbnsfp_pred_by_severity(
        fields.get("Polyphen2_HVAR_pred"),
        severity={
            "b": 0,
            "benign": 0,
            "p": 1,
            "possibly_damaging": 1,
            "possibly damaging": 1,
            "d": 2,
            "probably_damaging": 2,
            "probably damaging": 2,
        },
    )
    revel = _parse_dbnsfp_float(fields.get("REVEL_score"))
    # dbNSFP 5.x distributes MutPred2 under ``MutPred2_score`` (F31). The old
    # ``MutPred_score`` key never matched, leaving the column 100% NULL.
    mutpred2 = _parse_dbnsfp_float(fields.get("MutPred2_score"))
    vest4 = _parse_dbnsfp_float(fields.get("VEST4_score"))
    metasvm = _parse_dbnsfp_float(fields.get("MetaSVM_score"))
    metalr = _parse_dbnsfp_float(fields.get("MetaLR_score"))
    gerp_rs = _parse_dbnsfp_float(fields.get("GERP++_RS"))
    phylop = _parse_dbnsfp_float(fields.get("phyloP100way_vertebrate"))
    mpc = _parse_dbnsfp_float(fields.get("MPC_score"))
    primateai = _parse_dbnsfp_float(fields.get("PrimateAI_score"))

    # Skip if no scores at all
    all_scores = [
        cadd_phred,
        sift_score,
        polyphen2_score,
        revel,
        mutpred2,
        vest4,
        metasvm,
        metalr,
        gerp_rs,
        phylop,
        mpc,
        primateai,
    ]
    if all(s is None for s in all_scores) and sift_pred is None and polyphen2_pred is None:
        return None, "no_scores"

    record = DbNSFPRecord(
        rsid=rsid,
        chrom=chrom,
        pos=pos,
        ref=ref,
        alt=alt,
        cadd_phred=cadd_phred,
        sift_score=sift_score,
        sift_pred=sift_pred,
        polyphen2_hsvar_score=polyphen2_score,
        polyphen2_hsvar_pred=polyphen2_pred,
        revel=revel,
        mutpred2=mutpred2,
        vest4=vest4,
        metasvm=metasvm,
        metalr=metalr,
        gerp_rs=gerp_rs,
        phylop=phylop,
        mpc=mpc,
        primateai=primateai,
    )

    return record, None


def _iter_dbnsfp_single_file(
    fh,
    stats: LoadStats,
    progress_callback: Callable[[int], None] | None,
) -> Iterator[tuple[dict, LoadStats]]:
    """Parse rows from a single dbNSFP TSV file handle."""
    reader = csv.DictReader(fh, delimiter="\t")
    for fields in reader:
        stats.total_lines += 1

        record, skip_reason = parse_dbnsfp_tsv_line(fields)

        if record is None:
            if skip_reason == "invalid_chrom":
                stats.skipped_invalid_chrom += 1
            elif skip_reason == "no_scores":
                stats.skipped_no_scores += 1
            else:
                stats.skipped_malformed += 1
            continue

        stats.variants_loaded += 1

        row = _record_to_dict(record)

        if progress_callback and stats.total_lines % 100_000 == 0:
            progress_callback(stats.total_lines)

        yield row, stats


def iter_dbnsfp_tsv(
    tsv_path: Path,
    *,
    progress_callback: Callable[[int], None] | None = None,
) -> Iterator[tuple[dict, LoadStats]]:
    """Iterate over dbNSFP TSV rows lazily, yielding (row_dict, stats).

    Memory-efficient: yields one row at a time for streaming inserts.
    Handles plain text, gzip-compressed, and ZIP archives (containing
    per-chromosome gzipped TSV files as distributed by dbNSFP).

    Args:
        tsv_path: Path to the dbNSFP file (.tsv, .tsv.gz, or .zip).
        progress_callback: Optional callback called with parsed line count
            at regular intervals.

    Yields:
        Tuple of (row dict ready for insert, running LoadStats).
    """
    stats = LoadStats()

    if tsv_path.suffix == ".zip":
        # dbNSFP ZIP archive: contains per-chromosome files like
        # dbNSFP5.3.1a_variant.chr1.gz (gzipped TSVs)
        with zipfile.ZipFile(tsv_path, "r") as zf:
            members = sorted(
                n for n in zf.namelist() if "_variant.chr" in n and not n.startswith("__MACOSX")
            )
            logger.info("dbnsfp_zip_members", count=len(members), files=members[:3])
            for member in members:
                logger.info("dbnsfp_processing_member", member=member)
                with zf.open(member) as raw_fh:
                    if member.endswith(".gz"):
                        fh = io.TextIOWrapper(gzip.open(raw_fh, "rb"), encoding="utf-8")
                    else:
                        fh = io.TextIOWrapper(raw_fh, encoding="utf-8")
                    yield from _iter_dbnsfp_single_file(fh, stats, progress_callback)
    else:
        open_fn = gzip.open if tsv_path.suffix == ".gz" else open
        with open_fn(tsv_path, "rt", encoding="utf-8") as fh:  # type: ignore[call-overload]
            yield from _iter_dbnsfp_single_file(fh, stats, progress_callback)


def _record_to_dict(record: DbNSFPRecord) -> dict:
    """Convert a DbNSFPRecord to a dict suitable for DB insertion."""
    return {
        "rsid": record.rsid,
        "chrom": record.chrom,
        "pos": record.pos,
        "ref": record.ref,
        "alt": record.alt,
        "cadd_phred": record.cadd_phred,
        "sift_score": record.sift_score,
        "sift_pred": record.sift_pred,
        "polyphen2_hsvar_score": record.polyphen2_hsvar_score,
        "polyphen2_hsvar_pred": record.polyphen2_hsvar_pred,
        "revel": record.revel,
        "mutpred2": record.mutpred2,
        "vest4": record.vest4,
        "metasvm": record.metasvm,
        "metalr": record.metalr,
        "gerp_rs": record.gerp_rs,
        "phylop": record.phylop,
        "mpc": record.mpc,
        "primateai": record.primateai,
    }


# ── Database creation & loading ──────────────────────────────────────────


def _create_dbnsfp_table(engine: sa.Engine) -> None:
    """Create only the dbnsfp_scores table (no indexes). Safe to call repeatedly."""
    with engine.begin() as conn:
        conn.execute(sa.text(CREATE_TABLE_SQL))


def _create_dbnsfp_indexes(engine: sa.Engine) -> None:
    """Create the dbnsfp_scores indexes (idempotent). Retries on lock contention.

    Building the indexes — especially the wide ``idx_dbnsfp_rsid_covering`` — once
    over a fully populated table is far cheaper than maintaining them per-row
    across tens of millions of inserts, so the load path defers index creation
    to after the bulk insert and calls this.
    """

    def _do() -> None:
        with engine.begin() as conn:
            for idx_sql in CREATE_INDEXES_SQL:
                conn.execute(sa.text(idx_sql))

    retry_on_locked(_do)


def load_dbnsfp_from_tsv(
    tsv_path: Path,
    engine: sa.Engine,
    *,
    clear_existing: bool = True,
    progress_callback: Callable[[int], None] | None = None,
) -> LoadStats:
    """Parse a dbNSFP TSV and load scores into the dbnsfp_scores table.

    Uses streaming parse + batch insert to keep memory usage low.

    Args:
        tsv_path: Path to the dbNSFP TSV (.tsv or .tsv.gz).
        engine: SQLAlchemy engine for dbnsfp.db.
        clear_existing: Whether to DELETE all existing rows first.
        progress_callback: Called with parsed line count at intervals.

    Returns:
        LoadStats with counts and metadata.
    """
    # Create the table only; indexes are built once after the bulk insert.
    _create_dbnsfp_table(engine)

    batch: list[dict] = []
    final_stats = LoadStats()

    with bulk_write_connection(engine) as conn:
        if clear_existing:
            execute_write(conn, sa.text("DELETE FROM dbnsfp_scores"))

        for row, final_stats in iter_dbnsfp_tsv(tsv_path, progress_callback=progress_callback):
            batch.append(row)

            if len(batch) >= BATCH_SIZE:
                insert_batch(conn, _INSERT_DBNSFP_SQL, batch)
                batch = []

        # Flush remaining
        if batch:
            insert_batch(conn, _INSERT_DBNSFP_SQL, batch)

    # Build indexes over the populated table, then truncate the WAL.
    _create_dbnsfp_indexes(engine)
    _wal_checkpoint(engine)

    logger.info(
        "dbnsfp_tsv_loaded",
        variants=final_stats.variants_loaded,
        skipped_no_rsid=final_stats.skipped_no_rsid,
        skipped_invalid_chrom=final_stats.skipped_invalid_chrom,
        skipped_no_scores=final_stats.skipped_no_scores,
        skipped_malformed=final_stats.skipped_malformed,
    )

    return final_stats


def load_dbnsfp_from_csv(
    csv_path: Path,
    engine: sa.Engine,
    *,
    clear_existing: bool = True,
) -> LoadStats:
    """Seed the ``dbnsfp_scores`` table from a small CSV fixture — TEST SUPPORT ONLY.

    CSV is **not** a production or bundle-build input format: the real pipeline
    loads dbNSFP from its native academic TSV via :func:`load_dbnsfp_from_tsv`
    (``download_and_load_dbnsfp``). This loader exists solely so tests can seed
    the table from a compact CSV fixture instead of standing up the full TSV
    machinery; it is on no production/build path. The CSV is expected to have
    columns matching the dbnsfp_scores table exactly:
    rsid, chrom, pos, ref, alt, cadd_phred, ..., primateai.

    Args:
        csv_path: Path to the CSV fixture with dbNSFP data.
        engine: SQLAlchemy engine for dbnsfp.db.
        clear_existing: Whether to DELETE all existing rows first.

    Returns:
        LoadStats with counts.
    """
    # Create the table only; indexes are built once after the bulk insert.
    _create_dbnsfp_table(engine)

    stats = LoadStats()
    batch: list[dict] = []

    with bulk_write_connection(engine) as conn:
        if clear_existing:
            execute_write(conn, sa.text("DELETE FROM dbnsfp_scores"))

        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                stats.total_lines += 1
                for required in ("chrom", "pos", "ref", "alt"):
                    if required not in row:
                        msg = f"Missing required column '{required}' in CSV"
                        raise ValueError(msg)
                batch.append(
                    {
                        "rsid": row.get("rsid") or None,
                        "chrom": row["chrom"],
                        "pos": int(row["pos"]),
                        "ref": row["ref"],
                        "alt": row["alt"],
                        "cadd_phred": _parse_float(row.get("cadd_phred")),
                        "sift_score": _parse_float(row.get("sift_score")),
                        "sift_pred": row.get("sift_pred") or None,
                        "polyphen2_hsvar_score": _parse_float(row.get("polyphen2_hsvar_score")),
                        "polyphen2_hsvar_pred": row.get("polyphen2_hsvar_pred") or None,
                        "revel": _parse_float(row.get("revel")),
                        "mutpred2": _parse_float(row.get("mutpred2")),
                        "vest4": _parse_float(row.get("vest4")),
                        "metasvm": _parse_float(row.get("metasvm")),
                        "metalr": _parse_float(row.get("metalr")),
                        "gerp_rs": _parse_float(row.get("gerp_rs")),
                        "phylop": _parse_float(row.get("phylop")),
                        "mpc": _parse_float(row.get("mpc")),
                        "primateai": _parse_float(row.get("primateai")),
                    }
                )
                stats.variants_loaded += 1

                if len(batch) >= BATCH_SIZE:
                    insert_batch(conn, _INSERT_DBNSFP_SQL, batch)
                    batch = []

        if batch:
            insert_batch(conn, _INSERT_DBNSFP_SQL, batch)

    _create_dbnsfp_indexes(engine)
    _wal_checkpoint(engine)

    logger.info("dbnsfp_csv_loaded", variants=stats.variants_loaded)
    return stats


# ── Download ─────────────────────────────────────────────────────────────


def download_dbnsfp(
    dest_dir: Path,
    *,
    url: str = DBNSFP_TSV_URL,
    progress_callback: Callable[[int, int | None], None] | None = None,
    timeout: float = 3600.0,
) -> Path:
    """Download the dbNSFP database file.

    Writes to a temporary file and renames on success to avoid
    leaving partial files on failure.

    Args:
        dest_dir: Directory to save the downloaded file.
        url: Override URL (useful for testing).
        progress_callback: Called with (bytes_downloaded, total_bytes).
        timeout: HTTP request timeout in seconds.

    Returns:
        Path to the downloaded file.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / "dbnsfp_archive.zip"
    tmp_path = dest_dir / "dbnsfp_archive.zip.tmp"

    logger.info("dbnsfp_download_start", url=url)

    # Resumable so a partial ~47 GB archive survives a failed/restarted build and
    # the next run continues via HTTP Range instead of re-downloading from zero.
    # The validator sidecar persists the partial's If-Range token across runs so
    # a rotated upstream forces a clean restart rather than a corrupt splice.
    outcome = stream_download(
        url,
        tmp_path,
        progress_callback=progress_callback,
        timeout=timeout,
        resumable=True,
        validator=read_validator_sidecar(tmp_path),
        on_validator=lambda v: write_validator_sidecar(tmp_path, v),
    )

    # Atomic rename on success; drop the now-stale validator sidecar.
    tmp_path.replace(dest_path)
    clear_validator_sidecar(tmp_path)

    logger.info("dbnsfp_download_complete", path=str(dest_path), bytes=outcome.total_bytes)
    return dest_path


def _cleanup_dbnsfp_archive(path: Path) -> None:
    """Remove the transient dbNSFP source archive after the DB has been built."""
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("dbnsfp_archive_cleanup_failed", path=str(path), error=str(exc))
    else:
        logger.info("dbnsfp_archive_cleanup_complete", path=str(path))


def download_and_load_dbnsfp(
    dbnsfp_engine: sa.Engine,
    dest_dir: Path,
    *,
    url: str = DBNSFP_TSV_URL,
    download_progress: Callable[[int, int | None], None] | None = None,
    parse_progress: Callable[[int], None] | None = None,
    timeout: float = 3600.0,
    reference_engine: sa.Engine | None = None,
) -> LoadStats:
    """Full pipeline: download dbNSFP, parse, and load into dbnsfp.db.

    Args:
        dbnsfp_engine: SQLAlchemy engine for dbnsfp.db.
        dest_dir: Directory for downloaded files.
        url: dbNSFP download URL (override for testing).
        download_progress: Callback for download progress.
        parse_progress: Callback for parse progress.
        timeout: HTTP timeout in seconds.
        reference_engine: Optional engine for reference.db to record version.

    Returns:
        LoadStats with counts and metadata.
    """
    # Download
    downloaded_path = download_dbnsfp(
        dest_dir,
        url=url,
        progress_callback=download_progress,
        timeout=timeout,
    )
    archive_size = downloaded_path.stat().st_size

    # Compute checksum
    sha256 = _compute_sha256(downloaded_path)

    # Parse and load
    stats = load_dbnsfp_from_tsv(
        downloaded_path,
        dbnsfp_engine,
        progress_callback=parse_progress,
    )
    stats.sha256 = sha256

    # Record version in reference.db
    if reference_engine is not None:
        record_dbnsfp_version(
            reference_engine,
            version="5.3.1a",
            file_path=None,
            file_size_bytes=archive_size,
            checksum=sha256,
        )

    _cleanup_dbnsfp_archive(downloaded_path)
    return stats


# ── Version tracking ─────────────────────────────────────────────────────


def _parse_version_tuple(version: str) -> tuple[int, ...]:
    """Parse a dbNSFP release tag into a tuple of integer components.

    dbNSFP tags follow ``MAJOR.MINOR.PATCH[suffix]`` (e.g. ``5.3.1a``).  We
    strip any leading non-numeric prefix, split on ``.``, and reduce each
    component to its leading run of digits (so ``1a`` → ``1``), discarding
    components with no digits.  This yields a tuple suitable for numeric
    ordering, fixing the string-compare misorder of e.g. ``5.10.0`` vs
    ``5.9.0``.
    """
    stripped = version.lstrip("vVrR")
    components: list[int] = []
    for part in stripped.split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            components.append(int(digits))
    return tuple(components)


def _version_at_least(current: str, target: str) -> bool:
    """Return True if ``current`` is the same as or newer than ``target``.

    Compares numeric version components (zero-padding the shorter tuple) so
    that e.g. ``5.10.0`` correctly sorts after ``5.9.0``.
    """
    cur = _parse_version_tuple(current)
    tgt = _parse_version_tuple(target)
    length = max(len(cur), len(tgt))
    cur += (0,) * (length - len(cur))
    tgt += (0,) * (length - len(tgt))
    return cur >= tgt


def record_dbnsfp_version(
    engine: sa.Engine,
    *,
    version: str,
    file_path: str | None = None,
    file_size_bytes: int | None = None,
    checksum: str | None = None,
) -> None:
    """Insert or update the dbNSFP version in the database_versions table."""
    from backend.db.database_registry import _record_db_version

    _record_db_version(
        engine,
        db_name="dbnsfp",
        version=version,
        file_size_bytes=file_size_bytes,
        sha256=checksum,
        file_path=file_path,
    )


def check_dbnsfp_update(
    reference_engine: sa.Engine,
    settings: object | None = None,
    *,
    timeout: float = 30.0,
):
    """Check whether the dbNSFP release pinned in the manifest is newer than installed.

    Uses ``pipeline_pins["dbnsfp"]`` from ``bundles/manifest.json`` as the
    authoritative source for the latest URL + release tag, then performs an
    HTTP HEAD on the pinned URL to confirm reachability and obtain a
    download-size estimate for the bandwidth-window check. Returns ``None``
    when the manifest pin is missing/unreachable, the HEAD call fails, or
    the recorded version is the same as or newer than the manifest pin
    (numeric component compare on the release tag — dbNSFP tags follow
    ``MAJOR.MINOR.PATCH[suffix]``).

    Args:
        reference_engine: Reference DB engine for ``database_versions`` lookup.
        settings: Accepted for dispatch-signature parity with other
            ``check_*_update`` functions; unused.
        timeout: HTTP timeout in seconds for both the manifest fetch and HEAD.

    Returns:
        ``VersionInfo`` when the manifest pin is newer than the installed
        version, otherwise ``None``.
    """
    del settings  # unused; kept for dispatch-signature parity
    from backend.db.manifest import get_pipeline_pin
    from backend.db.update_manager import VersionInfo, get_current_version

    pin = get_pipeline_pin("dbnsfp", timeout=timeout)
    if pin is None or not pin.last_known_version:
        return None

    current = get_current_version(reference_engine, "dbnsfp")
    if current is not None and _version_at_least(current, pin.last_known_version):
        return None

    download_size = 0
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout, connect=10.0),
        ) as client:
            resp = client.head(pin.url)
            resp.raise_for_status()
            content_length = resp.headers.get("Content-Length")
            if content_length:
                download_size = int(content_length)
    except Exception as exc:
        logger.warning("dbnsfp_update_check_failed", error=str(exc))
        return None

    return VersionInfo(
        db_name="dbnsfp",
        latest_version=pin.last_known_version,
        download_url=pin.url,
        download_size_bytes=download_size,
    )


# ── Annotation lookup ────────────────────────────────────────────────────


def _dbnsfp_row_to_annotation(row: sa.Row) -> DbNSFPAnnotation:
    """Build a :class:`DbNSFPAnnotation` from a ``dbnsfp_scores`` row."""
    return DbNSFPAnnotation(
        rsid=row.rsid,
        chrom=row.chrom,
        pos=row.pos,
        ref=row.ref,
        alt=row.alt,
        cadd_phred=row.cadd_phred,
        sift_score=row.sift_score,
        sift_pred=row.sift_pred,
        polyphen2_hsvar_score=row.polyphen2_hsvar_score,
        polyphen2_hsvar_pred=row.polyphen2_hsvar_pred,
        revel=row.revel,
        mutpred2=row.mutpred2,
        vest4=row.vest4,
        metasvm=row.metasvm,
        metalr=row.metalr,
        gerp_rs=row.gerp_rs,
        phylop=row.phylop,
        mpc=row.mpc,
        primateai=row.primateai,
    )


def _pick_dbnsfp_row(rows: list[sa.Row], genotype: str | None) -> sa.Row:
    """Select the dbNSFP row whose ALT the sample carries (F11).

    A multi-allelic site has one ``dbnsfp_scores`` row per ALT, with different
    in-silico scores per ALT. The old code kept whichever row SQLite returned
    last, so the stored scores (and the ensemble_pathogenic flag) could come
    from an ALT the sample does not carry. Pick the carried ALT instead; when
    carriage is indeterminate (hom-ref / no-call / no genotype) fall back to a
    *deterministic* choice (lowest ALT then REF) rather than row order.
    """
    if len(rows) == 1:
        return rows[0]
    if genotype:
        from backend.analysis.zygosity import CARRIED_ZYGOSITIES, classify_zygosity

        carried = [
            r for r in rows if classify_zygosity(genotype, r.ref, r.alt) in CARRIED_ZYGOSITIES
        ]
        if carried:
            rows = carried
    return min(rows, key=lambda r: ((r.alt or ""), (r.ref or "")))


def lookup_dbnsfp_by_rsids(
    rsids: list[str],
    dbnsfp_engine: sa.Engine,
    genotype_by_rsid: dict[str, str] | None = None,
) -> dict[str, DbNSFPAnnotation]:
    """Look up dbNSFP scores for a batch of rsids.

    Processes in batches of 500 to stay under SQLite's 999-variable limit.

    Args:
        rsids: List of rsid strings (e.g. ["rs429358", "rs7412"]).
        dbnsfp_engine: SQLAlchemy engine for dbnsfp.db.
        genotype_by_rsid: Optional sample genotypes; when given, a multi-allelic
            site resolves to the row whose ALT the sample carries (F11) instead
            of an arbitrary row.

    Returns:
        Dict mapping rsid → DbNSFPAnnotation for matched variants.
    """
    if not rsids:
        return {}

    genotype_by_rsid = genotype_by_rsid or {}
    rows_by_rsid: dict[str, list[sa.Row]] = {}

    with dbnsfp_engine.connect() as conn:
        for i in range(0, len(rsids), LOOKUP_BATCH_SIZE):
            batch = rsids[i : i + LOOKUP_BATCH_SIZE]
            placeholders = ", ".join(f":r{j}" for j in range(len(batch)))
            params = {f"r{j}": rsid for j, rsid in enumerate(batch)}

            stmt = sa.text(
                "SELECT rsid, chrom, pos, ref, alt, cadd_phred, sift_score, "  # noqa: S608
                "sift_pred, polyphen2_hsvar_score, polyphen2_hsvar_pred, "
                "revel, mutpred2, vest4, metasvm, metalr, gerp_rs, phylop, "
                f"mpc, primateai FROM dbnsfp_scores WHERE rsid IN ({placeholders})"
            )
            rows = conn.execute(stmt, params).fetchall()

            for row in rows:
                rows_by_rsid.setdefault(row.rsid, []).append(row)

    return {
        rsid: _dbnsfp_row_to_annotation(_pick_dbnsfp_row(rows, genotype_by_rsid.get(rsid)))
        for rsid, rows in rows_by_rsid.items()
    }


def lookup_dbnsfp_by_positions(
    positions: list[tuple[str, int, str, str]],
    dbnsfp_engine: sa.Engine,
    *,
    source_build: str | None = None,
) -> dict[tuple[str, int, str, str], DbNSFPAnnotation]:
    """Look up dbNSFP annotations by (chrom, pos, ref, alt).

    Fallback strategy when rsid matching fails. Uses the composite
    primary key for efficient lookups.

    **Cross-build guard (F35).** ``dbnsfp.db`` is GRCh38-coordinate while the
    annotation pipeline operates in GRCh37, so this position join is only valid
    for GRCh38 inputs. ``source_build`` declares the assembly of *positions*
    (default: the GRCh37 pipeline build). When it does not match dbNSFP's
    GRCh38 build the join would silently mis-key against the wrong coordinates,
    so the call is skipped with a structured warning and an empty result — the
    live path matches by rsid (:func:`lookup_dbnsfp_by_rsids`), which is
    build-agnostic. A caller with genuine GRCh38 coordinates can opt in by
    passing ``source_build="GRCh38"``.

    Args:
        positions: List of (chrom, pos, ref, alt) tuples.
        dbnsfp_engine: SQLAlchemy engine for dbnsfp.db.
        source_build: Genome build of *positions*; defaults to the pipeline
            build (GRCh37).

    Returns:
        Dict mapping (chrom, pos, ref, alt) → DbNSFPAnnotation. Empty when
        *positions* is empty or *source_build* is not dbNSFP's build.
    """
    if not positions:
        return {}

    from backend.db.database_registry import EXPECTED_GENOME_BUILD, PIPELINE_GENOME_BUILD

    dbnsfp_build = EXPECTED_GENOME_BUILD["dbnsfp"]
    if source_build is None:
        source_build = PIPELINE_GENOME_BUILD
    if source_build != dbnsfp_build:
        logger.warning(
            "dbnsfp_position_lookup_skipped_cross_build",
            source_build=source_build,
            dbnsfp_build=dbnsfp_build,
            positions=len(positions),
        )
        return {}

    results: dict[tuple[str, int, str, str], DbNSFPAnnotation] = {}

    with dbnsfp_engine.connect() as conn:
        for i in range(0, len(positions), POSITION_LOOKUP_BATCH_SIZE):
            batch = positions[i : i + POSITION_LOOKUP_BATCH_SIZE]

            conditions = []
            params: dict[str, str | int] = {}
            for j, (chrom, pos, ref, alt) in enumerate(batch):
                conditions.append(
                    f"(chrom = :c{j} AND pos = :p{j} AND ref = :r{j} AND alt = :a{j})"
                )
                params[f"c{j}"] = chrom
                params[f"p{j}"] = pos
                params[f"r{j}"] = ref
                params[f"a{j}"] = alt

            where_clause = " OR ".join(conditions)
            stmt = sa.text(
                "SELECT rsid, chrom, pos, ref, alt, cadd_phred, sift_score, "  # noqa: S608
                "sift_pred, polyphen2_hsvar_score, polyphen2_hsvar_pred, "
                "revel, mutpred2, vest4, metasvm, metalr, gerp_rs, phylop, "
                f"mpc, primateai FROM dbnsfp_scores WHERE {where_clause}"
            )
            rows = conn.execute(stmt, params).fetchall()

            for row in rows:
                key = (row.chrom, row.pos, row.ref, row.alt)
                results[key] = DbNSFPAnnotation(
                    rsid=row.rsid,
                    chrom=row.chrom,
                    pos=row.pos,
                    ref=row.ref,
                    alt=row.alt,
                    cadd_phred=row.cadd_phred,
                    sift_score=row.sift_score,
                    sift_pred=row.sift_pred,
                    polyphen2_hsvar_score=row.polyphen2_hsvar_score,
                    polyphen2_hsvar_pred=row.polyphen2_hsvar_pred,
                    revel=row.revel,
                    mutpred2=row.mutpred2,
                    vest4=row.vest4,
                    metasvm=row.metasvm,
                    metalr=row.metalr,
                    gerp_rs=row.gerp_rs,
                    phylop=row.phylop,
                    mpc=row.mpc,
                    primateai=row.primateai,
                )

    return results
