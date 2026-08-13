"""Rare variant finder API (P3-29).

Accept filter params, return matching variants, export as VCF/TSV.

POST /api/analysis/rare-variants/search?sample_id=N     — Search with filters
GET  /api/analysis/rare-variants/findings?sample_id=N    — Stored findings
GET  /api/analysis/rare-variants/export/tsv?sample_id=N  — Export as TSV
GET  /api/analysis/rare-variants/export/vcf?sample_id=N  — Export as VCF
"""

from __future__ import annotations

import io
import json
import logging
import tempfile
from collections.abc import Iterator, Sequence
from datetime import date
from itertools import chain
from typing import IO, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from backend.analysis.pharmacogenomics import (
    ResponsePayloadHeldPairScanner,
    is_patient_presentable_finding_payload,
    is_patient_presentable_rendered_text_chunks,
    is_patient_presentable_response_payload,
)
from backend.analysis.rare_variant_finder import (
    DEFAULT_AF_THRESHOLD,
    RareVariantFilter,
    RareVariantResult,
    find_rare_variants,
    store_rare_variant_findings,
)
from backend.api.dependencies import require_fresh_sample
from backend.db.connection import get_registry
from backend.db.tables import annotated_variants, findings, samples
from backend.services.sex_inference import (
    get_recorded_biological_sex,
    infer_biological_sex,
    resolve_biological_sex,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/analysis/rare-variants",
    tags=["rare-variants"],
    dependencies=[Depends(require_fresh_sample)],
)


# ── Request / response models ────────────────────────────────────────


class RareVariantFilterRequest(BaseModel):
    """Filter parameters accepted by the search endpoint."""

    gene_symbols: list[str] | None = Field(
        default=None, description="Gene symbols to filter by (custom gene panel)"
    )
    af_threshold: float = Field(
        default=DEFAULT_AF_THRESHOLD,
        ge=0.0,
        le=1.0,
        description="Maximum gnomAD global allele frequency (0–1)",
    )
    consequences: list[str] | None = Field(
        default=None, description="SO consequence terms to filter by"
    )
    clinvar_significance: list[str] | None = Field(
        default=None, description="ClinVar significance values to filter by"
    )
    include_novel: bool = Field(default=True, description="Include variants with no gnomAD data")
    zygosity: str | None = Field(
        default=None, description="Filter by zygosity: 'het' or 'hom_alt'"
    )


class RareVariantResponse(BaseModel):
    """A single rare variant in the response."""

    rsid: str
    chrom: str
    pos: int
    ref: str | None = None
    alt: str | None = None
    genotype: str | None = None
    zygosity: str | None = None
    zygosity_label: str | None = None
    gene_symbol: str | None = None
    consequence: str | None = None
    hgvs_coding: str | None = None
    hgvs_protein: str | None = None
    gnomad_af_global: float | None = None
    gnomad_af_afr: float | None = None
    gnomad_af_amr: float | None = None
    gnomad_af_asj: float | None = None
    gnomad_af_eas: float | None = None
    gnomad_af_eur: float | None = None
    gnomad_af_fin: float | None = None
    gnomad_af_sas: float | None = None
    gnomad_source_status: str | None = None
    # Genuine novelty (#866): absent from gnomAD AND uncatalogued (no dbSNP rs id /
    # ClinVar). gnomAD-absence alone is NOT novelty — the exome-biased gnomAD bundle
    # lacks most catalogued chip SNPs. The UI must render "Novel" from this, not from
    # gnomad_af_global == null (which mislabels catalogued, gnomAD-absent variants).
    is_novel: bool = False
    clinvar_significance: str | None = None
    clinvar_low_penetrance_or_risk_allele: bool = False
    clinvar_review_stars: int | None = None
    clinvar_accession: str | None = None
    clinvar_conditions: str | None = None
    cadd_phred: float | None = None
    revel: float | None = None
    deleterious_count: int | None = None
    deleterious_total_assessed: int | None = None
    ensemble_pathogenic: bool = False
    evidence_conflict: bool = False
    evidence_level: int = 1
    disease_name: str | None = None
    inheritance_pattern: str | None = None


class RareVariantSearchResponse(BaseModel):
    """Response from the search endpoint."""

    items: list[RareVariantResponse]
    total: int
    total_variants_scanned: int
    novel_count: int
    pathogenic_count: int
    genes_with_findings: list[str]
    filters_applied: RareVariantFilterRequest


