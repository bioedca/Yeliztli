"""APOE genotype and findings API with opt-in disclosure gate (P3-22c).

The APOE ε4 opt-in gate blocks access to APOE findings until the user
has explicitly acknowledged the disclosure. Gate state is persisted in
the per-sample DB (apoe_gate table) and checked on every findings request.

GET  /api/analysis/apoe/disclaimer                   — APOE gate disclosure text
GET  /api/analysis/apoe/gate-status?sample_id=N      — Check gate acknowledgment
POST /api/analysis/apoe/acknowledge-gate?sample_id=N — Acknowledge the gate
GET  /api/analysis/apoe/genotype?sample_id=N         — Genotype status (ε4 fields gate-protected)
GET  /api/analysis/apoe/findings?sample_id=N         — Findings (gate-protected)
POST /api/analysis/apoe/run?sample_id=N              — Run APOE analysis
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.api.dependencies import require_fresh_sample
from backend.api.gating import apoe_gate_status
from backend.db.connection import get_registry
from backend.db.tables import apoe_gate, findings, samples
from backend.disclaimers import (
    APOE_GATE_ACCEPT_LABEL,
    APOE_GATE_DECLINE_LABEL,
    APOE_GATE_TEXT,
    APOE_GATE_TITLE,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis/apoe", tags=["apoe"])


# ── Response models ──────────────────────────────────────────────────


class APOEGateDisclaimerResponse(BaseModel):
    """APOE gate disclosure text (hardcoded in disclaimers.py)."""

    title: str
    text: str
    accept_label: str
    decline_label: str


class APOEGateStatusResponse(BaseModel):
    """Current APOE gate acknowledgment state for a sample."""

    acknowledged: bool
    acknowledged_at: str | None = None


class APOEGateAcknowledgeResponse(BaseModel):
    """Result of acknowledging the APOE gate."""

    acknowledged: bool
    acknowledged_at: str


class APOEGenotypeResponse(BaseModel):
    """Basic APOE genotype status.

    The ε4-bearing fields (``diplotype``, ``has_e4``, ``e4_count``, ``has_e2``,
    ``e2_count``, raw SNP genotypes) are gate-protected: they are populated only
    after the APOE disclosure gate is acknowledged. Before acknowledgment a
    determined genotype is reported as ``determined_but_locked`` with every
    sensitive field ``None`` (issue #46).
    """

    status: str  # determined / determined_but_locked / not_run
    diplotype: str | None = None
    has_e4: bool | None = None
    e4_count: int | None = None
    has_e2: bool | None = None
    e2_count: int | None = None
    rs429358_genotype: str | None = None
    rs7412_genotype: str | None = None


class APOEFindingResponse(BaseModel):
    """A single APOE finding (CV risk, Alzheimer's, lipid/dietary)."""

    category: str
    evidence_level: int
    finding_text: str
    phenotype: str | None = None
    conditions: str | None = None
    diplotype: str | None = None
    pmid_citations: list[str] = []
    detail_json: dict[str, Any] = {}


class APOEFindingsListResponse(BaseModel):
    """All APOE findings for a sample (gate-protected)."""

    items: list[APOEFindingResponse]
    total: int


class APOERunResponse(BaseModel):
    """Result of running APOE analysis.

    ``diplotype`` is gate-protected (issue #111): it encodes ε4 status — the
    Alzheimer-risk disclosure the gate exists to gate — so it is populated only
    after the APOE disclosure gate is acknowledged. Before acknowledgment it is
    ``None`` while ``genotype_stored`` / ``findings_count`` still report that the
    run completed and stored results.
    """

    genotype_stored: bool
    findings_count: int
    diplotype: str | None = None


# ── Helpers ──────────────────────────────────────────────────────────


def _get_sample_engine(sample_id: int) -> sa.Engine:
    """Resolve sample_id to a per-sample DB engine."""
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Sample {sample_id} not found.")

    sample_db_path = registry.settings.data_dir / row.db_path
    if not sample_db_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Sample database file not found for sample {sample_id}.",
        )
    return registry.get_sample_engine(sample_db_path)


def _is_gate_acknowledged(sample_engine: sa.Engine) -> tuple[bool, str | None]:
    """Check whether the APOE gate has been acknowledged for this sample.

    Delegates to the shared :func:`backend.api.gating.apoe_gate_status` helper
    so the gate check has a single source of truth across every route that can
    surface APOE findings (the dedicated APOE routes here and the generic
    aggregator in ``routes/findings.py``, issue #222).

    Returns:
        Tuple of (acknowledged: bool, acknowledged_at: str | None).
    """
    return apoe_gate_status(sample_engine)


def _ensure_gate_acknowledged(sample_engine: sa.Engine) -> None:
    """Raise 403 if the APOE gate has not been acknowledged."""
    acknowledged, _ = _is_gate_acknowledged(sample_engine)
    if not acknowledged:
        raise HTTPException(
            status_code=403,
            detail=(
                "APOE disclosure gate has not been acknowledged. "
                "You must acknowledge the APOE gate before viewing findings."
            ),
        )


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/disclaimer")
def get_apoe_disclaimer() -> APOEGateDisclaimerResponse:
    """Return the APOE gate disclosure text.

    This text is hardcoded in ``disclaimers.py`` and is not configurable.
    The gate is non-dismissible — the user must actively choose to view
    or skip APOE information.

    Example: ``GET /api/analysis/apoe/disclaimer``
    """
    return APOEGateDisclaimerResponse(
        title=APOE_GATE_TITLE,
        text=APOE_GATE_TEXT,
        accept_label=APOE_GATE_ACCEPT_LABEL,
        decline_label=APOE_GATE_DECLINE_LABEL,
    )


@router.get("/gate-status", dependencies=[Depends(require_fresh_sample)])
def get_gate_status(
    sample_id: int = Query(..., description="Sample ID"),
) -> APOEGateStatusResponse:
    """Check whether the APOE gate has been acknowledged for a sample.

    Example: ``GET /api/analysis/apoe/gate-status?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)
    acknowledged, acknowledged_at = _is_gate_acknowledged(sample_engine)
    return APOEGateStatusResponse(
        acknowledged=acknowledged,
        acknowledged_at=acknowledged_at,
    )


@router.post("/acknowledge-gate", dependencies=[Depends(require_fresh_sample)])
def acknowledge_gate(
    sample_id: int = Query(..., description="Sample ID"),
) -> APOEGateAcknowledgeResponse:
    """Acknowledge the APOE disclosure gate for a sample.

    Persists the acknowledgment state in the sample database. Once
    acknowledged, the gate does not re-appear for this sample.

    Example: ``POST /api/analysis/apoe/acknowledge-gate?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)
    now = datetime.now(tz=UTC)

    with sample_engine.begin() as conn:
        row = conn.execute(sa.select(apoe_gate.c.id).where(apoe_gate.c.id == 1)).fetchone()

        if row is None:
            # Insert initial row
            conn.execute(
                sa.insert(apoe_gate).values(
                    id=1,
                    acknowledged=True,
                    acknowledged_at=now,
                )
            )
        else:
            # Update existing row
            conn.execute(
                sa.update(apoe_gate)
                .where(apoe_gate.c.id == 1)
                .values(
                    acknowledged=True,
                    acknowledged_at=now,
                )
            )

    logger.info(
        "apoe_gate_acknowledged sample_id=%s acknowledged_at=%s",
        sample_id,
        now.isoformat(),
    )

    return APOEGateAcknowledgeResponse(
        acknowledged=True,
        acknowledged_at=now.isoformat(),
    )


