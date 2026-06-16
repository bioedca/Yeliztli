"""Single-variant evidence card generator (P4-09).

Generates a one-page PDF or PNG summary for a single variant (finding).
Reuses the Jinja2 + Playwright infrastructure from P4-07/P4-08.

Usage::

    from backend.reports.variant_card import (
        generate_variant_card_pdf,
        generate_variant_card_png,
        render_variant_card_html,
    )

    pdf_bytes = await generate_variant_card_pdf(sample_id=1, finding_id=42)
    png_bytes = await generate_variant_card_png(sample_id=1, finding_id=42)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
import structlog
from jinja2 import Environment, FileSystemLoader, select_autoescape

from backend.analysis.clinvar_conditions import format_clinvar_conditions_text
from backend.api.gating import gated_modules_to_hide
from backend.db.tables import findings
from backend.reports.generator import _get_sample_info, _read_svg_content
from backend.reports.module_disclaimers import MODULE_DISCLAIMERS, MODULE_DISPLAY_NAMES

logger = structlog.get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

TEMPLATES_DIR = Path(__file__).parent / "templates"
VERSION = "0.1.0"

# ── Jinja2 environment ────────────────────────────────────────────────

_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
    auto_reload=False,
)


# ── Data helpers ──────────────────────────────────────────────────────


def _load_single_finding(
    engine: sa.Engine,
    finding_id: int,
) -> dict[str, Any]:
    """Load a single finding by ID from the sample database.

    Raises ValueError if the finding does not exist.
    """
    stmt = sa.select(findings).where(findings.c.id == finding_id)

    with engine.connect() as conn:
        row = conn.execute(stmt).fetchone()

    if row is None:
        raise ValueError(f"Finding {finding_id} not found")

    # Disclosure gate (#963): the variant-card endpoints take a small, enumerable
    # finding_id, so without this check a gated module's shareable card (APOE
    # ε4/Alzheimer's, Parkinson's, sex-aneuploidy) leaks before its opt-in gate is
    # acknowledged — the un-hardened twin of the findings SVG-by-id gate
    # (findings.py). Raise the SAME not-found error as a missing id so all three
    # routes return 404: the no-leak posture must not confirm a gated finding
    # exists pre-acknowledgment.
    if row.module in gated_modules_to_hide(engine):
        raise ValueError(f"Finding {finding_id} not found")

    pmids_raw = row.pmid_citations
    pmids: list[str] = []
    if pmids_raw:
        try:
            parsed = json.loads(pmids_raw)
            if isinstance(parsed, list):
                pmids = [p for p in parsed if isinstance(p, str) and p.isdigit()]
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "id": row.id,
        "module": row.module,
        "category": row.category,
        "evidence_level": row.evidence_level,
        "gene_symbol": row.gene_symbol,
        "rsid": row.rsid,
        "finding_text": row.finding_text,
        "phenotype": row.phenotype,
        # Clean the raw CLNDN blob for display (#918): drop | separators, the
        # not provided/not specified placeholders, and drug-response entries.
        # Mirrors the frontend helper (#917); raw value stays in the DB.
        "conditions": format_clinvar_conditions_text(row.conditions),
        "zygosity": row.zygosity,
        "clinvar_significance": row.clinvar_significance,
        "diplotype": row.diplotype,
        "metabolizer_status": row.metabolizer_status,
        "drug": row.drug,
        "haplogroup": row.haplogroup,
        "prs_score": row.prs_score,
        "prs_percentile": row.prs_percentile,
        "pathway": row.pathway,
        "pathway_level": row.pathway_level,
        "svg_path": row.svg_path,
        "pmid_citations": pmids,
    }


# ── HTML rendering ────────────────────────────────────────────────────


def render_variant_card_html(
    sample_id: int,
    finding_id: int,
) -> str:
    """Render a single-variant evidence card as HTML.

    Parameters
    ----------
    sample_id:
        Numeric sample ID in the reference DB.
    finding_id:
        ID of the finding in the sample's ``findings`` table.

    Returns
    -------
    str
        Fully rendered HTML suitable for Playwright PDF/PNG conversion.
    """
    engine, sample_dir, sample_name = _get_sample_info(sample_id)
    finding = _load_single_finding(engine, finding_id)

    # Embed SVG content
    finding["svg_content"] = _read_svg_content(sample_dir, finding.get("svg_path"))

    # Module display name and disclaimer
    module = finding["module"]
    display_name = MODULE_DISPLAY_NAMES.get(module, module.replace("_", " ").title())
    disclaimer_info = MODULE_DISCLAIMERS.get(module)

    template = _jinja_env.get_template("variant_card.html")
    html = template.render(
        finding=finding,
        sample_name=sample_name,
        module_display_name=display_name,
        disclaimer_title=disclaimer_info["title"] if disclaimer_info else None,
        disclaimer_text=disclaimer_info["text"] if disclaimer_info else None,
        generated_at=datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC"),
        version=VERSION,
    )
    return html


# ── PDF generation ────────────────────────────────────────────────────


async def _html_to_pdf_single_page(html: str) -> bytes:
    """Convert HTML to a single-page PDF via Playwright."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required for PDF generation. "
            "Install it with: pip install playwright && playwright install chromium"
        ) from exc

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="networkidle", timeout=30000)
            await page.emulate_media(media="screen")
            pdf_bytes = await page.pdf(
                format="A4",
                print_background=True,
                margin={
                    "top": "15mm",
                    "bottom": "15mm",
                    "left": "15mm",
                    "right": "15mm",
                },
            )
            return pdf_bytes
        finally:
            await browser.close()


async def _html_to_png(html: str) -> bytes:
    """Convert HTML to PNG via Playwright screenshot."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required for PNG generation. "
            "Install it with: pip install playwright && playwright install chromium"
        ) from exc

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page(
                viewport={"width": 800, "height": 1200},
            )
            await page.set_content(html, wait_until="networkidle", timeout=30000)
            await page.emulate_media(media="screen")
            # Screenshot the full content
            png_bytes = await page.screenshot(
                full_page=True,
                type="png",
            )
            return png_bytes
        finally:
            await browser.close()


async def generate_variant_card_pdf(
    sample_id: int,
    finding_id: int,
) -> bytes:
    """Generate a single-variant evidence card as PDF.

    Parameters
    ----------
    sample_id:
        Numeric sample ID.
    finding_id:
        Finding row ID in the sample DB.

    Returns
    -------
    bytes
        PDF file content.

    Raises
    ------
    ValueError
        If sample or finding not found.
    RuntimeError
        If Playwright browsers are not installed.
    """
    html = render_variant_card_html(sample_id, finding_id)
    pdf_bytes = await _html_to_pdf_single_page(html)
    logger.info(
        "variant_card_pdf_generated",
        sample_id=sample_id,
        finding_id=finding_id,
        pdf_size_bytes=len(pdf_bytes),
    )
    return pdf_bytes


async def generate_variant_card_png(
    sample_id: int,
    finding_id: int,
) -> bytes:
    """Generate a single-variant evidence card as PNG.

    Parameters
    ----------
    sample_id:
        Numeric sample ID.
    finding_id:
        Finding row ID in the sample DB.

    Returns
    -------
    bytes
        PNG image content.

    Raises
    ------
    ValueError
        If sample or finding not found.
    RuntimeError
        If Playwright browsers are not installed.
    """
    html = render_variant_card_html(sample_id, finding_id)
    png_bytes = await _html_to_png(html)
    logger.info(
        "variant_card_png_generated",
        sample_id=sample_id,
        finding_id=finding_id,
        png_size_bytes=len(png_bytes),
    )
    return png_bytes