class RareVariantFindingResponse(BaseModel):
    """A stored finding from the findings table."""

    rsid: str | None = None
    gene_symbol: str | None = None
    category: str
    evidence_level: int = 1
    finding_text: str
    zygosity: str | None = None
    zygosity_label: str | None = None
    clinvar_significance: str | None = None
    clinvar_low_penetrance_or_risk_allele: bool = False
    conditions: str | None = None
    detail: dict[str, Any] = {}


class RareVariantFindingsListResponse(BaseModel):
    """All stored rare variant findings for a sample."""

    items: list[RareVariantFindingResponse]
    total: int


class RareVariantRunResponse(BaseModel):
    """Result of running the rare variant finder.

    ``items`` carries the matched variants for an exploratory run only. Such a
    run stores nothing, so its matches are unreachable through ``/findings``
    and this response is the caller's only view of them. A canonical run
    persists its matches instead and leaves ``items`` empty; read those back
    through the paginated ``/findings`` endpoint rather than inflating this
    response with a full, unpaginated result set.
    """

    variants_found: int
    findings_stored: int
    total_variants_scanned: int
    novel_count: int
    pathogenic_count: int
    genes_with_findings: list[str]
    items: list[RareVariantResponse] = []


# ── Helpers ──────────────────────────────────────────────────────────


def _payload_gate_projection(payload: dict[str, Any]) -> dict[str, Any]:
    """Omit only an absent intergenic gene label for aggregate gating.

    Stored rows first pass the full finding gate. A response model legitimately
    represents an intergenic locus as ``gene_symbol=None``; the generic
    prescribing gate reserves a present-but-blank identifier for malformed
    legacy clinical records. Keep the public DTO unchanged while omitting only
    None or empty labels from the gate-only projection. Whitespace or non-string
    values remain visible to the generic gate and therefore fail closed.
    """
    return {
        key: value
        for key, value in payload.items()
        if key != "gene_symbol" or not (value is None or isinstance(value, str) and value == "")
    }


# Rows pulled from SQLite per round trip. Matches ``_SQL_EXPORT_BATCH`` in
# backend/api/routes/export.py so both bounded producers age together.
_RARE_VARIANT_EXPORT_BATCH = 5_000

# Rendered TSV characters kept in RAM before the replay spool moves to a
# temporary file. ~4 MB of body — roughly 40k typical rows — so an ordinary
# export never touches disk, and a pathological one still cannot grow the
# process without bound. This is the documented threshold #2328 asks for:
# peak memory is a function of this constant, not of the result count.
_RARE_VARIANT_TSV_SPOOL_MAX_CHARS = 4_000_000


class _TsvReplaySpool:
    """Retain rendered TSV lines for one replay, bounded by a fixed threshold.

    The export cannot emit anything until the whole-response verdict is known,
    and the verdict needs the last row — so the already-rendered bytes must
    either be retained or re-derived. Re-deriving means a second query, which
    then needs a transaction held open across a client-paced download to stay
    TOCTOU-free; under a non-WAL configuration that read lock blocks writers for
    as long as the client takes. Retaining is the cheaper hazard, provided the
    retention is bounded — which is what this is.

    Overflow goes to :func:`tempfile.TemporaryFile`, which POSIX creates 0600
    and unlinks immediately, so the spill has no reachable path and disappears
    when the handle closes. It is placed inside the configured data directory
    rather than a shared temp dir: the same findings already live at rest two
    files away in ``sample_<id>.db``, so this keeps the spill inside the same
    trust boundary instead of widening it to ``/tmp``.

    Lines are length-framed on disk rather than newline-delimited, so replay
    reproduces the *exact* chunk boundaries that were validated even when a
    cell value contains a newline or carriage return. Splitting on newlines
    would still emit identical bytes, but it would silently re-chunk such a
    row, and ``test_rare_tsv_preflights_then_emits_exact_row_chunks`` exists to
    pin chunk shape.
    """

    def __init__(self) -> None:
        self._max_chars = _RARE_VARIANT_TSV_SPOOL_MAX_CHARS
        self._lines: list[str] = []
        self._chars = 0
        self._file: IO[str] | None = None

    def append(self, line: str) -> None:
        """Retain one already-rendered data line."""
        if self._file is None and self._chars + len(line) <= self._max_chars:
            self._lines.append(line)
            self._chars += len(line)
            return
        spill = self._file or self._overflow()
        spill.write(f"{len(line)}\n{line}")

    def _overflow(self) -> IO[str]:
        """Move everything buffered so far onto disk and return the handle."""
        spill = tempfile.TemporaryFile(
            mode="w+",
            encoding="utf-8",
            newline="",
            dir=get_registry().settings.data_dir,
        )
        for buffered in self._lines:
            spill.write(f"{len(buffered)}\n{buffered}")
        self._lines.clear()
        self._chars = 0
        self._file = spill
        return spill

    def replay(self) -> Iterator[str]:
        """Yield the retained lines once, in append order and chunk-for-chunk."""
        if self._file is None:
            yield from self._lines
            return
        self._file.flush()
        self._file.seek(0)
        while True:
            framing = self._file.readline()
            if not framing:
                return
            yield self._file.read(int(framing))

    def close(self) -> None:
        """Release the buffer and the spill file. Safe to call repeatedly."""
        self._lines.clear()
        self._chars = 0
        if self._file is not None:
            self._file.close()
            self._file = None


