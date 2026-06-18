"""GTEx eQTL regulatory-association ingestion (SW-F3 / roadmap #39).

Ingests GTEx v8 **open-access** significant cis-eQTL variant-gene pairs into a
standalone ``gtex_eqtl.db`` so the app can show, for a sample's *typed non-coding*
variants, which gene-expression associations GTEx reports — as **regulatory
context only**, never as a causal-mechanism claim or ACMG evidence.

GTEx's eQTL files are GRCh38: the ``variant_id`` is ``chrN_pos_ref_alt_b38``. To
join against the app's GRCh37 / rsID-keyed sample data **without a coordinate
liftover**, we map each ``variant_id`` to its dbSNP rsID via GTEx's WGS lookup
table (``rs_id_dbSNP151_GRCh38p7`` column) and store rsID-keyed rows; pairs whose
variant has no rsID are skipped (they cannot be matched to array data anyway).

Guardrails:

* **Association ≠ mechanism.** An eQTL is a statistical association with
  expression; the causal variant is often a correlated LD neighbor. This layer is
  context-only and is deliberately *not* fed into ACMG (no PP3/PS3 uplift) — see
  :data:`backend.disclaimers.GTEX_EQTL_CONTEXT_ONLY`.
* **No silent wipe.** A clear-then-load that parses zero rows raises (mirrors the
  ClinGen/CPIC/PGS empty-parse guards).

GTEx open-access summary statistics (eQTL results) are redistributable (the
protected data is the individual-level WGS, not these summaries); cite the GTEx
Consortium (2020, PMID 32913098).
"""

from __future__ import annotations

import gzip
import re
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING

import sqlalchemy as sa
import structlog

if TYPE_CHECKING:
    from collections.abc import Callable

logger = structlog.get_logger(__name__)

# Pipeline-build sources (mirrored in bundles/manifest.json -> pipeline_pins.gtex_eqtl).
# Both files are GTEx v8 open-access summary statistics (the protected data is the
# individual-level WGS, not these). Heavy (~1.6 GB tar + ~0.8 GB lookup) → build on
# the cluster (see docs/external-inputs-strategy.md / CLAUDE.md SLURM rule).
GTEX_EQTL_URL = (
    "https://storage.googleapis.com/adult-gtex/bulk-qtl/v8/"
    "single-tissue-cis-qtl/GTEx_Analysis_v8_eQTL.tar"
)
# The WGS variant lookup table maps the GRCh38 ``variant_id`` to its dbSNP rsID
# (``rs_id_dbSNP151_GRCh38p7``); this is how we join GTEx (GRCh38) to the app's
# GRCh37 / rsID-keyed sample data WITHOUT a coordinate liftover — see module header.
GTEX_LOOKUP_URL = (
    "https://storage.googleapis.com/adult-gtex/references/v8/reference-tables/"
    "GTEx_Analysis_2017-06-05_v8_WholeGenomeSeq_838Indiv_Analysis_Freeze."
    "lookup_table.txt.gz"
)
GTEX_VERSION = "v8"

# GTEx significant cis-eQTL per-tissue files inside the eQTL tar are named
# ``<Tissue>.v8.signif_variant_gene_pairs.txt.gz`` (e.g. ``Whole_Blood.v8.…``).
_SIGNIF_PAIRS_RE = re.compile(r"\.v8\.signif_variant_gene_pairs\.txt(\.gz)?$")

# variant_id format: chr1_64764_C_T_b38  (GRCh38, 'chr'-prefixed).
_VARIANT_ID_RE = re.compile(r"^chr([0-9XYM]+)_(\d+)_([ACGT]+)_([ACGT]+)_b38$", re.IGNORECASE)

_metadata = sa.MetaData()

gtex_eqtl = sa.Table(
    "gtex_eqtl",
    _metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("rsid", sa.Text, nullable=False),
    sa.Column("gene_id", sa.Text, nullable=False),  # Ensembl gene ID (ENSG...)
    sa.Column("tissue", sa.Text, nullable=False),
    sa.Column("chrom", sa.Text),  # GRCh38 chrom (no 'chr')
    sa.Column("pos", sa.Integer),  # GRCh38 pos
    sa.Column("pval_nominal", sa.Float),
    sa.Column("slope", sa.Float),  # effect size; sign = direction on expression
)

sa.Index("idx_gtex_eqtl_rsid", gtex_eqtl.c.rsid)
sa.Index("idx_gtex_eqtl_gene", gtex_eqtl.c.gene_id)


@dataclass
class GtexLoadStats:
    """Outcome of ingesting one tissue's significant eQTL pairs."""

    tissue: str
    loaded: int
    skipped_no_rsid: int
    skipped_bad_row: int


