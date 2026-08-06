"""Report generation API endpoints (P4-07, P4-09).

POST /api/reports/generate       — Generate a PDF report for a sample
POST /api/reports/preview        — Generate HTML preview of a report
POST /api/reports/variant-card   — Generate single-variant evidence card (PDF)
POST /api/reports/variant-card/png — Generate single-variant evidence card (PNG)
POST /api/reports/variant-card/preview — HTML preview of variant evidence card
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field, field_validator

from backend.api.dependencies import require_fresh_sample, sample_export_guard

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["reports"])


# ── Request / response models ─────────────────────────────────────────


class ReportRequest(BaseModel):
    """Request body for report generation."""

    sample_id: int = Field(..., description="Sample ID to generate report for")
    modules: list[str] | None = Field(
        None,
        description="List of module names to include. None = all modules.",
    )
    title: str = Field(
        "Yeliztli Genomic Report",
        description="Report title",
    )

    @field_validator("modules")
    @classmethod
    def modules_non_empty(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and len(v) == 0:
            raise ValueError("modules list cannot be empty; use null for all modules")
        return v


class VariantCardRequest(BaseModel):
    """Request body for single-variant evidence card generation (P4-09)."""

    sample_id: int = Field(..., gt=0, description="Sample ID")
    finding_id: int = Field(..., gt=0, description="Finding ID in the sample's findings table")


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/generate")
async def generate_report(request: ReportRequest) -> Response:
    """Generate a PDF report for the given sample.

    Returns the PDF file as a downloadable response.
    """
    require_fresh_sample(request.sample_id)
    from backend.reports.generator import ReportTooLargeError, generate_report_pdf

    try:
        with sample_export_guard(
            request.sample_id,
            operation="PDF report generation",
        ):
            pdf_bytes = await generate_report_pdf(
                sample_id=request.sample_id,
                modules=request.modules,
                title=request.title,
            )
    except ReportTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    filename = f"yeliztli_report_{request.sample_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/preview", response_class=HTMLResponse)
async def preview_report(request: ReportRequest) -> HTMLResponse:
    """Generate an HTML preview of the report (no PDF conversion).

    Useful for the report builder UI to show a live preview before
    the user commits to PDF generation.
    """
    require_fresh_sample(request.sample_id)
    from backend.reports.generator import ReportTooLargeError, render_report_html

    try:
        with sample_export_guard(
            request.sample_id,
            operation="report preview generation",
        ):
            html = render_report_html(
                sample_id=request.sample_id,
                modules=request.modules,
                title=request.title,
            )
    except ReportTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return HTMLResponse(content=html)


# ── Variant evidence card endpoints (P4-09) ──────────────────────────


@router.post("/variant-card")
async def generate_variant_card(request: VariantCardRequest) -> Response:
    """Generate a single-variant evidence card as PDF.

    Returns a one-page PDF summarising a single finding with all
    clinical metadata, evidence stars, and embedded SVG visualisation.
    """
    require_fresh_sample(request.sample_id)
    from backend.reports.variant_card import generate_variant_card_pdf

    try:
        pdf_bytes = await generate_variant_card_pdf(
            sample_id=request.sample_id,
            finding_id=request.finding_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    filename = f"variant_card_{request.sample_id}_{request.finding_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/variant-card/png")
async def generate_variant_card_as_png(request: VariantCardRequest) -> Response:
    """Generate a single-variant evidence card as PNG image."""
    require_fresh_sample(request.sample_id)
    from backend.reports.variant_card import generate_variant_card_png

    try:
        png_bytes = await generate_variant_card_png(
            sample_id=request.sample_id,
            finding_id=request.finding_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    filename = f"variant_card_{request.sample_id}_{request.finding_id}.png"
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/variant-card/preview", response_class=HTMLResponse)
async def preview_variant_card(request: VariantCardRequest) -> HTMLResponse:
    """Generate an HTML preview of the variant evidence card."""
    require_fresh_sample(request.sample_id)
    from backend.reports.variant_card import render_variant_card_html

    try:
        html = render_variant_card_html(
            sample_id=request.sample_id,
            finding_id=request.finding_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return HTMLResponse(content=html)