def _iter_rare_variant_tsv_data_lines(
    rows: Sequence[dict[str, Any]],
    columns: tuple[str, ...],
) -> Iterator[str]:
    """Yield canonical TSV lines after fail-closed scalar validation."""
    for row in rows:
        raw_values = [row[column] for column in columns]
        # The structure-aware gate deliberately keeps independent complete
        # records separate. Never flatten a malformed container afterward,
        # because its string form can reassemble a held identifier pair.
        if not all(
            value is None or isinstance(value, (str, int, float, bool)) for value in raw_values
        ):
            continue
        values = ["" if value is None else str(value) for value in raw_values]
        yield "\t".join(values) + "\n"


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


def _resolve_biological_sex_for_sample(sample_engine: sa.Engine, sample_id: int) -> str | None:
    """Resolve recorded-over-inferred biological sex for rare-variant labels."""
    registry = get_registry()
    resolved = resolve_biological_sex(
        recorded_sex=get_recorded_biological_sex(registry.reference_engine, sample_id),
        inferred_sex=infer_biological_sex(sample_engine),
    )
    return resolved.sex


async def _has_supplied_body(request: Request) -> bool:
    """Distinguish an absent request body from an explicit JSON ``null``."""
    return bool(await request.body())


def _request_to_filter(
    req: RareVariantFilterRequest, *, biological_sex: str | None = None
) -> RareVariantFilter:
    """Convert a Pydantic request model to the dataclass filter.

    ``carried_only=True`` is forced on: the interactive ``/search`` and ``/run``
    endpoints can both surface clinically significant variants, so — exactly like
    the automated ``run_all`` path — they must surface only variants the
    individual actually carries. A genotyping chip reports a call at every probe,
    so without this gate a hom-ref (non-carrier) or unscoreable call at a
    Pathogenic locus leaks in as a clinical finding (the genotype-agnostic defect
    class). NULL zygosity is also excluded by the gate.
    """
    return RareVariantFilter(
        gene_symbols=req.gene_symbols,
        af_threshold=req.af_threshold,
        consequences=req.consequences,
        clinvar_significance=req.clinvar_significance,
        include_novel=req.include_novel,
        zygosity=req.zygosity,
        carried_only=True,
        inferred_sex=biological_sex,
        biological_sex=biological_sex,
    )


def to_rare_variant_response(variant: RareVariantResult) -> RareVariantResponse:
    """Serialize a finder result for exploratory API responses."""
    return RareVariantResponse(
        rsid=variant.rsid,
        chrom=variant.chrom,
        pos=variant.pos,
        ref=variant.ref,
        alt=variant.alt,
        genotype=variant.genotype,
        zygosity=variant.zygosity,
        zygosity_label=variant.zygosity_label,
        gene_symbol=variant.gene_symbol,
        consequence=variant.consequence,
        hgvs_coding=variant.hgvs_coding,
        hgvs_protein=variant.hgvs_protein,
        gnomad_af_global=variant.gnomad_af_global,
        gnomad_af_afr=variant.gnomad_af_afr,
        gnomad_af_amr=variant.gnomad_af_amr,
        gnomad_af_asj=variant.gnomad_af_asj,
        gnomad_af_eas=variant.gnomad_af_eas,
        gnomad_af_eur=variant.gnomad_af_eur,
        gnomad_af_fin=variant.gnomad_af_fin,
        gnomad_af_sas=variant.gnomad_af_sas,
        gnomad_source_status=variant.gnomad_source_status,
        is_novel=variant.is_novel,
        clinvar_significance=variant.clinvar_significance,
        clinvar_low_penetrance_or_risk_allele=(variant.is_clinvar_low_penetrance_or_risk_allele),
        clinvar_review_stars=variant.clinvar_review_stars,
        clinvar_accession=variant.clinvar_accession,
        clinvar_conditions=variant.clinvar_conditions,
        cadd_phred=variant.cadd_phred,
        revel=variant.revel,
        deleterious_count=variant.deleterious_count,
        deleterious_total_assessed=variant.deleterious_total_assessed,
        ensemble_pathogenic=variant.ensemble_pathogenic,
        evidence_conflict=variant.evidence_conflict,
        evidence_level=variant.evidence_level,
        disease_name=variant.disease_name,
        inheritance_pattern=variant.inheritance_pattern,
    )