def create_gtex_tables(engine: sa.Engine) -> None:
    """Create the GTEx eQTL table if absent (idempotent)."""
    _metadata.create_all(engine)


def _float_at(parts: list[str], i: int | None) -> float | None:
    """Parse ``parts[i]`` as float, or None if missing/blank/non-numeric."""
    if i is None or i >= len(parts):
        return None
    try:
        return float(parts[i])
    except ValueError:
        return None


def _open_maybe_gzip(path: Path) -> IO[str]:
    p = Path(path)
    if p.suffix == ".gz":
        return gzip.open(p, "rt", encoding="utf-8")
    return p.open("r", encoding="utf-8")


def parse_variant_id(variant_id: str) -> tuple[str, int] | None:
    """Parse a GTEx ``chrN_pos_ref_alt_b38`` id → (chrom-without-chr, pos), or None."""
    m = _VARIANT_ID_RE.match(variant_id.strip())
    if not m:
        return None
    return (m.group(1).upper(), int(m.group(2)))


def load_variant_rsid_lookup(path: Path) -> dict[str, str]:
    """Load GTEx's WGS lookup table → {variant_id: rsID}.

    Reads the ``variant_id`` and ``rs_id_dbSNP151_GRCh38p7`` columns (name-keyed).
    Entries with no rsID (``.`` / empty) are omitted.
    """
    out: dict[str, str] = {}
    with _open_maybe_gzip(path) as fh:
        header = fh.readline().rstrip("\n").split("\t")
        idx = {name: i for i, name in enumerate(header)}
        i_vid = idx.get("variant_id")
        i_rs = next(
            (
                idx[c]
                for c in ("rs_id_dbSNP151_GRCh38p7", "rs_id_dbSNP150_GRCh38p7", "rs_id")
                if c in idx
            ),
            None,
        )
        if i_vid is None or i_rs is None:
            raise ValueError(f"lookup table missing variant_id/rs_id columns (have {header})")
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) <= max(i_vid, i_rs):
                continue
            rs = parts[i_rs].strip()
            if rs and rs not in (".", "NA"):
                out[parts[i_vid].strip()] = rs
    return out


def load_gtex_eqtl(
    signif_pairs_path: Path,
    rsid_lookup: dict[str, str],
    tissue: str,
    engine: sa.Engine,
    *,
    clear_existing: bool = True,
) -> GtexLoadStats:
    """Ingest one tissue's ``*.signif_variant_gene_pairs.txt(.gz)`` into ``gtex_eqtl.db``.

    Args:
        signif_pairs_path: GTEx significant variant-gene pairs file.
        rsid_lookup: ``{variant_id: rsID}`` from :func:`load_variant_rsid_lookup`.
        tissue: Tissue label (e.g. ``"Whole_Blood"``), normally the filename stem.
        engine: Target standalone-DB engine.
        clear_existing: Delete prior rows for this tissue first.

    Raises:
        ValueError: required columns missing, or zero rows parsed (no silent wipe).
    """
    create_gtex_tables(engine)

    with _open_maybe_gzip(signif_pairs_path) as fh:
        header = fh.readline().rstrip("\n").split("\t")
        idx = {name: i for i, name in enumerate(header)}
        i_vid = idx.get("variant_id")
        i_gene = idx.get("gene_id")
        if i_vid is None or i_gene is None:
            raise ValueError(f"signif_pairs missing variant_id/gene_id (have {header})")
        i_p = idx.get("pval_nominal")
        i_slope = idx.get("slope")

        rows: list[dict] = []
        skipped_no_rsid = 0
        skipped_bad = 0
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) <= max(i_vid, i_gene):
                skipped_bad += 1
                continue
            variant_id = parts[i_vid].strip()
            rsid = rsid_lookup.get(variant_id)
            if not rsid:
                skipped_no_rsid += 1
                continue
            coords = parse_variant_id(variant_id)
            chrom, pos = coords if coords else (None, None)
            rows.append(
                {
                    "rsid": rsid,
                    "gene_id": parts[i_gene].strip(),
                    "tissue": tissue,
                    "chrom": chrom,
                    "pos": pos,
                    "pval_nominal": _float_at(parts, i_p),
                    "slope": _float_at(parts, i_slope),
                }
            )

    if not rows:
        raise ValueError(
            f"{tissue}: parsed zero eQTL rows with an rsID — refusing to clear/replace "
            f"with empty data."
        )

    with engine.begin() as conn:
        if clear_existing:
            conn.execute(gtex_eqtl.delete().where(gtex_eqtl.c.tissue == tissue))
        for i in range(0, len(rows), 1000):
            conn.execute(gtex_eqtl.insert(), rows[i : i + 1000])

    return GtexLoadStats(
        tissue=tissue,
        loaded=len(rows),
        skipped_no_rsid=skipped_no_rsid,
        skipped_bad_row=skipped_bad,
    )


