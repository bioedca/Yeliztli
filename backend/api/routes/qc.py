"""Sample QC metrics API + reference-bias disclosure — EXPANSION_STRATEGY.md #9.

GET  /api/analysis/qc/disclaimer
GET  /api/analysis/qc/metrics?sample_id=N   — stored metrics + interpretation
POST /api/analysis/qc/run?sample_id=N       — (re)compute and persist qc_metrics
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.analysis.qc import CALL_RATE_PASS, het_outlier_zscore, sex_check
from backend.api.dependencies import require_fresh_sample
from backend.api.routes.risk_common import resolve_sample_engine
from backend.db.connection import get_registry
from backend.db.tables import individuals, qc_metrics, samples
from backend.disclaimers import QC_DISCLAIMER_TEXT, QC_DISCLAIMER_TITLE
from backend.services.sex_inference import infer_biological_sex

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis/qc", tags=["qc"])


class QCDisclaimerResponse(BaseModel):
    title: str
    text: str


class QCMetricsResponse(BaseModel):
    computed: bool
    call_rate: float | None = None
    call_rate_pass: bool | None = None
    heterozygosity_rate: float | None = None
    ti_tv_ratio: float | None = None
    total_variants: int | None = None
    called_variants: int | None = None
    nocall_variants: int | None = None
    genetic_sex: str | None = None
    recorded_sex: str | None = None
    sex_check: str | None = None
    het_outlier_z: float | None = None
    het_outlier_status: str | None = None


class QCRunResponse(BaseModel):
    computed: bool
    call_rate: float
    call_rate_pass: bool


def _recorded_sex(registry, sample_id: int) -> str | None:
    with registry.reference_engine.connect() as conn:
        return conn.execute(
            sa.select(individuals.c.biological_sex)
            .select_from(samples.join(individuals, samples.c.individual_id == individuals.c.id))
            .where(samples.c.id == sample_id)
        ).scalar()


def _sample_file_format(registry, sample_id: int) -> str | None:
    with registry.reference_engine.connect() as conn:
        return conn.execute(
            sa.select(samples.c.file_format).where(samples.c.id == sample_id)
        ).scalar()


def _other_sample_het_rates(registry, sample_id: int) -> list[float]:
    """Read other local samples' stored het rates from the SAME genotyping array.

    Heterozygosity rate is strongly array-ascertainment-dependent — SNP arrays
    "massively overestimate" expected heterozygosity, biased by each panel's
    discovery design, so array H is not comparable across different arrays (the
    same person can differ ~1.7x between 23andMe and AncestryDNA — issue #563).
    A het-outlier z-score is therefore only valid *within* one array, so we
    compare only against other samples sharing this sample's ``file_format``.
    A mixed-array account no longer produces chip-confounded "outlier" flags: a
    minority-array sample simply has too few same-array peers and is reported
    ``insufficient_samples`` rather than a spurious contamination alarm.

    When this sample's ``file_format`` is unknown, no comparable cohort can be
    guaranteed, so we withhold (return an empty cohort).
    """
    file_format = _sample_file_format(registry, sample_id)
    if file_format is None:
        return []
    with registry.reference_engine.connect() as conn:
        rows = conn.execute(
            sa.select(samples.c.db_path).where(
                samples.c.id != sample_id,
                samples.c.file_format == file_format,
            )
        ).fetchall()
    rates: list[float] = []
    for row in rows:
        path = registry.settings.data_dir / row.db_path
        if not path.exists():
            continue
        try:
            engine = registry.get_sample_engine(path)
            with engine.connect() as conn:
                rate = conn.execute(
                    sa.select(qc_metrics.c.heterozygosity_rate).order_by(qc_metrics.c.id.desc())
                ).scalar()
            if rate is not None:
                rates.append(float(rate))
        except sa.exc.SQLAlchemyError:
            continue  # sample without a qc_metrics table/row yet — skip
    return rates


@router.get("/disclaimer")
def get_disclaimer() -> QCDisclaimerResponse:
    return QCDisclaimerResponse(title=QC_DISCLAIMER_TITLE, text=QC_DISCLAIMER_TEXT)


@router.get("/metrics", dependencies=[Depends(require_fresh_sample)])
def get_metrics(sample_id: int = Query(..., description="Sample ID")) -> QCMetricsResponse:
    registry = get_registry()
    engine = resolve_sample_engine(sample_id)
    with engine.connect() as conn:
        row = conn.execute(sa.select(qc_metrics).order_by(qc_metrics.c.id.desc())).fetchone()
    if row is None:
        return QCMetricsResponse(computed=False)

    genetic_sex = infer_biological_sex(engine)
    recorded = _recorded_sex(registry, sample_id)
    others = _other_sample_het_rates(registry, sample_id)
    z = het_outlier_zscore(row.heterozygosity_rate, others)
    if z is None:
        het_status = "insufficient_samples"
    elif abs(z) > 3:
        het_status = "outlier"
    else:
        het_status = "within_range"

    return QCMetricsResponse(
        computed=True,
        call_rate=row.call_rate,
        call_rate_pass=row.call_rate is not None and row.call_rate >= CALL_RATE_PASS,
        heterozygosity_rate=row.heterozygosity_rate,
        ti_tv_ratio=row.ti_tv_ratio,
        total_variants=row.total_variants,
        called_variants=row.called_variants,
        nocall_variants=row.nocall_variants,
        genetic_sex=genetic_sex,
        recorded_sex=recorded,
        sex_check=sex_check(genetic_sex, recorded),
        het_outlier_z=z,
        het_outlier_status=het_status,
    )


@router.post("/run", dependencies=[Depends(require_fresh_sample)])
def run(sample_id: int = Query(..., description="Sample ID")) -> QCRunResponse:
    from backend.analysis.qc import compute_qc_metrics, store_qc_metrics

    engine = resolve_sample_engine(sample_id)
    metrics = compute_qc_metrics(engine)
    store_qc_metrics(metrics, engine)
    return QCRunResponse(
        computed=True,
        call_rate=metrics.call_rate,
        call_rate_pass=metrics.call_rate >= CALL_RATE_PASS,
    )