def _parse_detail_json(row_id: int | None, detail_json: str | None) -> dict[str, Any]:
    """Parse detail_json from a findings row, returning empty dict on failure."""
    if not detail_json:
        return {}
    try:
        return json.loads(detail_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse detail_json for finding id=%s", row_id)
        return {}


# ── Endpoints ────────────────────────────────────────────────────────


@router.post("/search")
def search_rare_variants(
    body: RareVariantFilterRequest,
    sample_id: int = Query(..., description="Sample ID"),
) -> RareVariantSearchResponse:
    """Search for rare variants with the given filter parameters.

    Accepts filter criteria (gene panel, AF threshold, consequence types,
    ClinVar significance) and returns matching variants sorted by clinical
    relevance. Searches are exploratory and never replace stored findings.

    Example: ``POST /api/analysis/rare-variants/search?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)
    biological_sex = _resolve_biological_sex_for_sample(sample_engine, sample_id)
    filters = _request_to_filter(body, biological_sex=biological_sex)
    result = find_rare_variants(filters, sample_engine)

    items = [to_rare_variant_response(variant) for variant in result.variants]

    return RareVariantSearchResponse(
        items=items,
        total=result.count,
        total_variants_scanned=result.total_variants_scanned,
        novel_count=result.novel_count,
        pathogenic_count=result.pathogenic_count,
        genes_with_findings=result.genes_with_findings,
        filters_applied=body,
    )


@router.get("/findings")
def list_rare_variant_findings(
    sample_id: int = Query(..., description="Sample ID"),
    limit: int | None = Query(
        None,
        ge=1,
        le=5000,
        description="Max findings to return (pagination). Omit for all (legacy).",
    ),
    offset: int = Query(0, ge=0, description="Pagination offset (used with limit)."),
) -> RareVariantFindingsListResponse:
    """List stored rare variant findings for a sample.

    Returns findings previously generated by an unfiltered full run or the
    automated analysis pipeline,
    sorted by evidence level (highest first). Callers rendering rows should pass
    ``limit`` to avoid fetching and mounting tens of thousands of findings.

    Example: ``GET /api/analysis/rare-variants/findings?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)

    with sample_engine.connect() as conn:
        where_clause = findings.c.module == "rare_variants"
        query = (
            sa.select(findings)
            .where(where_clause)
            .order_by(findings.c.evidence_level.desc(), findings.c.gene_symbol, findings.c.id)
        )
        # Apply pagination only after the full payload gate. A scalar-safe
        # legacy row can otherwise consume a raw SQL page and leak its
        # existence through the response total or leave a later presentable row
        # unreachable. Stream all rows to preserve the safe total without
        # materializing a large rare-variant table for a bounded page.
        rows: list[sa.Row] = []
        total = 0
        page_end = offset + limit if limit is not None else None
        for row in conn.execute(query):
            if not is_patient_presentable_finding_payload(row._mapping):
                continue
            if page_end is None or offset <= total < page_end:
                rows.append(row)
            total += 1

    items: list[RareVariantFindingResponse] = []
    for row in rows:
        detail = _parse_detail_json(row.id, row.detail_json)
        zygosity_label = detail.get("zygosity_label")
        items.append(
            RareVariantFindingResponse(
                rsid=row.rsid,
                gene_symbol=row.gene_symbol,
                category=row.category,
                evidence_level=row.evidence_level or 1,
                finding_text=row.finding_text or "",
                zygosity=row.zygosity,
                zygosity_label=zygosity_label if isinstance(zygosity_label, str) else None,
                clinvar_significance=row.clinvar_significance,
                clinvar_low_penetrance_or_risk_allele=bool(
                    detail.get("clinvar_low_penetrance_or_risk_allele", False)
                ),
                conditions=row.conditions,
                detail=detail,
            )
        )

    response = RareVariantFindingsListResponse(items=items, total=total)
    if not is_patient_presentable_response_payload(
        [_payload_gate_projection(item.model_dump(mode="json")) for item in items]
    ):
        return RareVariantFindingsListResponse(items=[], total=0)
    return response