def lookup_eqtls_by_rsids(rsids: list[str], engine: sa.Engine) -> dict[str, list[dict]]:
    """Return ``{rsid: [eQTL rows]}`` for the given rsids (batched IN query)."""
    if not rsids:
        return {}
    out: dict[str, list[dict]] = {}
    with engine.connect() as conn:
        for i in range(0, len(rsids), 500):
            batch = rsids[i : i + 500]
            stmt = sa.select(gtex_eqtl).where(gtex_eqtl.c.rsid.in_(batch))
            for r in conn.execute(stmt):
                out.setdefault(r.rsid, []).append(dict(r._mapping))
    return out


def record_gtex_eqtl_version(
    engine: sa.Engine,
    *,
    version: str = GTEX_VERSION,
    file_path: str | None = None,
    file_size_bytes: int | None = None,
    checksum: str | None = None,
) -> None:
    """Record the GTEx eQTL version in ``database_versions`` (GRCh37-joinable).

    The rows are rsID-keyed and joined to the app's GRCh37 sample data by rsID, so
    the recorded build is GRCh37 even though GTEx's native coordinates are GRCh38
    (mirrors how dbNSFP is rsID-joined; see ``EXPECTED_GENOME_BUILD``).
    """
    from backend.db.database_registry import _record_db_version

    _record_db_version(
        engine,
        db_name="gtex_eqtl",
        version=version,
        file_size_bytes=file_size_bytes,
        sha256=checksum,
        file_path=file_path,
        genome_build="GRCh37",
    )


def _extract_eqtl_tar(tar_path: Path, dest_dir: Path) -> Path:
    """Extract the GTEx eQTL tar into ``dest_dir`` (path-traversal/symlink safe).

    Mirrors the LAI bundle extractor's safety guards (``database_registry.
    _extract_lai_bundle``): reject absolute / ``..`` / link members, extract with
    ``filter="data"``. Returns the directory that holds the per-tissue
    ``*.signif_variant_gene_pairs.txt.gz`` files.

    The destination is cleared first so a stale extraction (e.g. tissue files
    from a previous, larger tar) can never be mixed into the new build.
    """
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r") as tf:
        for member in tf.getmembers():
            if member.name.startswith("/") or ".." in member.name.split("/"):
                logger.warning("gtex_eqtl_skip_unsafe_entry", name=member.name)
                continue
            if member.issym() or member.islnk():
                logger.warning("gtex_eqtl_skip_link", name=member.name)
                continue
            tf.extract(member, dest_dir, filter="data")
    return dest_dir


def _tissue_from_filename(path: Path) -> str:
    """``Whole_Blood.v8.signif_variant_gene_pairs.txt.gz`` → ``Whole_Blood``."""
    return _SIGNIF_PAIRS_RE.sub("", path.name)


def load_gtex_eqtl_dir(
    eqtl_dir: Path,
    lookup_path: Path,
    engine: sa.Engine,
    *,
    parse_progress: Callable[[int], None] | None = None,
) -> list[GtexLoadStats]:
    """Ingest every per-tissue signif-pairs file under ``eqtl_dir`` into ``gtex_eqtl.db``.

    Loads the WGS variant→rsID lookup once, then ingests each tissue (per-tissue
    ``clear_existing`` so a re-build replaces a tissue in place). Raises if the tar
    contains no signif-pairs files (no silent empty build — mirrors the per-tissue
    empty-parse guard in :func:`load_gtex_eqtl`).
    """
    signif_files = sorted(
        p for p in eqtl_dir.rglob("*") if p.is_file() and _SIGNIF_PAIRS_RE.search(p.name)
    )
    if not signif_files:
        raise ValueError(
            f"no GTEx *.signif_variant_gene_pairs.txt(.gz) files found under {eqtl_dir} — "
            f"refusing to build an empty gtex_eqtl.db."
        )

    rsid_lookup = load_variant_rsid_lookup(lookup_path)

    create_gtex_tables(engine)
    stats: list[GtexLoadStats] = []
    total_loaded = 0
    for f in signif_files:
        tissue = _tissue_from_filename(f)
        try:
            s = load_gtex_eqtl(f, rsid_lookup, tissue, engine, clear_existing=True)
        except ValueError as exc:
            # Only the explicit zero-rsID-match case is benign (a tissue with no
            # joinable rows); a malformed/short-schema file must still abort the
            # build rather than silently ship an incomplete DB.
            if "parsed zero eQTL rows with an rsID" not in str(exc):
                raise
            logger.warning("gtex_eqtl_tissue_empty", tissue=tissue, error=str(exc))
            continue
        stats.append(s)
        total_loaded += s.loaded
        if parse_progress is not None:
            parse_progress(total_loaded)

    if not stats:
        raise ValueError(
            "every GTEx tissue parsed zero rsID-matched eQTL rows — refusing to build "
            "an empty gtex_eqtl.db (is the WGS lookup table the right one?)."
        )
    return stats


