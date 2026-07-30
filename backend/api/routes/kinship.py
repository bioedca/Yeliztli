"""Within-account KING-robust kinship / relatedness QC API — roadmap #49.

GET  /api/analysis/kinship/disclaimer
GET  /api/analysis/kinship/findings?sample_id=N
POST /api/analysis/kinship/run?sample_id=N   — compare sample N against the
     account's other local samples (never cross-user) and store relatedness QC.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.analysis.kinship import CATEGORY, MODULE
from backend.api.dependencies import require_fresh_sample
from backend.api.routes.risk_common import resolve_sample_engine
from backend.db.connection import get_registry
from backend.db.tables import findings, samples
from backend.disclaimers import KINSHIP_DISCLAIMER_TEXT, KINSHIP_DISCLAIMER_TITLE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis/kinship", tags=["kinship"])


class KinshipFindingResponse(BaseModel):
    finding_text: str
    relationship: str | None = None
    phi: float | None = None
    ibs0_proportion: float | None = None
    n_shared_snps: int | None = None
    # het_i + het_j, the denominator the KING estimate rests on. `n_shared_snps`
    # counts identical homozygotes that contribute nothing to it, so a large
    # shared count can sit on a negligible -- or zero -- amount of actual
    # evidence (#2170). Exposed so the two cannot be confused.
    informative_denominator: int | None = None
    # "insufficient_shared_snps" | "no_heterozygous_information" | None.
    indeterminate_reason: str | None = None
    other_sample_id: int | None = None
    other_sample_name: str | None = None
    same_vendor: bool | None = None


class KinshipListResponse(BaseModel):
    items: list[KinshipFindingResponse]
    total: int


class KinshipDisclaimerResponse(BaseModel):
    title: str
    text: str


class KinshipRunResponse(BaseModel):
    findings_count: int
    samples_compared: int


def _vendor(file_format: str | None) -> str:
    """Coarse vendor key from a file_format like '23andme_v5' → '23andme'."""
    return (file_format or "").split("_")[0].lower()


@router.get("/disclaimer")
def get_disclaimer() -> KinshipDisclaimerResponse:
    return KinshipDisclaimerResponse(title=KINSHIP_DISCLAIMER_TITLE, text=KINSHIP_DISCLAIMER_TEXT)


@router.get("/findings", dependencies=[Depends(require_fresh_sample)])
def list_findings(sample_id: int = Query(..., description="Sample ID")) -> KinshipListResponse:
    engine = resolve_sample_engine(sample_id)
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(findings)
            .where(findings.c.module == MODULE, findings.c.category == CATEGORY)
            .order_by(findings.c.id)
        ).fetchall()
    items: list[KinshipFindingResponse] = []
    for row in rows:
        detail: dict[str, Any] = {}
        if row.detail_json:
            try:
                detail = json.loads(row.detail_json)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Failed to parse kinship detail_json for id=%s", row.id)
        items.append(
            KinshipFindingResponse(
                finding_text=row.finding_text or "",
                relationship=detail.get("relationship"),
                phi=detail.get("phi"),
                ibs0_proportion=detail.get("ibs0_proportion"),
                n_shared_snps=detail.get("n_shared_snps"),
                informative_denominator=detail.get("informative_denominator"),
                indeterminate_reason=detail.get("indeterminate_reason"),
                other_sample_id=detail.get("other_sample_id"),
                other_sample_name=detail.get("other_sample_name"),
                same_vendor=detail.get("same_vendor"),
            )
        )
    return KinshipListResponse(items=items, total=len(items))


@router.post("/run", dependencies=[Depends(require_fresh_sample)])
def run(sample_id: int = Query(..., description="Sample ID")) -> KinshipRunResponse:
    from backend.analysis.kinship import (
        assess_kinship,
        read_autosomal_genotypes,
        store_kinship_findings,
    )

    registry = get_registry()
    target_engine = resolve_sample_engine(sample_id)

    # Enumerate the account's OTHER local samples (never cross-user — this is a
    # single local instance). Read each one's autosomal genotypes for KING.
    with registry.reference_engine.connect() as conn:
        target_row = conn.execute(
            sa.select(samples.c.file_format).where(samples.c.id == sample_id)
        ).fetchone()
        other_rows = conn.execute(
            sa.select(
                samples.c.id, samples.c.name, samples.c.file_format, samples.c.db_path
            ).where(samples.c.id != sample_id)
        ).fetchall()

    target_genos = read_autosomal_genotypes(target_engine)
    target_vendor = _vendor(target_row.file_format if target_row else None)

    others: list[tuple[int, str, bool, dict[str, str]]] = []
    for r in other_rows:
        path = registry.settings.data_dir / r.db_path
        if not path.exists():
            continue
        other_engine = registry.get_sample_engine(path)
        genos = read_autosomal_genotypes(other_engine)
        others.append(
            (r.id, r.name or f"Sample {r.id}", _vendor(r.file_format) == target_vendor, genos)
        )

    result = assess_kinship(sample_id, target_genos, others)
    count = store_kinship_findings(result, target_engine)
    return KinshipRunResponse(findings_count=count, samples_compared=result.samples_compared)
