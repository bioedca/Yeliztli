"""PDF report generator via Playwright page.pdf() (P4-07).

Generates modular PDF reports from analysis findings stored in the
per-sample ``findings`` table. Findings are grouped by module, sorted by
evidence level (highest first), and rendered through a Jinja2 HTML template.
Cards are generated freshly from the gated structured finding rather than from
persisted SVG artifacts. Playwright's headless Chromium converts the final HTML
to PDF.

Usage::

    from backend.reports.generator import generate_report_pdf

    pdf_bytes = await generate_report_pdf(
        sample_id=1,
        modules=["cancer", "pharmacogenomics", "nutrigenomics"],
    )
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
from backend.db.connection import get_registry
from backend.db.tables import findings, samples
from backend.reports.module_disclaimers import MODULE_DISCLAIMERS, MODULE_DISPLAY_NAMES
from backend.services.lai_production_coverage import policy_qualified_finding_clause

logger = structlog.get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

TEMPLATES_DIR = Path(__file__).parent / "templates"
VERSION = "0.1.0"
MAX_REPORT_FINDINGS = 1_000


class ReportTooLargeError(ValueError):
    """Raised before rendering when a report selection exceeds its safe bound."""


# Module display order (determines section order in report)
MODULE_ORDER = [
    "cancer",
    "cardiovascular",
    "apoe",
    "pharmacogenomics",
    "nutrigenomics",
    "metabolic",
    "fh",
    "ebmd",
    "carrier",
    "ancestry",
    "gene_health",
    "fitness",
    "sleep",
    "methylation",
    "skin",
    "allergy",
    "traits",
    "rare_variants",
]

# ── Jinja2 environment ────────────────────────────────────────────────

_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
    auto_reload=False,
)


# ── Data helpers ──────────────────────────────────────────────────────


def _get_sample_info(sample_id: int) -> tuple[sa.Engine, Path, str]:
    """Look up sample and return (engine, sample_dir, sample_name)."""
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path, samples.c.name).where(samples.c.id == sample_id)
        ).fetchone()
    if row is None:
        raise ValueError(f"Sample {sample_id} not found")
    sample_db_full = registry.settings.data_dir / row.db_path
    if not sample_db_full.exists():
        raise ValueError(f"Sample database file not found: {sample_db_full}")
    engine = registry.get_sample_engine(sample_db_full)
    return engine, sample_db_full.parent, row.name or f"Sample {sample_id}"


def _parse_json_field(raw: str | None) -> list[str] | dict | None:
    """Safely parse a JSON string field."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _load_findings(
    engine: sa.Engine,
    modules: list[str] | None,
) -> list[dict[str, Any]]:
    """Query reportable findings from sample DB, sorted by evidence."""
    clauses = [
        policy_qualified_finding_clause(findings.c.category),
        patient_visible_finding_clause(findings.c),
    ]
    if modules:
        clauses.append(findings.c.module.in_(modules))

    hidden_modules = gated_modules_to_hide(engine)
    if hidden_modules:
        clauses.append(findings.c.module.not_in(hidden_modules))

    where_clause = sa.and_(*clauses)
    bounded_selection = (
        sa.select(sa.literal(1).label("selected"))
        .where(where_clause)
        .limit(MAX_REPORT_FINDINGS + 1)
        .subquery()
    )

    stmt = (
        sa.select(findings)
        .where(where_clause)
        .order_by(
            sa.desc(sa.func.coalesce(findings.c.evidence_level, 0)),
            findings.c.module,
            findings.c.id,
        )
        .limit(MAX_REPORT_FINDINGS + 1)
    )

    with engine.connect() as conn:
        # Prove the selection is bounded before SQLite evaluates the report's
        # presentation ordering. Without this unordered preflight, an oversized
        # request can still sort every matching row before LIMIT returns 1,001.
        selection_count = conn.scalar(sa.select(sa.func.count()).select_from(bounded_selection))
        if int(selection_count or 0) > MAX_REPORT_FINDINGS:
            raise ReportTooLargeError(
                "Report selection exceeds the maximum of "
                f"{MAX_REPORT_FINDINGS:,} findings; select fewer modules."
            )

        # Keep LIMIT + 1 on the ordered load as a defense-in-depth recheck.
        rows = conn.execute(stmt).fetchall()

    if len(rows) > MAX_REPORT_FINDINGS:
        raise ReportTooLargeError(
            "Report selection exceeds the maximum of "
            f"{MAX_REPORT_FINDINGS:,} findings; select fewer modules."
        )

    result = []
    for row in rows:
        if not is_patient_presentable_finding_payload(row._mapping):
            continue
        pmids_raw = _parse_json_field(row.pmid_citations)
        pmids = pmids_raw if isinstance(pmids_raw, list) else []
        # Parsed once per row: two consumers below read the same column, and a
        # report loads up to MAX_REPORT_FINDINGS rows. Sharing one parse also
        # means the two cannot drift apart on what they read.
        detail_blob = _parse_json_field(row.detail_json)

        # A report is a durable export, so a retired cross-module link would
        # outlive every screen that has stopped showing it. Resolve the target
        # and note against the panel that is loaded, and drop a link the panel
        # no longer declares (#2021).
        resolved_text = normalize_legacy_finding_text(
            row.module, row.category, row.finding_text, detail_blob, engine
        )
        resolved = normalize_cross_module_row(
            row.module,
            row.category,
            row.rsid,
            resolved_text,
            detail_blob if isinstance(detail_blob, dict) else None,
        )
        if resolved is None:
            continue
        resolved_text, resolved_detail = resolved
        if isinstance(detail_blob, dict) and isinstance(resolved_detail, dict):
            detail_blob = resolved_detail

        result.append(
            {
                "id": row.id,
                "module": row.module,
                "category": row.category,
                "evidence_level": row.evidence_level,
                "gene_symbol": row.gene_symbol,
                "rsid": row.rsid,
                # A stored ROH narrative written before the evaluability gate
                # asserts a "typical" FROH ≈ 0 for a sample whose markers cannot
                # produce a segment (#2177); other modules are untouched.
                "finding_text": resolved_text,
                "phenotype": row.phenotype,
                # Clean the raw CLNDN blob for display (#918), mirroring the
                # frontend helper (#917); raw value stays in the DB. (The current
                # report_base.html does not render this row, but keep both report
                # finding-builders symmetric so it is correct if it ever does.)
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
                # Coverage-aware label so an incomplete Standard pathway can't render
                # a plain green Standard badge in exported reports (#1651).
                "pathway_level_display": pathway_level_display_label(
                    row.pathway_level, detail_blob
                ),
                "svg_path": row.svg_path,
                "pmid_citations": pmids,
                # Keep the complete, already-gated source record private until
                # the card is freshly rendered below. It is never sent to the
                # Jinja template or serialized in report evidence.
                "_svg_render_input": dict(row._mapping),
            }
        )

    # The report template renders all selected findings in one document. Keep
    # complete source-row boundaries, but withhold the dynamic report body if
    # separately safe legacy fragments would assemble a held pair on the page.
    evidence_rows = [
        {
            key: value
            for key, value in result_row.items()
            if key not in {"_svg_render_input", "svg_path"} and value is not None
        }
        for result_row in result
    ]
    return result if is_patient_presentable_response_payload(evidence_rows) else []