def _download_gtex_inputs(
    dest_dir: Path,
    *,
    download_progress: Callable[[int, int | None], None] | None = None,
    timeout: float = 7200.0,
) -> tuple[Path, Path]:
    """Download the GTEx eQTL tar + the WGS rsID lookup table → ``(tar_path, lookup_path)``.

    Both are streamed (resumable) via the shared ``stream_download`` helper. Heavy —
    run on the cluster. Isolated behind one function so tests can monkeypatch it.
    """
    from backend.annotation.http_download import (
        clear_validator_sidecar,
        read_validator_sidecar,
        stream_download,
        write_validator_sidecar,
    )

    dest_dir.mkdir(parents=True, exist_ok=True)

    def _fetch(url: str, name: str, band_lo: float, band_hi: float) -> Path:
        """Download one file, reporting progress into the ``[band_lo, band_hi]`` %.

        The two downloads share one callback; mapping each into a distinct
        percentage band keeps overall progress monotonic instead of resetting to
        0% when the second (lookup) download starts. Reported as ``(pct, 100)``.
        """
        dest_path = dest_dir / name
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
        logger.info("gtex_eqtl_download_start", url=url, dest=str(dest_path))

        def _banded(done: int, total: int | None) -> None:
            if download_progress is None:
                return
            frac = (done / total) if total else 0.0
            download_progress(int(band_lo + frac * (band_hi - band_lo)), 100)

        stream_download(
            url,
            tmp_path,
            progress_callback=_banded,
            timeout=timeout,
            resumable=True,
            validator=read_validator_sidecar(tmp_path),
            on_validator=lambda v: write_validator_sidecar(tmp_path, v),
        )
        tmp_path.rename(dest_path)
        clear_validator_sidecar(tmp_path)
        return dest_path

    # Bands sized to the inputs' relative bytes (~1.6 GB tar, ~0.8 GB lookup).
    tar_path = _fetch(GTEX_EQTL_URL, "GTEx_Analysis_v8_eQTL.tar", 0.0, 65.0)
    lookup_path = _fetch(GTEX_LOOKUP_URL, "GTEx_v8_WGS_lookup_table.txt.gz", 65.0, 100.0)
    return tar_path, lookup_path


def download_and_load_gtex_eqtl(
    engine: sa.Engine,
    dest_dir: Path,
    *,
    download_progress: Callable[[int, int | None], None] | None = None,
    parse_progress: Callable[[int], None] | None = None,
    timeout: float = 7200.0,
    reference_engine: sa.Engine | None = None,
) -> list[GtexLoadStats]:
    """Build-mode entry point: fetch GTEx v8 eQTL + lookup and load ``gtex_eqtl.db``.

    Pipeline build (like AlphaMissense): downloads the open-access eQTL tar (~1.6 GB)
    and the WGS variant→rsID lookup table (~0.8 GB), extracts the tar, and ingests
    each tissue rsID-keyed (GRCh38→rsID match, no liftover — see module header).
    Heavy → run on the cluster. The version MUST be recorded into the reference DB
    (``reference_engine``) so the Update Manager surfaces a row — writing it into
    the standalone ``gtex_eqtl.db`` would be invisible to the registry contract, so
    ``reference_engine`` is required.
    """
    if reference_engine is None:
        raise ValueError(
            "download_and_load_gtex_eqtl requires reference_engine so the version is "
            "recorded in reference.db (visible to the Update Manager), not the "
            "standalone gtex_eqtl.db."
        )

    tar_path, lookup_path = _download_gtex_inputs(
        dest_dir, download_progress=download_progress, timeout=timeout
    )

    eqtl_dir = _extract_eqtl_tar(tar_path, dest_dir / "gtex_v8_eqtl")
    stats = load_gtex_eqtl_dir(eqtl_dir, lookup_path, engine, parse_progress=parse_progress)

    record_gtex_eqtl_version(
        reference_engine,
        file_path=str(tar_path),
        file_size_bytes=tar_path.stat().st_size if tar_path.exists() else None,
    )
    if download_progress is not None:
        download_progress(100, 100)
    logger.info(
        "gtex_eqtl_build_complete",
        tissues=len(stats),
        rows=sum(s.loaded for s in stats),
    )
    return stats
