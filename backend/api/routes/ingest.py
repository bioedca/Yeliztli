"""Ingestion API endpoints (P1-13).

POST /api/ingest        — Upload a 23andMe or AncestryDNA file, parse, store, return 202
GET  /api/ingest/status/{job_id} — Poll parse job progress (SSE)
"""

from __future__ import annotations

import hashlib
import io
import logging
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, UploadFile
from packaging.version import InvalidVersion, Version

from backend.api.sse import job_progress_stream, sse_response
from backend.db.connection import get_registry
from backend.db.database_registry import DATABASES
from backend.db.manifest import get_bundle_info
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import database_versions, jobs, raw_variants, sample_metadata_table, samples
from backend.ingestion.dispatcher import ParserError, parse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingestion"])

# Batch size for bulk inserts
_INSERT_BATCH = 10_000

# Minimum vep_bundle semver required to accept AncestryDNA uploads (Plan §5.4).
_VEP_BUNDLE_MIN_FOR_ANCESTRYDNA = Version("2.0.0")

# The only genome build the analysis pipeline can process. Vendor parsers tag
# each ``ParseResult`` with its source reference assembly (parser_23andme:
# v3→GRCh36 — the parser's label for NCBI Build 36 / hg18, which predates the
# GRC — v4/v5→GRCh37; AncestryDNA→GRCh37). Every position-dependent path —
# GRCh38 liftover (backend/ingestion/liftover.py), positional PRS/annotation
# joins (backend/analysis/prs.py, backend/annotation/engine.py), VCF/FHIR export
# — assumes the stored (chrom, pos) are GRCh37, and there is no build-36→37
# (hg18→hg19) liftover. A 23andMe v3 file is build 36 (hg18); stored verbatim it
# would be silently mis-placed by up to several megabases at the wrong build,
# producing wrong polygenic scores with no error (issue #480). Until an
# hg18→hg19 lift lands, reject non-GRCh37 builds at ingest rather than emit
# silently-wrong coordinates and scores.
_SUPPORTED_BUILD = "GRCh37"


def _coerce_semver(raw: str | None) -> Version | None:
    """Parse a manifest/version-row string into a ``Version`` if possible."""
    if not raw:
        return None
    try:
        return Version(raw.lstrip("v"))
    except InvalidVersion:
        return None


def _build_bundle_gate_payload(installed_version: str | None) -> dict:
    """Build the §5.4 HTTP 409 payload, preferring manifest fields."""
    manifest_entry = get_bundle_info("vep_bundle")
    registry_entry = DATABASES.get("vep_bundle")

    if manifest_entry is not None:
        update_url = manifest_entry.url or (registry_entry.url if registry_entry else "")
        size_bytes = manifest_entry.size_bytes
        sha256 = manifest_entry.sha256
        required = manifest_entry.version
    else:
        update_url = registry_entry.url if registry_entry else ""
        size_bytes = registry_entry.expected_size_bytes if registry_entry else 0
        sha256 = registry_entry.sha256 if registry_entry else None
        # Manifest unreachable → advertise the gate *floor* (the minimum semver
        # that unblocks AncestryDNA), derived from the threshold constant so it
        # never drifts when the manifest's latest version is bumped (e.g. G1's
        # v3.0.0). This only surfaces to users below the floor, so the floor is
        # the honest "what you need at least" when the latest is unknown.
        required = f"v{_VEP_BUNDLE_MIN_FOR_ANCESTRYDNA}"

    return {
        "error": "bundle_version_too_old",
        "installed_version": installed_version or "v1.0.0",
        "required_version": required,
        "vendor": "ancestrydna",
        "update_url": update_url,
        "size_bytes": size_bytes,
        "checksum_sha256": sha256,
    }


def _vep_bundle_blocks_ancestrydna(reference_engine: sa.Engine) -> tuple[bool, str | None]:
    """Read the installed vep_bundle semver and decide whether to gate.

    Returns ``(should_block, installed_version_raw)``. Block when the
    installed version is missing, malformed, or strictly below v2.0.0
    (per Plan §5.4 — partial-hit annotation is clinically misleading).
    """
    with reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(database_versions.c.version).where(
                database_versions.c.db_name == "vep_bundle"
            )
        ).fetchone()
    installed_raw = row.version if row else None
    installed = _coerce_semver(installed_raw)
    if installed is None:
        return True, installed_raw
    return installed < _VEP_BUNDLE_MIN_FOR_ANCESTRYDNA, installed_raw