def _group_findings_into_sections(
    finding_rows: list[dict[str, Any]],
    _sample_dir: Path,
    modules: list[str] | None,
) -> list[dict[str, Any]]:
    """Group findings by module with freshly rendered SVG cards."""
    # Group by module
    by_module: dict[str, list[dict[str, Any]]] = {}
    for f in finding_rows:
        mod = f["module"]
        by_module.setdefault(mod, []).append(f)

    # Determine module order
    if modules:
        ordered_modules = [m for m in MODULE_ORDER if m in modules and m in by_module]
        # Add any modules not in the predefined order
        for m in modules:
            if m in by_module and m not in ordered_modules:
                ordered_modules.append(m)
    else:
        ordered_modules = [m for m in MODULE_ORDER if m in by_module]
        for m in by_module:
            if m not in ordered_modules:
                ordered_modules.append(m)

    sections = []
    for mod in ordered_modules:
        mod_findings = by_module[mod]
        # Stored SVG files are mutable artifacts and are never embedded in a
        # patient report. Preserve their presence as an availability marker,
        # then generate a fresh card from the already-gated source record.
        for f in mod_findings:
            render_input = f.pop("_svg_render_input", None)
            f["svg_content"] = (
                render_finding_svg(render_input)
                if is_safe_svg_marker(f.get("svg_path")) and isinstance(render_input, dict)
                else None
            )

        disclaimer_info = MODULE_DISCLAIMERS.get(mod)
        sections.append(
            {
                "module": mod,
                "display_name": MODULE_DISPLAY_NAMES.get(mod, mod.replace("_", " ").title()),
                "finding_count": len(mod_findings),
                "findings": mod_findings,
                "disclaimer_title": disclaimer_info["title"] if disclaimer_info else None,
                "disclaimer": disclaimer_info["text"] if disclaimer_info else None,
            }
        )

    # The report body was checked before rendering, and cards are generated
    # only from gated records. Recheck the complete dynamic document evidence
    # after card generation as defense in depth for cross-record assembly.
    report_evidence = []
    for section in sections:
        for finding in section["findings"]:
            svg_content = finding.get("svg_content")
            report_evidence.append(
                {
                    **{
                        key: value
                        for key, value in finding.items()
                        if key not in {"svg_content", "svg_path"} and value is not None
                    },
                    **({"svg_content": svg_content} if isinstance(svg_content, str) else {}),
                }
            )
    if not is_patient_presentable_response_payload(report_evidence):
        for section in sections:
            for finding in section["findings"]:
                finding["svg_content"] = None

    return sections