@router.get("/genotype", dependencies=[Depends(require_fresh_sample)])
def get_apoe_genotype(
    sample_id: int = Query(..., description="Sample ID"),
) -> APOEGenotypeResponse:
    """Get APOE genotype status for a sample, gated on the disclosure acknowledgment.

    APOE ε4 status is itself the sensitive Alzheimer-risk disclosure (the gate
    exists precisely to let a user choose whether to learn it), so the ε4-bearing
    fields are released only after the gate is acknowledged. Before that:

    - determined genotype stored → ``determined_but_locked`` with all sensitive
      fields (``diplotype``/``has_e4``/``e4_count``/``has_e2``/``e2_count``/raw
      SNPs) ``None``
    - analysis ran but the genotype is un-callable → its real
      ``missing_snps`` / ``no_call`` / ``ambiguous`` status (these carry NO ε4
      information, so they are surfaced un-gated; #806)
    - a genotype is derivable but not yet analyzed/persisted → ``not_run``

    After acknowledgment the full genotype is returned (status ``determined``).
    This mirrors the gate on ``/findings`` so ε4 status cannot be read ahead of
    the disclosure (issue #46).

    Example: ``GET /api/analysis/apoe/genotype?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)

    with sample_engine.connect() as conn:
        row = conn.execute(
            sa.select(findings).where(
                findings.c.module == "apoe",
                findings.c.category == "genotype",
            )
        ).fetchone()

    if row is None:
        # No determined genotype is stored. Distinguish a genuinely un-run
        # analysis from one that ran but is un-callable (missing SNPs / no-call /
        # ambiguous). Those three carry NO ε4 information, so they need no gating
        # and are surfaced directly — only `determined` is sensitive (and lives
        # in the stored+gated row below). `not_run` is reserved for the case
        # where a genotype is derivable but has not yet been analyzed/persisted,
        # so the dead `missing_snps`/`no_call`/`ambiguous` frontend branches now
        # receive their real status instead of a false "not run yet" (#806).
        from backend.analysis.apoe import APOEStatus, determine_apoe_genotype

        result = determine_apoe_genotype(sample_engine)
        if result.status != APOEStatus.DETERMINED:
            return APOEGenotypeResponse(status=result.status.value)
        return APOEGenotypeResponse(status="not_run")

    # Gate boundary: a determined genotype exists, but ε4/diplotype must not be
    # disclosed until the user acknowledges the APOE gate. Report only that a
    # result is present-but-locked, with no sensitive fields.
    acknowledged, _ = _is_gate_acknowledged(sample_engine)
    if not acknowledged:
        return APOEGenotypeResponse(status="determined_but_locked")

    detail: dict[str, Any] = {}
    if row.detail_json:
        try:
            detail = json.loads(row.detail_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to parse genotype detail_json")

    return APOEGenotypeResponse(
        status="determined",
        diplotype=row.diplotype,
        has_e4=detail.get("has_e4"),
        e4_count=detail.get("e4_count"),
        has_e2=detail.get("has_e2"),
        e2_count=detail.get("e2_count"),
        rs429358_genotype=detail.get("rs429358_genotype"),
        rs7412_genotype=detail.get("rs7412_genotype"),
    )


@router.get("/findings", dependencies=[Depends(require_fresh_sample)])
def list_apoe_findings(
    sample_id: int = Query(..., description="Sample ID"),
) -> APOEFindingsListResponse:
    """List all APOE findings for a sample (gate-protected).

    Returns the three APOE findings (cardiovascular risk, Alzheimer's risk,
    lipid/dietary context) ONLY if the APOE disclosure gate has been
    acknowledged for this sample. Returns 403 if the gate is not yet
    acknowledged.

    Example: ``GET /api/analysis/apoe/findings?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)
    _ensure_gate_acknowledged(sample_engine)

    with sample_engine.connect() as conn:
        rows = conn.execute(
            sa.select(findings)
            .where(
                findings.c.module == "apoe",
                findings.c.category != "genotype",
            )
            .order_by(findings.c.evidence_level.desc(), findings.c.category)
        ).fetchall()

    items: list[APOEFindingResponse] = []
    for row in rows:
        detail: dict[str, Any] = {}
        if row.detail_json:
            try:
                detail = json.loads(row.detail_json)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Failed to parse detail_json for finding id=%s", row.id)

        pmids: list[str] = []
        if row.pmid_citations:
            try:
                pmids = json.loads(row.pmid_citations)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Failed to parse pmid_citations for finding id=%s", row.id)

        items.append(
            APOEFindingResponse(
                category=row.category or "",
                evidence_level=row.evidence_level or 1,
                finding_text=row.finding_text or "",
                phenotype=row.phenotype,
                conditions=row.conditions,
                diplotype=row.diplotype,
                pmid_citations=pmids,
                detail_json=detail,
            )
        )

    return APOEFindingsListResponse(items=items, total=len(items))