def _ingest_file(file_bytes: bytes, filename: str) -> dict:
    """Parse a vendor raw-data file (23andMe or AncestryDNA) and persist it.

    This is the synchronous core of the ingest endpoint. For v1 (< 2 min
    parse time), this runs inline. Huey background tasks will wrap this
    in Phase 2 for the annotation pipeline.

    Returns a dict with sample_id, job_id, variant_count, nocall_count.
    """
    registry = get_registry()
    settings = registry.settings

    # Compute SHA-256 of the uploaded file
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    # Parse the file content (pure, no side effects). The dispatcher routes
    # by vendor head-line and returns a unified ``base.ParseResult`` with a
    # string ``version`` field (Plan \u00a78.7).
    text = file_bytes.decode("utf-8", errors="replace")
    if "\ufffd" in text:
        logger.warning("File %s contains invalid UTF-8 sequences that were replaced", filename)
    result = parse(io.StringIO(text))

    # Genome-build gate (issue #480), keyed off the *parsed* build so it cannot
    # be bypassed by an unusual header. The pipeline treats every sample's
    # (chrom, pos) as GRCh37 and there is no build-36→37 liftover, so a build-36
    # (23andMe v3 / hg18) file accepted here would be silently processed ~Mb off
    # at the wrong build — corrupting the genome browser, GRCh38 liftover, and
    # polygenic scores (a clinical-tier result) with no error. rsID-keyed paths
    # would still work, but shipping silently-wrong positional results is not
    # worth it: refuse before any sample/job rows are written. The detail is a
    # plain string so the upload UI renders it verbatim (api/setup.ts only shows
    # string ``detail``; an object falls back to a generic "Upload failed").
    if result.build != _SUPPORTED_BUILD:
        logger.info(
            "genome_build_gate vendor=%s version=%s build=%s",
            result.vendor.value,
            result.version,
            result.build,
        )
        raise HTTPException(
            status_code=422,
            detail=(
                f"This file is reported on genome build {result.build}, but analysis "
                f"requires build 37 ({_SUPPORTED_BUILD}). 23andMe v3 exports use NCBI "
                "build 36 (hg18); processing build-36 coordinates as GRCh37 would "
                "silently shift variant positions — by up to several megabases in "
                "affected regions — and produce incorrect polygenic scores, so the "
                "upload was refused. "
                "Please upload a build-37 export (23andMe v4/v5 or AncestryDNA)."
            ),
        )

    # §5.4 bundle-version gate, keyed off the *parsed* vendor (not a pre-parse
    # byte sniff) so it cannot be bypassed by an unusual header. AncestryDNA
    # uploads against a pre-v2.0.0 vep_bundle are rejected with the structured
    # 409 payload before any sample/job rows are written.
    if result.vendor.value == "ancestrydna":
        should_block, installed_raw = _vep_bundle_blocks_ancestrydna(registry.reference_engine)
        if should_block:
            payload = _build_bundle_gate_payload(installed_raw)
            logger.info(
                "ancestrydna_bundle_gate installed=%s required=%s",
                payload["installed_version"],
                payload["required_version"],
            )
            raise HTTPException(status_code=409, detail=payload)

    file_format = f"{result.vendor.value}_{result.version}"

    # Register sample in reference.db
    now = datetime.now(UTC)
    with registry.reference_engine.begin() as conn:
        row = conn.execute(
            samples.insert()
            .values(
                name=filename,
                db_path="",  # placeholder, updated below
                file_format=file_format,
                file_hash=file_hash,
                created_at=now,
            )
            .returning(samples.c.id)
        )
        sample_id = row.scalar_one()

        # Set db_path now that we have the id
        db_path = f"samples/sample_{sample_id}.db"
        conn.execute(samples.update().where(samples.c.id == sample_id).values(db_path=db_path))

    # Create the per-sample database
    sample_db_path = settings.data_dir / db_path
    sample_db_path.parent.mkdir(parents=True, exist_ok=True)
    sample_engine = registry.get_sample_engine(sample_db_path)
    create_sample_tables(sample_engine)

    # Write sample metadata (single-row table)
    with sample_engine.begin() as conn:
        conn.execute(
            sample_metadata_table.insert().values(
                id=1,
                name=filename,
                file_format=file_format,
                file_hash=file_hash,
                created_at=now,
            )
        )

    # Bulk-insert raw variants in batches
    variant_dicts = [
        {
            "rsid": v.rsid,
            "chrom": v.chrom,
            "pos": v.pos,
            "genotype": v.genotype,
        }
        for v in result.variants
    ]
    with sample_engine.begin() as conn:
        for i in range(0, len(variant_dicts), _INSERT_BATCH):
            batch = variant_dicts[i : i + _INSERT_BATCH]
            conn.execute(raw_variants.insert(), batch)

    # Create a job record to track status
    job_id = str(uuid.uuid4())
    with registry.reference_engine.begin() as conn:
        conn.execute(
            jobs.insert().values(
                job_id=job_id,
                sample_id=sample_id,
                job_type="ingest",
                status="complete",
                progress_pct=100.0,
                message=f"Parsed {len(result.variants)} variants",
                created_at=now,
                updated_at=now,
            )
        )

    return {
        "sample_id": sample_id,
        "job_id": job_id,
        "variant_count": len(result.variants),
        "nocall_count": result.nocall_count,
        "file_format": file_format,
    }


@router.post("", status_code=202)
async def ingest_file(file: UploadFile) -> dict:
    """Upload and parse a 23andMe or AncestryDNA raw data file.

    Routing to the per-vendor parser is delegated to
    :func:`backend.ingestion.dispatcher.parse` (Plan §8.7). The returned
    ``file_format`` is composed as ``f"{vendor.value}_{version}"`` (e.g.
    ``"23andme_v5"`` or ``"ancestrydna_v2.0"``).

    Returns 202 Accepted with sample_id and job_id for status polling.
    AncestryDNA uploads against a pre-v2.0.0 vep_bundle return 409 with
    the structured update payload from Plan §5.4.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file.")

    try:
        result = _ingest_file(file_bytes, file.filename)
    except ParserError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return result


@router.get("/status/{job_id}")
async def ingest_status(job_id: str):
    """Stream ingest job progress via SSE."""
    registry = get_registry()
    stream = job_progress_stream(registry.reference_engine, job_id)
    return sse_response(stream)