@router.post("/run")
def run_rare_variant_finder(
    sample_id: int = Query(..., description="Sample ID"),
    body: RareVariantFilterRequest | None = None,
    has_supplied_body: bool = Depends(_has_supplied_body),
) -> RareVariantRunResponse:
    """Run the rare variant finder with optional filters.

    With no body, uses default filters (AF < 1%, include novel) and replaces the
    canonical stored findings. Any supplied JSON body, including ``{}`` and
    ``null``, is an exploratory run and leaves those findings unchanged.

    An exploratory run returns its matches in ``items``, as ``/search`` does,
    because nothing is persisted for ``/findings`` to serve. A canonical run
    leaves ``items`` empty and its matches are read back from ``/findings``.

    Example: ``POST /api/analysis/rare-variants/run?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)
    biological_sex = _resolve_biological_sex_for_sample(sample_engine, sample_id)
    # No body → default filter, but still carriage-gate (this path stores findings).
    filters = (
        _request_to_filter(body, biological_sex=biological_sex)
        if body is not None
        else RareVariantFilter(
            carried_only=True,
            inferred_sex=biological_sex,
            biological_sex=biological_sex,
        )
    )
    result = find_rare_variants(filters, sample_engine)
    # FastAPI parses both an absent body and JSON ``null`` as ``None``. The async
    # dependency consults cached raw bytes so this synchronous handler can leave
    # blocking database work in FastAPI's threadpool. Only a body-less POST can
    # replace canonical findings; JSON ``null`` fails closed as exploratory (#2060).
    is_canonical_run = body is None and not has_supplied_body
    stored = store_rare_variant_findings(result, sample_engine) if is_canonical_run else 0

    return RareVariantRunResponse(
        variants_found=result.count,
        findings_stored=stored,
        total_variants_scanned=result.total_variants_scanned,
        novel_count=result.novel_count,
        pathogenic_count=result.pathogenic_count,
        genes_with_findings=result.genes_with_findings,
        # An exploratory run persists nothing, so these transient matches are the
        # only way its caller can inspect the result (#2060 review round 2).
        items=[] if is_canonical_run else [to_rare_variant_response(v) for v in result.variants],
    )


@router.get("/export/tsv")
def export_rare_variants_tsv(
    sample_id: int = Query(..., description="Sample ID"),
) -> StreamingResponse:
    """Export stored rare variant findings as a TSV file.

    Returns a downloadable tab-separated values file containing all
    rare variant findings for the given sample.

    Example: ``GET /api/analysis/rare-variants/export/tsv?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)

    tsv_columns = (
        "rsid",
        "gene_symbol",
        "category",
        "evidence_level",
        "zygosity",
        "clinvar_significance",
        "conditions",
        "consequence",
        "gnomad_af_global",
        "cadd_phred",
        "revel",
        "finding_text",
    )
    header = "\t".join(tsv_columns) + "\n"
    response_headers = {
        "Content-Disposition": f"attachment; filename=rare_variants_sample_{sample_id}.tsv"
    }

    # Both response-wide gates fold incrementally, so nothing proportional to the
    # result count is retained to reach a verdict: the structured gate keeps a
    # constant number of booleans per held pair, and the rendered-text gate has
    # always been a chunk scanner with fixed-width state. Only the rendered bytes
    # are retained, in a spool that is bounded by a documented threshold.
    payload_scanner = ResponsePayloadHeldPairScanner()
    spool = _TsvReplaySpool()

    def _render_and_retain() -> Iterator[str]:
        """Stream every safe data line once, feeding both gates and the spool."""
        with sample_engine.connect() as conn:
            result = conn.execute(
                sa.select(findings)
                .where(findings.c.module == "rare_variants")
                # `id` breaks ties so the order is total. It matters here
                # because the spool replays what was validated: a partial order
                # would leave the emitted body dependent on SQLite's row
                # ordering rather than on this clause. `/findings` already
                # orders this way.
                .order_by(
                    findings.c.evidence_level.desc(),
                    findings.c.gene_symbol,
                    findings.c.id,
                )
            )
            while True:
                batch = result.fetchmany(_RARE_VARIANT_EXPORT_BATCH)
                if not batch:
                    return
                for row in batch:
                    if not is_patient_presentable_finding_payload(row._mapping):
                        continue
                    detail = _parse_detail_json(row.id, row.detail_json)
                    export_row = {
                        "rsid": row.rsid or "",
                        "gene_symbol": row.gene_symbol or "",
                        "category": row.category or "",
                        "evidence_level": row.evidence_level or 1,
                        "zygosity": row.zygosity or "",
                        "clinvar_significance": row.clinvar_significance or "",
                        "conditions": row.conditions or "",
                        "consequence": detail.get("consequence", ""),
                        "gnomad_af_global": detail.get("af_global"),
                        "cadd_phred": detail.get("cadd_phred"),
                        "revel": detail.get("revel"),
                        "finding_text": row.finding_text or "",
                    }
                    # Rows have already passed their own stored-payload gate.
                    # Fold the decoded export DTO into the response-wide gate so
                    # safe fragments cannot reassemble a held prescribing pair
                    # across separate source records.
                    payload_scanner.feed(_payload_gate_projection(export_row))
                    # A malformed container is dropped here, exactly as before,
                    # and is therefore absent from the spool and from the
                    # rendered scan alike.
                    for line in _iter_rare_variant_tsv_data_lines((export_row,), tsv_columns):
                        spool.append(line)
                        yield line

    # The structured gate preserves record boundaries, but TSV separators are
    # part of the patient-visible text, so the exact rendered lines are scanned
    # too — tabs and newlines must not join safe fragments into a held
    # identifier, including across omitted malformed rows. The fixed header is
    # application metadata and stays outside both gates.
    #
    # This runs to completion inside the handler, before any response object
    # exists, so no patient-derived chunk can be emitted ahead of the verdict.
    try:
        rendered_safe = is_patient_presentable_rendered_text_chunks(_render_and_retain())
        safe = rendered_safe and payload_scanner.is_presentable()
    except BaseException:
        spool.close()
        raise

    if not safe:
        spool.close()
        return StreamingResponse(
            iter((header,)),
            media_type="text/tab-separated-values",
            headers=response_headers,
        )

    def _emit() -> Iterator[str]:
        # Replays the exact strings the gates saw — not a re-query and not a
        # re-render — so validation and emission observe the same immutable
        # data by construction and no TOCTOU gap exists. The `finally` covers
        # client cancellation, which Starlette surfaces by closing the
        # generator; `BackgroundTask` covers a response that is never iterated.
        try:
            yield from spool.replay()
        finally:
            spool.close()

    return StreamingResponse(
        chain((header,), _emit()),
        media_type="text/tab-separated-values",
        headers=response_headers,
        background=BackgroundTask(spool.close),
    )


