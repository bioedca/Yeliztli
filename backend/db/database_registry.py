"""Registry of reference databases available for download.

Defines metadata for each database that Yeliztli uses: name, description,
approximate size, download URL, expected SHA-256, and whether it is required
or optional for core functionality.

The setup wizard API (P1-18) uses this registry to list databases and
orchestrate parallel downloads.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from backend.config import Settings

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy import Engine

logger = structlog.get_logger(__name__)

# Directory containing databases shipped with the repo
BUNDLED_DIR = Path(__file__).resolve().parent.parent.parent / "bundles"


@dataclass(frozen=True)
class DatabaseInfo:
    """Metadata for a downloadable reference database."""

    name: str
    display_name: str
    description: str
    url: str
    filename: str
    expected_size_bytes: int
    sha256: str | None = None
    required: bool = True
    phase: int = 1
    post_download: Callable[[Path, Path], None] | None = None
    build_mode: str = "pipeline"  # "pipeline" | "download" | "manual" | "bundled"
    target_db: str = "standalone"  # "standalone" | "reference"

    def dest_path(self, settings: Settings) -> Path:
        """Resolve the destination file path for this database."""
        return settings.data_dir / self.filename


# ── Post-download transforms ─────────────────────────────────────────


def _extract_lai_bundle(tarball_path: Path, dest_path: Path) -> None:
    """Extract the LAI bundle tarball into a sibling directory.

    Called by the download pipeline as a ``post_download`` hook.  *dest_path*
    is the nominal ``data_dir / "lai_bundle.tar.gz"`` — the bundle is extracted
    into ``data_dir / "lai_bundle/"`` alongside it.
    """
    import tarfile

    dest_dir = dest_path.parent / "lai_bundle"
    dest_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(tarball_path, "r:gz") as tf:
        # Safety: reject entries with path traversal or symlinks
        for member in tf.getmembers():
            if member.name.startswith("/") or ".." in member.name.split("/"):
                logger.warning("lai_bundle_skip_unsafe_entry", name=member.name)
                continue
            if member.issym() or member.islnk():
                logger.warning("lai_bundle_skip_link", name=member.name)
                continue
            tf.extract(member, dest_dir, filter="data")

    # Validate: all 22 chromosome model directories must exist
    missing = []
    for chrom in range(1, 23):
        model_dir = dest_dir / "gnomix_models" / f"chr{chrom}"
        for expected_file in ("base_coefs.npz", "metadata.npz", "smoother.json"):
            if not (model_dir / expected_file).exists():
                missing.append(f"gnomix_models/chr{chrom}/{expected_file}")

    if missing:
        logger.error("lai_bundle_incomplete", missing_files=missing[:5])
        raise ValueError(
            f"LAI bundle extraction incomplete — missing {len(missing)} file(s): "
            + ", ".join(missing[:5])
        )

    # Remove tarball after successful extraction
    tarball_path.unlink(missing_ok=True)

    _record_lai_bundle_version(dest_path.parent, dest_dir)

    logger.info("lai_bundle_extracted", dest=str(dest_dir))


def _record_lai_bundle_version(data_dir: Path, bundle_dir: Path) -> None:
    """Write a ``database_versions`` row for the freshly extracted LAI bundle.

    Pulls ``version``/``sha256`` from the bundle manifest when reachable,
    otherwise records ``version="unknown-pre-manifest"`` so the Update Manager
    still surfaces the bundle. Best-effort — failure to reach the reference DB
    is logged but does not abort extraction.
    """
    import sqlalchemy as sa

    from backend.db.manifest import get_bundle_info

    entry = get_bundle_info("lai_bundle")
    if entry is not None:
        version = entry.version
        sha256: str | None = entry.sha256
    else:
        version = "unknown-pre-manifest"
        sha256 = None

    dest_dir_size = sum(p.stat().st_size for p in bundle_dir.rglob("*") if p.is_file())
    reference_db_path = data_dir / "reference.db"

    try:
        engine = sa.create_engine(f"sqlite:///{reference_db_path}")
        try:
            _record_db_version(
                engine,
                db_name="lai_bundle",
                version=version,
                file_size_bytes=dest_dir_size,
                sha256=sha256,
            )
        finally:
            engine.dispose()
    except Exception as exc:
        logger.warning(
            "lai_bundle_version_record_failed",
            error=str(exc),
            reference_db=str(reference_db_path),
        )


def detect_java() -> bool:
    """Check whether a Java runtime (8+) is available on PATH.

    Runs ``java -version`` and parses the output to verify the major
    version is at least 8.  Returns False if Java is missing, the
    command fails, or the version cannot be parsed.
    """
    import re
    import subprocess

    if shutil.which("java") is None:
        return False
    try:
        result = subprocess.run(
            ["java", "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False
        # java -version prints to stderr
        output = result.stderr + result.stdout
        # Match patterns like: "1.8.0_292", "11.0.11", "17", "21.0.1"
        match = re.search(r'"(\d+)(?:\.(\d+))?', output)
        if not match:
            return False
        major = int(match.group(1))
        # Java 8 reports as "1.8"; Java 9+ reports as "9", "11", etc.
        if major == 1:
            minor = int(match.group(2)) if match.group(2) else 0
            return minor >= 8
        return major >= 8
    except (OSError, subprocess.TimeoutExpired):
        return False


def validate_lai_bundle(bundle_dir: Path) -> bool:
    """Check that an extracted LAI bundle has the expected structure."""
    if not bundle_dir.is_dir():
        return False
    for chrom in range(1, 23):
        model_dir = bundle_dir / "gnomix_models" / f"chr{chrom}"
        for fname in ("base_coefs.npz", "metadata.npz", "smoother.json"):
            if not (model_dir / fname).exists():
                return False
    return True


def _build_encode_ccres_db(raw_bed_path: Path, db_path: Path) -> None:
    """Transform a downloaded ENCODE cCREs BED file into a SQLite database.

    Called by the download pipeline as a ``post_download`` hook. Creates a
    SQLite database at *db_path* from the raw BED at *raw_bed_path*, then
    removes the raw BED file.
    """
    import sqlalchemy as sa

    from backend.annotation.encode_ccres import load_encode_ccres

    engine = sa.create_engine(f"sqlite:///{db_path}", echo=False)
    try:
        load_encode_ccres(raw_bed_path, engine)
    except Exception:
        engine.dispose()
        db_path.unlink(missing_ok=True)
        raise
    engine.dispose()
    # Clean up the raw BED — the SQLite DB is the final artifact
    raw_bed_path.unlink(missing_ok=True)

    _record_encode_ccres_version(db_path)


def _record_encode_ccres_version(db_path: Path) -> None:
    """Write a ``database_versions`` row for the freshly built ENCODE cCREs DB.

    Uses ``now_yyyymmdd`` (UTC) as the version since the upstream BED has no
    embedded version. Best-effort — failure to reach the reference DB is
    logged but does not abort the build.
    """
    from datetime import UTC, datetime

    import sqlalchemy as sa

    if not db_path.exists():
        logger.warning("encode_ccres_version_record_skipped_missing_db", path=str(db_path))
        return

    version = datetime.now(UTC).strftime("%Y%m%d")
    file_size = db_path.stat().st_size
    reference_db_path = db_path.parent / "reference.db"

    try:
        engine = sa.create_engine(f"sqlite:///{reference_db_path}")
        try:
            _record_db_version(
                engine,
                db_name="encode_ccres",
                version=version,
                file_size_bytes=file_size,
                sha256=None,
            )
        finally:
            engine.dispose()
    except Exception as exc:
        logger.warning(
            "encode_ccres_version_record_failed",
            error=str(exc),
            reference_db=str(reference_db_path),
        )


# ── Database Definitions ──────────────────────────────────────────────
# URLs point to GitHub Releases (placeholder URLs until actual releases
# are published).  SHA-256 values are None until bundles are built.

DATABASES: dict[str, DatabaseInfo] = {
    "clinvar": DatabaseInfo(
        name="clinvar",
        display_name="ClinVar",
        description="Clinical variant interpretations from NCBI ClinVar",
        url="",
        filename="clinvar.db",
        expected_size_bytes=250_000_000,  # ~250 MB
        required=True,
        phase=1,
        build_mode="pipeline",
        target_db="reference",
    ),
    "vep_bundle": DatabaseInfo(
        name="vep_bundle",
        display_name="VEP Bundle",
        description=(
            "Pre-computed variant effect predictions for the 23andMe v5 "
            "∪ AncestryDNA v2.0 rsid catalog"
        ),
        url="https://github.com/bioedca/Yeliztli/releases/download/bundle-v2.0.0/vep_bundle.db",
        filename="vep_bundle.db",
        expected_size_bytes=600_000_000,  # ~600 MB (union catalog; v2.0.0+)
        required=False,
        phase=2,
        build_mode="bundled",
        target_db="standalone",
    ),
    "gnomad": DatabaseInfo(
        name="gnomad",
        display_name="gnomAD",
        description="Population allele frequencies from the Genome Aggregation Database",
        # In bundled mode the runner reads the authoritative url/sha/size from the
        # manifest (bundles["gnomad"]); this URL is documentation/fallback and points
        # at the published gnomad-bundle-v1.0.0 release asset.
        url="https://github.com/bioedca/Yeliztli/releases/download/gnomad-bundle-v1.0.0/gnomad_af.db",
        filename="gnomad_af.db",
        # Exact size of the published gnomad_af.db asset (byte-matches
        # bundles/manifest.json -> bundles.gnomad.size_bytes; gnomAD r2.1.1 exomes).
        expected_size_bytes=1_952_698_368,
        # sha256 unpinned in bundled mode — the manifest bundles["gnomad"] entry is authoritative.
        sha256=None,
        required=True,
        phase=2,
        build_mode="bundled",
        target_db="standalone",
    ),
    "dbnsfp": DatabaseInfo(
        name="dbnsfp",
        display_name="dbNSFP",
        description=(
            "In-silico pathogenicity prediction scores (SIFT, PolyPhen-2, CADD, REVEL, etc.)"
        ),
        url="",
        filename="dbnsfp.db",
        expected_size_bytes=1_500_000_000,  # ~1.5 GB
        required=True,
        phase=2,
        build_mode="pipeline",
        target_db="standalone",
    ),
    "alphamissense": DatabaseInfo(
        name="alphamissense",
        display_name="AlphaMissense",
        description=(
            "Proteome-wide missense pathogenicity predictions (hg19, CC-BY-4.0); "
            "context-only complement to REVEL"
        ),
        url="",
        filename="alphamissense.db",
        expected_size_bytes=3_500_000_000,  # ~3.5 GB built SQLite (71M rows)
        required=False,
        phase=2,
        build_mode="pipeline",
        target_db="standalone",
    ),
    "pgs_scores": DatabaseInfo(
        name="pgs_scores",
        display_name="PGS Catalog scores",
        description=(
            "Bundle-eligible CC-BY PGS Catalog GRCh37-harmonized polygenic scores "
            "(T2D, BMI, LDL-C) for the Wave-B disease-PRS modules"
        ),
        # In bundled mode the runner reads the authoritative url/sha/size from the
        # manifest (bundles["pgs_scores"]); this URL is documentation/fallback and
        # points at the published pgs-scores-v1.0.0 release asset.
        url="https://github.com/bioedca/Yeliztli/releases/download/pgs-scores-v1.0.0/pgs_scores.db",
        filename="pgs_scores.db",
        # Byte-matches bundles/manifest.json -> bundles.pgs_scores.size_bytes.
        expected_size_bytes=103_874_560,
        # sha256 unpinned in bundled mode — the manifest entry is authoritative.
        sha256=None,
        required=False,
        phase=2,
        build_mode="bundled",
        target_db="standalone",
    ),
    "cpic": DatabaseInfo(
        name="cpic",
        display_name="CPIC",
        description="Pharmacogenomics allele definitions and drug guidelines",
        url="",
        filename="cpic.db",
        expected_size_bytes=5_000_000,  # ~5 MB
        required=True,
        phase=3,
        build_mode="pipeline",
        target_db="reference",
    ),
    "clingen": DatabaseInfo(
        name="clingen",
        display_name="ClinGen Gene-Disease Validity",
        description=("ClinGen gene-disease validity classifications (guardrail context; CC0)"),
        url="",
        filename="clingen.db",
        expected_size_bytes=1_200_000,  # ~1.1 MB committed CSV
        required=False,
        phase=3,
        build_mode="pipeline",
        target_db="reference",
    ),
    "ancestry_pca": DatabaseInfo(
        name="ancestry_pca",
        display_name="Ancestry PCA Bundle",
        description=(
            "Pre-computed PCA loadings and reference population coordinates"
            " (5,000 AIMs, 7 populations)"
        ),
        url="",
        filename="ancestry_pca_bundle.npz",
        expected_size_bytes=414_432,  # ~414 KB
        required=False,
        phase=3,
        build_mode="bundled",
        target_db="standalone",
    ),
    "lai_bundle": DatabaseInfo(
        name="lai_bundle",
        display_name="LAI Bundle (Chromosome Painting)",
        description=(
            "Local ancestry inference models for chromosome-level ancestry painting. "
            "Optional — requires ~1.6 GB and Java 8+."
        ),
        url="https://github.com/bioedca/Yeliztli/releases/download/lai-bundle-v2.0.0/genomeinsight_lai_bundle_v2.0.0.tar.gz",
        filename="lai_bundle.tar.gz",
        # Real v2.0.0 tarball size + SHA-256. The SHA MUST byte-match
        # bundles.lai_bundle.sha256 (Phase E1 smoke + nightly cache pin on
        # registry/manifest agreement — Plan §9 Done criterion #4).
        # Rebuilt 2026-06-04 to fix European misclassification (the original
        # 96f2fcac… bundle dropped 767/770 EUR from training), then re-balanced
        # 2026-06-05 (--per-region-cap=250) to fix residual Middle-Eastern
        # misclassification (held-out MID 2/5 → 5/5; see fix-lai-mid-rebalance PR).
        expected_size_bytes=1_723_731_810,  # ~1.6 GB (v2.0.0 union bundle)
        sha256="36abb5f2ed95011aff1227c894f52597ef5c31adb5a132fafdf0830eabf14bff",
        required=False,
        phase=3,
        build_mode="download",
        target_db="standalone",
        post_download=_extract_lai_bundle,
    ),
    "encode_ccres": DatabaseInfo(
        name="encode_ccres",
        display_name="ENCODE cCREs",
        description="Candidate cis-Regulatory Elements for IGV.js track visualization",
        url="https://downloads.wenglab.org/V3/GRCh38-cCREs.bed",
        filename="encode_ccres.db",
        expected_size_bytes=30_000_000,  # ~30 MB (SQLite after BED loading)
        required=False,
        phase=2,
        build_mode="download",
        target_db="standalone",
        post_download=_build_encode_ccres_db,
    ),
    "gwas_catalog": DatabaseInfo(
        name="gwas_catalog",
        display_name="GWAS Catalog",
        description="Genome-wide association study results from EBI GWAS Catalog",
        url="",
        filename="",
        expected_size_bytes=100_000_000,  # ~100 MB
        required=True,
        phase=2,
        build_mode="pipeline",
        target_db="reference",
    ),
    "dbsnp": DatabaseInfo(
        name="dbsnp",
        display_name="dbSNP",
        description="SNP merge history for rsid validation (NCBI dbSNP b151)",
        url="",
        filename="",
        expected_size_bytes=20_000_000,  # ~20 MB
        required=True,
        phase=2,
        build_mode="pipeline",
        target_db="reference",
    ),
    "mondo_hpo": DatabaseInfo(
        name="mondo_hpo",
        display_name="MONDO/HPO",
        description="Gene-disease-phenotype associations from Monarch Initiative and HPO",
        url="",
        filename="",
        expected_size_bytes=15_000_000,  # ~15 MB
        required=True,
        phase=2,
        build_mode="pipeline",
        target_db="reference",
    ),
}


def get_all_databases() -> list[DatabaseInfo]:
    """Return all registered databases."""
    return list(DATABASES.values())


def get_database(name: str) -> DatabaseInfo | None:
    """Look up a database by name, or None if not found."""
    return DATABASES.get(name)


# ── Build function registry (per-database lazy import) ───────────
# Each entry maps db_name -> (module_path, function_name).
# Only the requested module is imported, so a broken import in one
# builder does not break all others.

_BUILD_FN_REGISTRY: dict[str, tuple[str, str]] = {
    "clinvar": ("backend.annotation.clinvar", "download_and_load_clinvar"),
    # gnomad is no longer a pipeline build — it ships as a prebuilt downloadable
    # bundle (build_mode="bundled"). get_build_fn("gnomad") now returns None so the
    # setup wizard / scheduler route it through run_gnomad_bundle_update instead.
    "dbnsfp": ("backend.annotation.dbnsfp", "download_and_load_dbnsfp"),
    "alphamissense": ("backend.annotation.alphamissense", "download_and_load_alphamissense"),
    "gwas_catalog": ("backend.annotation.gwas", "download_and_load_gwas"),
    "dbsnp": ("backend.annotation.dbsnp", "download_and_load_rsmerge"),
    "mondo_hpo": ("backend.annotation.mondo_hpo", "download_and_load_mondo_hpo"),
    "cpic": ("backend.annotation.cpic", "download_and_load_cpic"),
    "clingen": ("backend.annotation.clingen", "download_and_load_clingen"),
}

# Cache resolved callables so each module is imported at most once.
_build_fn_cache: dict[str, Callable] = {}


def get_build_fn(db_name: str) -> Callable | None:
    """Return the build function for a pipeline database, or None.

    Imports only the requested module on first call, caching the result.
    """
    if db_name in _build_fn_cache:
        return _build_fn_cache[db_name]

    entry = _BUILD_FN_REGISTRY.get(db_name)
    if entry is None:
        return None

    from importlib import import_module

    module_path, fn_name = entry
    mod = import_module(module_path)
    fn = getattr(mod, fn_name)
    _build_fn_cache[db_name] = fn
    return fn


# ── Genome-build provenance (F30) ─────────────────────────────────
# The annotation pipeline operates in GRCh37: the chip rsid catalog, ClinVar,
# gnomAD r2.1.1, the GWAS catalog, CPIC, the VEP bundle and the gnomAD gene
# constraint table are all GRCh37-coordinate. dbNSFP is the lone exception — it
# ships GRCh38 coordinates (F35) and is joined by rsid on the live path, so its
# cross-build coordinates are *expected*, not a defect.
PIPELINE_GENOME_BUILD = "GRCh37"

# Expected genome build per recorded source, keyed by the ``db_name`` written to
# ``database_versions``. Sources absent here are build-agnostic / gene-keyed
# (dbsnp merge history, mondo_hpo, omim, lai_bundle, ancestry_pca, encode_ccres)
# and record no build. This map is the single source of truth shared by the
# recorder (auto-stamp) and :func:`check_genome_build_consistency`.
EXPECTED_GENOME_BUILD: dict[str, str] = {
    "clinvar": "GRCh37",
    "gnomad": "GRCh37",
    "gwas_catalog": "GRCh37",
    "cpic": "GRCh37",
    "vep_bundle": "GRCh37",
    "gnomad_constraint": "GRCh37",
    "alphamissense": "GRCh37",
    "pgs_scores": "GRCh37",
    "dbnsfp": "GRCh38",
}


# ── Version recording ────────────────────────────────────────────


def _record_db_version(
    engine: Engine,
    db_name: str,
    version: str,
    file_size_bytes: int | None,
    sha256: str | None = None,
    file_path: str | None = None,
    genome_build: str | None = None,
) -> None:
    """Upsert a single row in ``database_versions``.

    Single-source helper used by every download/build/extract path so the
    Update Manager always sees a row regardless of which DB type completed
    (per setup-update-plan §3.7).

    ``genome_build`` records the source's coordinate assembly (F30). When the
    caller leaves it ``None`` it is auto-resolved from
    :data:`EXPECTED_GENOME_BUILD` by ``db_name``, so every recorder stamps the
    correct build without each call site repeating it; a build-agnostic source
    (not in the map) records ``NULL``. An explicit non-``None`` value overrides
    the map — used by tests to plant a skew.
    """
    from datetime import UTC, datetime

    import sqlalchemy as sa

    from backend.db.tables import database_versions

    if genome_build is None:
        genome_build = EXPECTED_GENOME_BUILD.get(db_name)

    with engine.begin() as conn:
        existing = conn.execute(
            sa.select(database_versions.c.db_name).where(database_versions.c.db_name == db_name)
        ).fetchone()

        now = datetime.now(UTC)
        if existing:
            conn.execute(
                database_versions.update()
                .where(database_versions.c.db_name == db_name)
                .values(
                    version=version,
                    file_path=file_path,
                    file_size_bytes=file_size_bytes,
                    downloaded_at=now,
                    checksum_sha256=sha256,
                    genome_build=genome_build,
                )
            )
        else:
            conn.execute(
                database_versions.insert().values(
                    db_name=db_name,
                    version=version,
                    file_path=file_path,
                    file_size_bytes=file_size_bytes,
                    downloaded_at=now,
                    checksum_sha256=sha256,
                    genome_build=genome_build,
                )
            )


def check_genome_build_consistency(reference_engine: Engine) -> list[str]:
    """Return ``db_name``s whose recorded ``genome_build`` is an unexpected skew.

    Compares each ``database_versions`` row's recorded build against
    :data:`EXPECTED_GENOME_BUILD`. A source is flagged only when it carries a
    non-NULL build that differs from what the map expects — e.g. a GRCh38 gnomAD
    bundle slipping in where the GRCh37 pipeline expects GRCh37. dbNSFP's
    expected build is GRCh38, so its legitimate cross-build coordinates are
    *not* flagged. Rows with a NULL build (not yet stamped, or a build-agnostic
    source) and sources absent from the map are skipped.

    This is advisory provenance — callers **log a warning**, they do not
    hard-fail: the dominant live path joins by rsid and is build-agnostic. The
    check exists to make an unexpected assembly skew visible. Returns an empty
    list when ``database_versions`` is unreachable.
    """
    import sqlalchemy as sa

    from backend.db.tables import database_versions

    flagged: list[str] = []
    try:
        with reference_engine.connect() as conn:
            rows = conn.execute(
                sa.select(
                    database_versions.c.db_name,
                    database_versions.c.genome_build,
                )
            ).fetchall()
    except sa.exc.OperationalError as exc:
        logger.warning("genome_build_consistency_unreadable", error=str(exc))
        return flagged

    for row in rows:
        expected = EXPECTED_GENOME_BUILD.get(row.db_name)
        if expected is None or row.genome_build is None:
            continue
        if row.genome_build != expected:
            flagged.append(row.db_name)
    return flagged


# ── Bundled-DB materialization (offline fallback) ────────────────


def _committed_bundle_version(db_info: DatabaseInfo) -> str:
    """Return an honest version string for a bundled DB's committed fixture.

    The repo ships small fixtures under ``bundles/`` so the app works offline,
    but they are *not* always the current release (notably ``vep_bundle.db`` is
    a small pre-v2.0.0 fixture — the real 358 MB union catalog lives as a GitHub
    release asset). Recording a truthful version keeps the §5.4 AncestryDNA gate
    and the Update Manager honest:

    - ``vep_bundle``: read the fixture's own ``bundle_metadata.bundle_version``;
      fixtures predating v2.0.0 omit that key → fall back to ``"v1.0.0"`` (the
      version the staleness machinery already assumes for pre-Phase-0 bundles).
    - everything else (e.g. ``ancestry_pca``): the committed fixture *is* the
      shipped release, so use the manifest version when reachable.
    """
    import sqlite3

    src = BUNDLED_DIR / db_info.filename

    if db_info.name == "vep_bundle" and src.exists():
        try:
            with sqlite3.connect(str(src)) as conn:
                row = conn.execute(
                    "SELECT value FROM bundle_metadata WHERE key = 'bundle_version'"
                ).fetchone()
            if row and row[0]:
                return str(row[0])
        except Exception:
            pass
        return "v1.0.0"

    from backend.db.manifest import get_bundle_info

    entry = get_bundle_info(db_info.name)
    if entry is not None and entry.version:
        return entry.version
    return "v1.0.0"


def install_committed_bundle(db_info: DatabaseInfo, settings: Settings) -> bool:
    """Materialize a bundled DB from its committed fixture and record a version.

    Copies ``bundles/<filename>`` into ``data_dir`` (only when the destination
    is absent) and upserts a ``database_versions`` row with the honest fixture
    version (:func:`_committed_bundle_version`). This is the *offline fallback*
    install path — the setup wizard prefers the manifest download (real latest
    release) and only falls back here when the release is unreachable or the
    manifest carries no URL (e.g. the out-of-band ``ancestry_pca`` bundle).

    Returns ``True`` when the destination file exists after the call. Version
    recording is best-effort: a missing reference DB is logged, not fatal.
    """
    import sqlalchemy as sa

    src = BUNDLED_DIR / db_info.filename
    dest = db_info.dest_path(settings)
    if not dest.exists():
        if not src.exists():
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dest))
        logger.info(
            "bundled_db_copied",
            db_name=db_info.name,
            src=str(src),
            dest=str(dest),
        )

    version = _committed_bundle_version(db_info)
    ref_path = settings.reference_db_path
    if ref_path.exists():
        try:
            engine = sa.create_engine(f"sqlite:///{ref_path}")
            try:
                _record_db_version(
                    engine,
                    db_name=db_info.name,
                    version=version,
                    file_size_bytes=dest.stat().st_size if dest.exists() else None,
                )
            finally:
                engine.dispose()
        except Exception as exc:
            logger.warning(
                "bundled_db_version_record_failed",
                db_name=db_info.name,
                error=str(exc),
                reference_db=str(ref_path),
            )

    return dest.exists()


# ── Status checking ──────────────────────────────────────────────


def _check_db_version_exists(db_name: str, settings: Settings) -> bool:
    """Check if a database has a record in the database_versions table."""
    import sqlalchemy as sa

    from backend.db.tables import database_versions

    ref_path = settings.reference_db_path
    if not ref_path.exists():
        return False

    engine = sa.create_engine(f"sqlite:///{ref_path}")
    try:
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions.c.db_name).where(
                    database_versions.c.db_name == db_name
                )
            ).fetchone()
        return row is not None
    except Exception:
        return False
    finally:
        engine.dispose()


def get_database_status(db_info: DatabaseInfo, settings: Settings) -> dict:
    """Check the on-disk status of a single database.

    Returns a dict with download/presence status suitable for API responses.
    """
    if db_info.name == "lai_bundle":
        # LAI bundle: the extracted directory is the artifact, not the tarball
        lai_dir = settings.data_dir / "lai_bundle"
        downloaded = validate_lai_bundle(lai_dir)
        file_size = None  # directory, not a single file
    elif db_info.build_mode == "bundled":
        dest = db_info.dest_path(settings)
        bundled_src = BUNDLED_DIR / db_info.filename
        if not dest.exists() and bundled_src.exists():
            # Offline fallback: surface the committed fixture so the app works
            # without a download. This deliberately does NOT record a
            # database_versions row — the version stamp is owned by the explicit
            # install path (install_committed_bundle / the manifest download),
            # keeping this status call side-effect-light and network-free. A
            # versionless vep_bundle stays gated for AncestryDNA (§5.4) and is
            # offered the real-release download by the setup wizard.
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(bundled_src), str(dest))
            logger.info(
                "bundled_db_copied",
                db_name=db_info.name,
                src=str(bundled_src),
                dest=str(dest),
            )
        downloaded = dest.exists()
        file_size = dest.stat().st_size if downloaded else None
    elif db_info.target_db == "reference":
        # reference.db-resident: check database_versions table
        downloaded = _check_db_version_exists(db_info.name, settings)
        file_size = None
    elif db_info.build_mode == "pipeline" and db_info.target_db == "standalone":
        # Standalone pipeline DB (gnomad, dbnsfp, alphamissense): require both the file
        # AND a database_versions entry. A file alone may be a partial
        # write from a crashed build.
        dest = db_info.dest_path(settings)
        file_exists = dest.exists()
        file_size = dest.stat().st_size if file_exists else None
        downloaded = file_exists and _check_db_version_exists(db_info.name, settings)
    else:
        # download or manual mode: file existence is sufficient
        dest = db_info.dest_path(settings)
        downloaded = dest.exists()
        file_size = dest.stat().st_size if downloaded else None

    return {
        "name": db_info.name,
        "display_name": db_info.display_name,
        "description": db_info.description,
        "filename": db_info.filename,
        "expected_size_bytes": db_info.expected_size_bytes,
        "required": db_info.required,
        "phase": db_info.phase,
        "downloaded": downloaded,
        "file_size_bytes": file_size,
        "build_mode": db_info.build_mode,
    }
