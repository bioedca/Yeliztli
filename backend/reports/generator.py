"""PDF report generator via Playwright page.pdf() (P4-07).

Generates modular PDF reports from analysis findings stored in the
per-sample ``findings`` table.  Findings are grouped by module, sorted
by evidence level (highest first), and rendered through a Jinja2 HTML
template.  Pre-rendered SVGs (created at analysis time) are embedded
inline.  Playwright's headless Chromium converts the final HTML to PDF.

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

from backend.db.connection import get_registry
from backend.db.tables import findings, samples
from backend.reports.module_disclaimers import MODULE_DISCLAIMERS, MODULE_DISPLAY_NAMES

logger = structlog.get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

TEMPLATES_DIR = Path(__file__).parent / "templates"
VERSION = "0.1.0"

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
    """Query findings from sample DB, grouped by module and sorted by evidence."""
    clauses = []
    if modules:
        clauses.append(findings.c.module.in_(modules))

    stmt = sa.select(findings)
    if clauses:
        stmt = stmt.where(sa.and_(*clauses))

    stmt = stmt.order_by(
        sa.desc(sa.func.coalesce(findings.c.evidence_level, 0)),
        findings.c.module,
        findings.c.id,
    )

    with engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()

    result = []
    for row in rows:
        pmids_raw = _parse_json_field(row.pmid_citations)
        pmids = pmids_raw if isinstance(pmids_raw, list) else []

        result.append(
            {
                "id": row.id,
                "module": row.module,
                "category": row.category,
                "evidence_level": row.evidence_level,
                "gene_symbol": row.gene_symbol,
                "rsid": row.rsid,
                "finding_text": row.finding_text,
                "phenotype": row.phenotype,
                "conditions": row.conditions,
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
        )

    return result


def _read_svg_content(sample_dir: Path, svg_path: str | None) -> str | None:
    """Read an SVG file from disk and return its content for inline embedding.

    Returns None if the SVG path is empty or the file doesn't exist.
    """
    if not svg_path:
        return None
    svg_file = sample_dir / svg_path
    # Prevent path traversal attacks
    try:
        svg_file = svg_file.resolve()
        sample_dir_resolved = sample_dir.resolve()
        if not svg_file.is_relative_to(sample_dir_resolved):
            logger.warning("SVG path traversal attempt blocked: %s", svg_path)
            return None
    except (ValueError, OSError):
        return None
    if not svg_file.exists():
        logger.warning("SVG file not found: %s", svg_file)
        return None
    try:
        content = svg_file.read_text(encoding="utf-8")
        # Strip XML declaration for inline embedding
        if content.startswith("<?xml"):
            end_idx = content.find("?>")
            if end_idx != -1:
                content = content[end_idx + 2 :].lstrip()
        return content
    except Exception:
        logger.exception("Failed to read SVG: %s", svg_file)
        return None


def _group_findings_into_sections(
    finding_rows: list[dict[str, Any]],
    sample_dir: Path,
    modules: list[str] | None,
) -> list[dict[str, Any]]:
    """Group findings by module into ordered sections with embedded SVGs."""
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
        # Embed SVG content into each finding
        for f in mod_findings:
            f["svg_content"] = _read_svg_content(sample_dir, f.get("svg_path"))

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
    to PDF via ``page.pdf()``.  Pre-rendered SVGs are embedded inline in
    the HTML before conversion.

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
    html = render_report_html(sample_id, modules=modules, title=title)
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