# ── HTML rendering ────────────────────────────────────────────────────


def render_report_html(
    sample_id: int,
    modules: list[str] | None = None,
    title: str = "Yeliztli Genomic Report",
) -> str:
    """Render the report HTML string (useful for preview / testing).

    Parameters
    ----------
    sample_id:
        Numeric ID of the sample in the reference DB.
    modules:
        List of module names to include.  ``None`` means all modules.
    title:
        Report title shown in the header.

    Returns
    -------
    str
        Fully rendered HTML suitable for Playwright PDF conversion.
    """
    engine, sample_dir, sample_name = _get_sample_info(sample_id)
    finding_rows = _load_findings(engine, modules)
    sections = _group_findings_into_sections(finding_rows, sample_dir, modules)

    total_findings = sum(s["finding_count"] for s in sections)
    high_evidence_count = sum(
        1 for s in sections for f in s["findings"] if (f.get("evidence_level") or 0) >= 3
    )

    template = _jinja_env.get_template("report_base.html")
    html = template.render(
        title=title,
        sample_name=sample_name,
        generated_at=datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC"),
        version=VERSION,
        sections=sections,
        total_findings=total_findings,
        high_evidence_count=high_evidence_count,
    )
    return html


# ── PDF generation ────────────────────────────────────────────────────


async def generate_report_pdf(
    sample_id: int,
    modules: list[str] | None = None,
    title: str = "Yeliztli Genomic Report",
) -> bytes:
    """Generate a PDF report for the given sample.

    Uses Playwright's headless Chromium to render the Jinja2 HTML template
    to PDF via ``page.pdf()``. Fresh SVG cards are embedded inline in the HTML
    before conversion.

    Parameters
    ----------
    sample_id:
        Numeric ID of the sample in the reference DB.
    modules:
        List of module names to include.  ``None`` means all modules.
    title:
        Report title shown in the header.

    Returns
    -------
    bytes
        PDF file content.

    Raises
    ------
    ValueError
        If the sample is not found.
    RuntimeError
        If Playwright browsers are not installed.
    """
    # Offloaded: this render performs the ROH coverage scan, and running it
    # inline would block the event loop before the first await below.
    html = await run_in_threadpool(render_report_html, sample_id, modules=modules, title=title)
    pdf_bytes = await _html_to_pdf(html)
    logger.info(
        "report_generated",
        sample_id=sample_id,
        modules=modules,
        pdf_size_bytes=len(pdf_bytes),
    )
    return pdf_bytes


async def _html_to_pdf(html: str) -> bytes:
    """Convert an HTML string to PDF bytes via Playwright.

    Uses the async Playwright API with headless Chromium.  Emulates
    screen media for full-colour rendering of backgrounds and SVGs.
    """
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
            await page.set_content(html, wait_until="networkidle")
            await page.emulate_media(media="screen")
            pdf_bytes = await page.pdf(
                format="A4",
                print_background=True,
                margin={
                    "top": "20mm",
                    "bottom": "25mm",
                    "left": "18mm",
                    "right": "18mm",
                },
            )
            return pdf_bytes
        finally:
            await browser.close()