@router.post("/run", dependencies=[Depends(require_fresh_sample)])
def run_apoe_analysis(
    sample_id: int = Query(..., description="Sample ID"),
) -> APOERunResponse:
    """Run or re-run APOE genotype determination and findings generation.

    Determines the APOE diplotype from rs429358 + rs7412, stores the
    genotype finding, and generates the three APOE findings (CV risk,
    Alzheimer's, lipid/dietary).

    Note: Running the analysis does NOT acknowledge the gate. The user
    must still explicitly acknowledge the disclosure before results are
    visible. The ε4-bearing ``diplotype`` (e.g. ``ε3/ε4``) directly encodes
    the Alzheimer-risk disclosure the gate exists to protect, so it is
    withheld from this response until the gate is acknowledged (issue #111,
    same boundary as ``/genotype`` from #46): before acknowledgment only
    ``genotype_stored`` / ``findings_count`` are returned, with
    ``diplotype`` ``None``. Re-invoking ``/run`` after acknowledgment (or
    calling ``/genotype``) returns the diplotype.

    Example: ``POST /api/analysis/apoe/run?sample_id=1``
    """
    from backend.analysis.apoe import (
        determine_apoe_genotype,
        store_apoe_finding,
        store_apoe_three_findings,
    )

    sample_engine = _get_sample_engine(sample_id)

    # P3-22a: Genotype determination
    result = determine_apoe_genotype(sample_engine)
    genotype_stored = store_apoe_finding(result, sample_engine) > 0

    # P3-22b: Three findings generation
    findings_count = store_apoe_three_findings(result, sample_engine)

    # Gate boundary (issue #111): the diplotype encodes ε4 status — the very
    # Alzheimer-risk disclosure the gate exists to gate. Determine and store
    # results so acknowledgment later reveals them, but withhold the diplotype
    # from the run response until the gate is acknowledged (mirrors /genotype).
    acknowledged, _ = _is_gate_acknowledged(sample_engine)

    return APOERunResponse(
        genotype_stored=genotype_stored,
        findings_count=findings_count,
        diplotype=result.diplotype if acknowledged else None,
    )