@router.get("/export/vcf")
def export_rare_variants_vcf(
    sample_id: int = Query(..., description="Sample ID"),
) -> StreamingResponse:
    """Export stored rare variant findings as a minimal VCF 4.2 file.

    Each record is rebuilt from the finding's own stored locus/allele identity
    (``chrom``/``pos``/``ref``/``alt`` persisted in ``detail_json``), rather than
    re-resolving CHROM/POS/REF/ALT via an rsID join to ``annotated_variants``.
    A VCF 4.2 record is keyed on CHROM/POS/REF/ALT — ID is only an identifier
    ("No identifier should be present in more than one data record") — so the
    finding's own stored identity, not a re-lookup by the (potentially shared,
    aliased, or absent) rsID, is authoritative. Findings written before #1575
    lack the persisted coordinates; for those only, the export falls back to the
    annotated_variants row for the finding's rsid (unique — it is the PK). (#1575)

    Example: ``GET /api/analysis/rare-variants/export/vcf?sample_id=1``
    """
    sample_engine = _get_sample_engine(sample_id)

    with sample_engine.connect() as conn:
        finding_rows = conn.execute(
            sa.select(findings).where(findings.c.module == "rare_variants")
        ).fetchall()

    # Rebuild each record from the finding's own stored locus/allele identity,
    # then order by genomic coordinate (mirrors the previous ORDER BY chrom, pos;
    # records whose stored chrom/pos is missing sort last).
    records: list[dict[str, Any]] = []
    for row in finding_rows:
        if not is_patient_presentable_finding_payload(row._mapping):
            continue
        detail = _parse_detail_json(row.id, row.detail_json)
        records.append(
            {
                "chrom": detail.get("chrom"),
                "pos": detail.get("pos"),
                "rsid": row.rsid,
                "ref": detail.get("ref"),
                "alt": detail.get("alt"),
                "gene_symbol": row.gene_symbol,
                "consequence": detail.get("consequence"),
                "clinvar_significance": row.clinvar_significance,
                "gnomad_af_global": detail.get("af_global"),
                "evidence_level": row.evidence_level,
            }
        )
    # The VCF body is derived from these decoded records. Gate the complete
    # collection before legacy coordinate backfill or serialization so an
    # unsafe aggregate produces the established header-only export.
    if not is_patient_presentable_response_payload(
        [_payload_gate_projection(record) for record in records]
    ):
        records = []
    # Backward-compat backfill: findings stored before #1575 have no chrom/pos in
    # detail_json. For those (only), fall back to the annotated_variants row for
    # the finding's rsid so an already-analysed sample that has not been re-run
    # still exports real coordinates rather than placeholders. av.rsid is the
    # primary key, so this resolves to exactly one row; the finding's own stored
    # identity is always preferred whenever present.
    legacy = [r for r in records if r["chrom"] is None or r["pos"] is None]
    legacy_rsids = {r["rsid"] for r in legacy if r["rsid"]}
    if legacy_rsids:
        av = annotated_variants
        with sample_engine.connect() as conn:
            av_by_rsid = {
                row.rsid: row
                for row in conn.execute(
                    sa.select(av.c.rsid, av.c.chrom, av.c.pos, av.c.ref, av.c.alt).where(
                        av.c.rsid.in_(legacy_rsids)
                    )
                ).fetchall()
            }
        for r in legacy:
            av_row = av_by_rsid.get(r["rsid"])
            if av_row is None:
                continue
            for field in ("chrom", "pos", "ref", "alt"):
                if r[field] is None:
                    r[field] = getattr(av_row, field)

    records.sort(
        key=lambda r: (r["chrom"] is None, r["chrom"] or "", r["pos"] is None, r["pos"] or 0)
    )

    buf = io.StringIO()

    # VCF header
    today = date.today().isoformat()
    buf.write("##fileformat=VCFv4.2\n")
    buf.write(f"##fileDate={today}\n")
    buf.write("##source=Yeliztli-RareVariantFinder\n")
    buf.write("##reference=GRCh37\n")
    buf.write('##INFO=<ID=GENE,Number=1,Type=String,Description="Gene symbol">\n')
    buf.write('##INFO=<ID=CSQ,Number=1,Type=String,Description="Consequence type">\n')
    buf.write('##INFO=<ID=CLNSIG,Number=1,Type=String,Description="ClinVar significance">\n')
    buf.write('##INFO=<ID=AF,Number=1,Type=Float,Description="gnomAD global allele frequency">\n')
    buf.write('##INFO=<ID=EVLVL,Number=1,Type=Integer,Description="Evidence level (1-4 stars)">\n')
    buf.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")

    for row in records:
        chrom = row["chrom"] or "."
        pos = str(row["pos"]) if row["pos"] else "0"
        rsid = row["rsid"] or "."
        ref = row["ref"] or "."
        alt = row["alt"] or "."
        gene = row["gene_symbol"] or "."
        csq = row["consequence"] or "."
        clnsig = (row["clinvar_significance"] or ".").replace(" ", "_")
        af_val = row["gnomad_af_global"]
        af_str = f"{af_val:.6f}" if af_val is not None else "."
        evlvl = str(row["evidence_level"] or 1)

        info_parts = [
            f"GENE={gene}",
            f"CSQ={csq}",
            f"CLNSIG={clnsig}",
            f"AF={af_str}",
            f"EVLVL={evlvl}",
        ]
        info_str = ";".join(info_parts)

        buf.write(f"{chrom}\t{pos}\t{rsid}\t{ref}\t{alt}\t.\tPASS\t{info_str}\n")

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/plain",
        headers={
            "Content-Disposition": f"attachment; filename=rare_variants_sample_{sample_id}.vcf"
        },
    )
