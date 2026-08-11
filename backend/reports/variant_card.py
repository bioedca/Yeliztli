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
from starlette.concurrency import run_in_threadpool

from backend.analysis.clinvar_conditions import format_clinvar_conditions_text
from backend.analysis.cross_module_links import normalize_cross_module_row
from backend.analysis.pathway_coverage import pathway_level_display_label
from backend.analysis.pharmacogenomics import (
    is_patient_presentable_finding_payload,
    is_patient_presentable_response_payload,
    patient_visible_finding_clause,
)
from backend.analysis.roh import normalize_legacy_finding_text
from backend.analysis.svg_renderer import is_safe_svg_marker, render_finding_svg
from backend.api.gating import gated_modules_to_hide
from backend.db.tables import findings
from backend.reports.generator import _get_sample_info
from backend.reports.module_disclaimers import MODULE_DISCLAIMERS, MODULE_DISPLAY_NAMES
from backend.services.lai_production_coverage import policy_qualified_finding_clause
from backend.version import app_version

logger = structlog.get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

TEMPLATES_DIR = Path(__file__).parent / "templates"
# The version stamped into the footer of every artifact this module
# emits. Sourced, not written out: this literal said 0.1.0 while the app
# was 0.2.0, so exported documents misattributed themselves (#2025).
VERSION = app_version()

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
    stmt = sa.select(findings).where(
        findings.c.id == finding_id,
        policy_qualified_finding_clause(findings.c.category),
        patient_visible_finding_clause(findings.c),
    )

    with engine.connect() as conn:
        row = conn.execute(stmt).fetchone()

    if row is None or not is_patient_presentable_finding_payload(row._mapping):
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

    detail: dict[str, Any] = {}
    if row.detail_json:
        try:
            parsed_detail = json.loads(row.detail_json)
            if isinstance(parsed_detail, dict):
                detail = parsed_detail
        except (json.JSONDecodeError, TypeError):
            pass

    # A shareable card is a user-visible render path too, so a pre-gate ROH row
    # must not carry "typical result" onto one (#2177), and a cross-module row
    # must name the link the panel declares now. A card is exported by
    # finding_id, so a retired link stays reachable unless it is refused
    # outright — with the same not-found error the disclosure gate uses, so the
    # no-leak posture is unchanged (#2021).
    corrected_text = normalize_legacy_finding_text(
        row.module, row.category, row.finding_text, detail, engine
    )
    resolved = normalize_cross_module_row(
        row.module, row.category, row.rsid, corrected_text, detail
    )
    if resolved is None:
        raise ValueError(f"Finding {finding_id} not found")
    corrected_text, resolved_detail = resolved
    if isinstance(resolved_detail, dict):
        detail = resolved_detail

    return {
        "id": row.id,
        "module": row.module,
        "category": row.category,
        "evidence_level": row.evidence_level,
        "gene_symbol": row.gene_symbol,
        "rsid": row.rsid,
        "finding_text": corrected_text,
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
        # Coverage-aware label so an incomplete Standard pathway can't render a plain
        # green Standard badge in exported single-variant cards (#1651).
        "pathway_level_display": pathway_level_display_label(row.pathway_level, detail),
        "svg_path": row.svg_path,
        "pmid_citations": pmids,
        # This private source record is used only for fresh card generation;
        # it must not reach the HTML template or patient-facing evidence DTO.
        "_svg_render_input": dict(row._mapping),
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
    engine, _sample_dir, sample_name = _get_sample_info(sample_id)
    finding = _load_single_finding(engine, finding_id)

    # Never embed a persisted SVG artifact. It can have changed after the
    # source finding was evaluated, so regenerate it from the gated record.
    render_input = finding.pop("_svg_render_input", None)
    finding["svg_content"] = (
        render_finding_svg(render_input)
        if is_safe_svg_marker(finding.get("svg_path")) and isinstance(render_input, dict)
        else None
    )
    svg_content = finding.get("svg_content")
    card_evidence = {
        **{
            key: value
            for key, value in finding.items()
            if key not in {"svg_content", "svg_path", "_svg_render_input"} and value is not None
        },
        **({"svg_content": svg_content} if isinstance(svg_content, str) else {}),
    }
    if not is_patient_presentable_response_payload(card_evidence):
        finding["svg_content"] = None

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
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required for PDF generation. "
            "Install it with: pip install playwright && python -m playwright install chromium"
        ) from exc

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except PlaywrightError as exc:
            raise RuntimeError(
                "Playwright Chromium is required for PDF generation. "
                "Install it with: python -m playwright install chromium"
            ) from exc
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
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required for PNG generation. "
            "Install it with: pip install playwright && python -m playwright install chromium"
        ) from exc

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except PlaywrightError as exc:
            raise RuntimeError(
                "Playwright Chromium is required for PNG generation. "
                "Install it with: python -m playwright install chromium"
            ) from exc
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
    # Offloaded: this render performs the ROH coverage scan, and running it
    # inline would block the event loop before the first await below.
    html = await run_in_threadpool(render_variant_card_html, sample_id, finding_id)
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
    # Offloaded for the same reason as the PDF path: this render performs the
    # ROH coverage scan and would block the loop before the await below.
    html = await run_in_threadpool(render_variant_card_html, sample_id, finding_id)
    png_bytes = await _html_to_png(html)
    logger.info(
        "variant_card_png_generated",
        sample_id=sample_id,
        finding_id=finding_id,
        png_size_bytes=len(png_bytes),
    )
    return png_bytes
