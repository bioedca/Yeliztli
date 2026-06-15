"""SVG rendering functions for PDF report generation.

Generates standalone SVG files at analysis time and stores them alongside
findings in the sample directory (``{sample_dir}/svgs/``). The SVGs are
embedded in HTML reports rendered to PDF via Playwright ``page.pdf()``.

Pure Python SVG generation — no matplotlib, cairosvg, or other external
rendering dependencies.

Visual language:
  - Primary teal: #0D9488
  - Darker teal:  #0F766E
  - Lighter teal: #14B8A6
  - Background:   #F0FDFA (teal-50)
  - Gray track:   #E2E8F0 (slate-200)
  - Text:         #1E293B (slate-800)
  - Muted text:   #64748B (slate-500)

Target sizes:
  - Gauges (PRS):           400 x 300 px
  - Pathway indicators:     400 x 120 px
  - Metabolizer cards:      400 x 140 px
  - Admixture bars:         400 x 160 px
  - Evidence stars:         200 x 40  px
  - Carrier gene cards:     400 x 160 px
  - APOE genotype cards:    400 x 180 px

Usage::

    from backend.analysis.svg_renderer import (
        render_finding_svg,
        save_finding_svgs,
    )

    # Generate SVGs for all findings and persist to disk
    updated_findings = save_finding_svgs(finding_dicts, sample_dir)
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ── Color palette ─────────────────────────────────────────────────────────

TEAL_PRIMARY = "#0D9488"
TEAL_DARK = "#0F766E"
TEAL_LIGHT = "#14B8A6"
TEAL_BG = "#F0FDFA"
GRAY_TRACK = "#E2E8F0"
TEXT_PRIMARY = "#1E293B"
TEXT_MUTED = "#64748B"
WHITE = "#FFFFFF"
AMBER_STAR = "#F59E0B"
AMBER_STAR_EMPTY = "#E2E8F0"

# ── SVG XML boilerplate ──────────────────────────────────────────────────

SVG_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<svg xmlns="http://www.w3.org/2000/svg" '
    'width="{width}" height="{height}" '
    'viewBox="0 0 {width} {height}" '
    'font-family="-apple-system, BlinkMacSystemFont, '
    "'Segoe UI', Roboto, sans-serif\">\n"
)
SVG_FOOTER = "</svg>\n"

# ── Font style constants ─────────────────────────────────────────────────

FONT_TITLE = f'font-size="16" font-weight="600" fill="{TEXT_PRIMARY}"'
FONT_LABEL = f'font-size="12" font-weight="500" fill="{TEXT_MUTED}"'
FONT_VALUE = f'font-size="14" font-weight="600" fill="{TEXT_PRIMARY}"'
FONT_SMALL = f'font-size="11" fill="{TEXT_MUTED}"'
FONT_BIG = f'font-size="28" font-weight="700" fill="{TEXT_PRIMARY}"'


# ═══════════════════════════════════════════════════════════════════════════
# Dispatcher
# ═══════════════════════════════════════════════════════════════════════════


def render_finding_svg(finding: dict[str, Any]) -> str | None:
    """Route a finding dict to its module-specific SVG renderer.

    Returns the SVG string, or ``None`` if the finding type has no
    visual representation.

    Parameters
    ----------
    finding:
        A dict matching the ``findings`` table columns.  The ``module``
        and ``category`` fields drive dispatch.  Additional fields like
        ``prs_percentile``, ``pathway_level``, ``metabolizer_status``,
        ``evidence_level``, and ``detail_json`` supply rendering data.
    """
    module = finding.get("module", "")
    category = finding.get("category", "")

    # Parse detail_json once for renderers that need it
    detail = _parse_detail_json(finding.get("detail_json"))

    if module in ("cancer_prs", "traits_prs") or category == "prs":
        return _render_prs_gauge(finding, detail)

    if module == "nutrigenomics" or module == "methylation":
        return _render_pathway_indicator(finding, detail)

    if module == "pharmacogenomics":
        return _render_metabolizer_card(finding, detail)

    if module == "ancestry" and category == "admixture":
        return _render_admixture_bar(finding, detail)

    if module == "carrier_status":
        return _render_carrier_card(finding, detail)

    if module == "apoe":
        return _render_apoe_card(finding, detail)

    # Fallback: render evidence stars if the finding has an evidence level
    evidence_level = finding.get("evidence_level")
    if evidence_level is not None and 1 <= evidence_level <= 4:
        return _render_evidence_stars(evidence_level)

    return None


# ═══════════════════════════════════════════════════════════════════════════
# PRS gauge — semicircular arc with percentile, CI arc, and needle
# ═══════════════════════════════════════════════════════════════════════════


def _render_prs_gauge(
    finding: dict[str, Any],
    detail: dict[str, Any],
) -> str:
    """Render a semicircular gauge for PRS percentile with bootstrap CI.

    Gauge geometry: semicircle (180 degrees) centered at (200, 220),
    radius 150.  0th percentile = left (180 deg), 100th = right (0 deg).
    """
    width, height = 400, 300
    cx, cy = 200, 230
    radius = 140
    track_width = 20

    percentile = finding.get("prs_percentile", 50.0)
    percentile = max(0.0, min(100.0, float(percentile or 50.0)))

    z_score = detail.get("z_score") or finding.get("prs_score")
    ci_lower_raw = detail.get("bootstrap_ci_lower")
    ci_upper_raw = detail.get("bootstrap_ci_upper")
    ci_lower = percentile
    ci_upper = percentile
    has_ci = False
    if ci_lower_raw is not None and ci_upper_raw is not None:
        try:
            ci_lower = float(ci_lower_raw)
            ci_upper = float(ci_upper_raw)
        except (TypeError, ValueError):
            has_ci = False
        else:
            ci_lower = max(0.0, min(100.0, ci_lower))
            ci_upper = max(0.0, min(100.0, ci_upper))
            if ci_lower > ci_upper:
                ci_lower, ci_upper = ci_upper, ci_lower
            has_ci = ci_lower != ci_upper
    trait_name = finding.get("phenotype") or detail.get("trait_name", "Trait")

    parts: list[str] = []
    parts.append(SVG_HEADER.format(width=width, height=height))

    # Background card
    parts.append(
        f'  <rect width="{width}" height="{height}" rx="12" '
        f'fill="{WHITE}" stroke="{GRAY_TRACK}" stroke-width="1"/>\n'
    )

    # Title
    parts.append(
        f'  <text x="200" y="32" text-anchor="middle" {FONT_TITLE}>'
        f"{_escape(trait_name)} — Polygenic Risk</text>\n"
    )

    # Gray track (semicircle background)
    parts.append(
        _arc_path(
            cx,
            cy,
            radius,
            180.0,
            0.0,
            stroke=GRAY_TRACK,
            stroke_width=track_width,
        )
    )

    # CI shaded region (lighter teal, semi-transparent)
    if has_ci:
        ci_start_angle = _percentile_to_angle(ci_lower)
        ci_end_angle = _percentile_to_angle(ci_upper)
        parts.append(
            _arc_path(
                cx,
                cy,
                radius,
                ci_start_angle,
                ci_end_angle,
                stroke=TEAL_LIGHT,
                stroke_width=track_width,
                opacity=0.35,
            )
        )

    # Filled arc to percentile position (teal)
    pct_angle = _percentile_to_angle(percentile)
    parts.append(
        _arc_path(
            cx,
            cy,
            radius,
            180.0,
            pct_angle,
            stroke=TEAL_PRIMARY,
            stroke_width=track_width,
        )
    )

    # Needle at percentile position
    needle_angle_rad = math.radians(pct_angle)
    nx_inner = cx + (radius - track_width) * math.cos(needle_angle_rad)
    ny_inner = cy - (radius - track_width) * math.sin(needle_angle_rad)
    nx_outer = cx + (radius + track_width * 0.6) * math.cos(needle_angle_rad)
    ny_outer = cy - (radius + track_width * 0.6) * math.sin(needle_angle_rad)
    parts.append(
        f'  <line x1="{nx_inner:.1f}" y1="{ny_inner:.1f}" '
        f'x2="{nx_outer:.1f}" y2="{ny_outer:.1f}" '
        f'stroke="{TEAL_DARK}" stroke-width="3" stroke-linecap="round"/>\n'
    )

    # Center dot
    parts.append(f'  <circle cx="{cx}" cy="{cy}" r="5" fill="{TEAL_DARK}"/>\n')

    # Percentile text (large, centered below arc)
    parts.append(
        f'  <text x="{cx}" y="{cy - 30}" text-anchor="middle" {FONT_BIG}>{percentile:.0f}</text>\n'
    )
    parts.append(
        f'  <text x="{cx}" y="{cy - 10}" text-anchor="middle" {FONT_LABEL}>percentile</text>\n'
    )

    # Scale labels: 0 and 100
    parts.append(
        f'  <text x="{cx - radius - 5}" y="{cy + 20}" text-anchor="middle" {FONT_SMALL}>0</text>\n'
    )
    parts.append(
        f'  <text x="{cx + radius + 5}" y="{cy + 20}" '
        f'text-anchor="middle" {FONT_SMALL}>100</text>\n'
    )

    # Z-score and CI line
    footer_y = height - 30
    z_str = f"z = {float(z_score):.2f}" if z_score is not None else ""
    ci_str = f"95% CI: {ci_lower:.0f} – {ci_upper:.0f}" if has_ci else ""
    footer_parts = [s for s in (z_str, ci_str) if s]
    if footer_parts:
        parts.append(
            f'  <text x="{cx}" y="{footer_y}" text-anchor="middle" '
            f"{FONT_SMALL}>{_escape('  |  '.join(footer_parts))}</text>\n"
        )

    # Evidence stars in top-right corner
    ev = finding.get("evidence_level")
    if ev is not None and 1 <= ev <= 4:
        parts.append(_star_group(ev, x=340, y=12))

    parts.append(SVG_FOOTER)
    return "".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# Pathway level indicator — horizontal bar (Elevated / Moderate / Standard)
# ═══════════════════════════════════════════════════════════════════════════


def _render_pathway_indicator(
    finding: dict[str, Any],
    detail: dict[str, Any],
) -> str:
    """Horizontal tri-segment bar showing pathway risk level."""
    width, height = 400, 120
    pathway = finding.get("pathway") or detail.get("pathway", "Pathway")
    level = finding.get("pathway_level", "Standard")

    parts: list[str] = []
    parts.append(SVG_HEADER.format(width=width, height=height))

    # Background card
    parts.append(
        f'  <rect width="{width}" height="{height}" rx="12" '
        f'fill="{WHITE}" stroke="{GRAY_TRACK}" stroke-width="1"/>\n'
    )

    # Title
    parts.append(f'  <text x="20" y="30" {FONT_TITLE}>{_escape(pathway)}</text>\n')

    # Three-segment bar
    bar_x, bar_y = 20, 48
    bar_w, bar_h = 360, 28
    seg_w = bar_w / 3

    segments = [
        ("Standard", GRAY_TRACK, TEXT_MUTED),
        ("Moderate", TEAL_LIGHT, WHITE),
        ("Elevated", TEAL_PRIMARY, WHITE),
    ]

    for i, (seg_label, bg_color, txt_color) in enumerate(segments):
        sx = bar_x + i * seg_w
        is_active = seg_label == level

        # Rounded corners: first segment left-rounded, last right-rounded
        if i == 0:
            rx_str = f'rx="{bar_h // 2}"'
            # Use clip-path for left-only rounding
            parts.append(
                f'  <rect x="{sx}" y="{bar_y}" '
                f'width="{seg_w:.1f}" height="{bar_h}" '
                f'{rx_str} fill="{bg_color if is_active else GRAY_TRACK}" '
                f'opacity="{1.0 if is_active else 0.4}"/>\n'
            )
        elif i == 2:
            parts.append(
                f'  <rect x="{sx:.1f}" y="{bar_y}" '
                f'width="{seg_w:.1f}" height="{bar_h}" '
                f'rx="{bar_h // 2}" '
                f'fill="{bg_color if is_active else GRAY_TRACK}" '
                f'opacity="{1.0 if is_active else 0.4}"/>\n'
            )
        else:
            parts.append(
                f'  <rect x="{sx:.1f}" y="{bar_y}" '
                f'width="{seg_w:.1f}" height="{bar_h}" '
                f'fill="{bg_color if is_active else GRAY_TRACK}" '
                f'opacity="{1.0 if is_active else 0.4}"/>\n'
            )

        # Segment label
        label_color = txt_color if is_active else TEXT_MUTED
        parts.append(
            f'  <text x="{sx + seg_w / 2:.1f}" y="{bar_y + bar_h / 2 + 4:.1f}" '
            f'text-anchor="middle" font-size="11" font-weight="600" '
            f'fill="{label_color}">{seg_label}</text>\n'
        )

    # Active indicator arrow below the bar
    level_idx = {"Standard": 0, "Moderate": 1, "Elevated": 2}.get(level, 0)
    arrow_x = bar_x + level_idx * seg_w + seg_w / 2
    arrow_y = bar_y + bar_h + 8
    parts.append(
        f'  <polygon points="{arrow_x - 5:.1f},{arrow_y + 6:.1f} '
        f"{arrow_x + 5:.1f},{arrow_y + 6:.1f} "
        f'{arrow_x:.1f},{arrow_y:.1f}" fill="{TEAL_DARK}"/>\n'
    )

    # Evidence stars
    ev = finding.get("evidence_level")
    if ev is not None and 1 <= ev <= 4:
        parts.append(_star_group(ev, x=340, y=12))

    # Bottom note
    gene = finding.get("gene_symbol") or detail.get("gene", "")
    if gene:
        parts.append(
            f'  <text x="20" y="{height - 14}" {FONT_SMALL}>Gene: {_escape(gene)}</text>\n'
        )

    parts.append(SVG_FOOTER)
    return "".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# Metabolizer status card — pharmacogenomics phenotype display
# ═══════════════════════════════════════════════════════════════════════════


def _render_metabolizer_card(
    finding: dict[str, Any],
    detail: dict[str, Any],
) -> str:
    """Simple status card for pharmacogenomics metabolizer phenotype."""
    width, height = 400, 140

    gene = finding.get("gene_symbol") or detail.get("gene", "Gene")
    diplotype = finding.get("diplotype") or detail.get("diplotype", "")
    status = finding.get("metabolizer_status") or detail.get("phenotype", "Unknown")
    drug = finding.get("drug") or detail.get("drug", "")

    # Status color mapping
    status_lower = status.lower()
    if "poor" in status_lower or "ultra" in status_lower:
        status_bg = "#EF4444"  # red-500
        status_fg = WHITE
    elif "intermediate" in status_lower:
        status_bg = "#F59E0B"  # amber-500
        status_fg = WHITE
    elif "rapid" in status_lower:
        status_bg = TEAL_PRIMARY
        status_fg = WHITE
    else:
        status_bg = TEAL_LIGHT
        status_fg = WHITE

    parts: list[str] = []
    parts.append(SVG_HEADER.format(width=width, height=height))

    # Background card
    parts.append(
        f'  <rect width="{width}" height="{height}" rx="12" '
        f'fill="{WHITE}" stroke="{GRAY_TRACK}" stroke-width="1"/>\n'
    )

    # Gene name header
    parts.append(f'  <text x="20" y="32" {FONT_TITLE}>{_escape(gene)}</text>\n')

    # Diplotype
    if diplotype:
        parts.append(
            f'  <text x="20" y="54" {FONT_LABEL}>Diplotype: {_escape(diplotype)}</text>\n'
        )

    # Status badge (rounded pill)
    badge_x, badge_y = 20, 68
    badge_w = min(len(status) * 9 + 24, 360)
    badge_h = 30
    parts.append(
        f'  <rect x="{badge_x}" y="{badge_y}" '
        f'width="{badge_w}" height="{badge_h}" rx="15" '
        f'fill="{status_bg}"/>\n'
    )
    parts.append(
        f'  <text x="{badge_x + badge_w / 2}" y="{badge_y + badge_h / 2 + 5}" '
        f'text-anchor="middle" font-size="13" font-weight="600" '
        f'fill="{status_fg}">{_escape(status)}</text>\n'
    )

    # Drug name
    if drug:
        parts.append(
            f'  <text x="20" y="{height - 16}" {FONT_SMALL}>Drug: {_escape(drug)}</text>\n'
        )

    # Evidence stars
    ev = finding.get("evidence_level")
    if ev is not None and 1 <= ev <= 4:
        parts.append(_star_group(ev, x=340, y=12))

    parts.append(SVG_FOOTER)
    return "".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# Admixture bar — stacked horizontal bar for ancestry fractions
# ═══════════════════════════════════════════════════════════════════════════

# Population color palette (distinguishable, accessible)
_POPULATION_COLORS = [
    "#0D9488",  # teal (primary)
    "#7C3AED",  # violet-600
    "#F59E0B",  # amber-500
    "#EF4444",  # red-500
    "#3B82F6",  # blue-500
    "#EC4899",  # pink-500
    "#10B981",  # emerald-500
    "#8B5CF6",  # violet-500
    "#F97316",  # orange-500
    "#06B6D4",  # cyan-500
]


def _render_admixture_bar(
    finding: dict[str, Any],
    detail: dict[str, Any],
) -> str:
    """Stacked horizontal bar showing ancestry admixture fractions."""
    width, height = 400, 160

    # Extract fractions from detail_json (try both key names)
    fractions: dict[str, float] = detail.get("admixture_fractions", {})
    if not fractions:
        fractions = detail.get("fractions", {})
    if not fractions:
        fractions = {"Unknown": 1.0}

    # Sort by fraction descending
    sorted_pops = sorted(fractions.items(), key=lambda x: -x[1])

    parts: list[str] = []
    parts.append(SVG_HEADER.format(width=width, height=height))

    # Background card
    parts.append(
        f'  <rect width="{width}" height="{height}" rx="12" '
        f'fill="{WHITE}" stroke="{GRAY_TRACK}" stroke-width="1"/>\n'
    )

    # Title
    parts.append(f'  <text x="20" y="30" {FONT_TITLE}>Ancestry Composition</text>\n')

    # Stacked bar
    bar_x, bar_y = 20, 44
    bar_w, bar_h = 360, 28
    current_x = float(bar_x)

    # Clip path for rounded bar
    parts.append(
        f"  <defs>\n"
        f'    <clipPath id="bar-clip">\n'
        f'      <rect x="{bar_x}" y="{bar_y}" '
        f'width="{bar_w}" height="{bar_h}" rx="{bar_h // 2}"/>\n'
        f"    </clipPath>\n"
        f"  </defs>\n"
    )

    parts.append('  <g clip-path="url(#bar-clip)">\n')
    for i, (pop, frac) in enumerate(sorted_pops):
        seg_w = frac * bar_w
        color = _POPULATION_COLORS[i % len(_POPULATION_COLORS)]
        parts.append(
            f'    <rect x="{current_x:.1f}" y="{bar_y}" '
            f'width="{seg_w:.1f}" height="{bar_h}" fill="{color}"/>\n'
        )
        current_x += seg_w
    parts.append("  </g>\n")

    # Legend
    legend_y = bar_y + bar_h + 20
    col_width = 180
    for i, (pop, frac) in enumerate(sorted_pops):
        col = i % 2
        row = i // 2
        lx = 20 + col * col_width
        ly = legend_y + row * 20
        color = _POPULATION_COLORS[i % len(_POPULATION_COLORS)]

        parts.append(
            f'  <rect x="{lx}" y="{ly - 9}" width="12" height="12" rx="2" fill="{color}"/>\n'
        )
        parts.append(
            f'  <text x="{lx + 18}" y="{ly}" {FONT_SMALL}>'
            f"{_escape(pop)}: {frac * 100:.1f}%</text>\n"
        )

    parts.append(SVG_FOOTER)
    return "".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# Evidence stars — standalone star rating SVG
# ═══════════════════════════════════════════════════════════════════════════


def _render_evidence_stars(level: int) -> str:
    """Render a standalone 1-4 star evidence rating SVG."""
    width, height = 200, 40

    parts: list[str] = []
    parts.append(SVG_HEADER.format(width=width, height=height))

    # Background
    parts.append(
        f'  <rect width="{width}" height="{height}" rx="8" '
        f'fill="{WHITE}" stroke="{GRAY_TRACK}" stroke-width="1"/>\n'
    )

    # Label
    parts.append(f'  <text x="12" y="26" {FONT_SMALL}>Evidence</text>\n')

    # Stars
    parts.append(_star_group(level, x=80, y=10))

    parts.append(SVG_FOOTER)
    return "".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# Carrier status gene card
# ═══════════════════════════════════════════════════════════════════════════


def _render_carrier_card(
    finding: dict[str, Any],
    detail: dict[str, Any],
) -> str:
    """Gene card for carrier status (het P/LP finding)."""
    width, height = 400, 160

    gene = finding.get("gene_symbol") or detail.get("gene", "Gene")
    condition = finding.get("conditions") or detail.get("condition", "")
    zygosity = finding.get("zygosity") or "Heterozygous"
    significance = finding.get("clinvar_significance") or detail.get("significance", "")
    rsid = finding.get("rsid") or detail.get("rsid", "")

    parts: list[str] = []
    parts.append(SVG_HEADER.format(width=width, height=height))

    # Background card with left accent bar
    parts.append(
        f'  <rect width="{width}" height="{height}" rx="12" '
        f'fill="{WHITE}" stroke="{GRAY_TRACK}" stroke-width="1"/>\n'
    )
    parts.append(
        f'  <rect x="0" y="0" width="6" height="{height}" rx="3" fill="{TEAL_PRIMARY}"/>\n'
    )

    # Gene name (large)
    parts.append(
        f'  <text x="24" y="36" font-size="20" font-weight="700" '
        f'fill="{TEAL_DARK}">{_escape(gene)}</text>\n'
    )

    # Carrier badge
    parts.append(f'  <rect x="24" y="48" width="64" height="22" rx="11" fill="{TEAL_BG}"/>\n')
    parts.append(
        f'  <text x="56" y="63" text-anchor="middle" '
        f'font-size="10" font-weight="600" fill="{TEAL_PRIMARY}">'
        f"Carrier</text>\n"
    )

    # Condition
    if condition:
        parts.append(f'  <text x="24" y="92" {FONT_VALUE}>{_escape(condition)}</text>\n')

    # Details line: zygosity, significance, rsid
    detail_parts = [s for s in (zygosity, significance, rsid) if s]
    if detail_parts:
        parts.append(
            f'  <text x="24" y="112" {FONT_SMALL}>{_escape("  ·  ".join(detail_parts))}</text>\n'
        )

    # Reproductive framing note
    parts.append(
        f'  <text x="24" y="{height - 16}" font-size="10" '
        f'fill="{TEXT_MUTED}" font-style="italic">'
        f"Relevant for reproductive planning</text>\n"
    )

    # Evidence stars
    ev = finding.get("evidence_level")
    if ev is not None and 1 <= ev <= 4:
        parts.append(_star_group(ev, x=340, y=12))

    parts.append(SVG_FOOTER)
    return "".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# APOE genotype card
# ═══════════════════════════════════════════════════════════════════════════


def _render_apoe_card(
    finding: dict[str, Any],
    detail: dict[str, Any],
) -> str:
    """Diplotype display card for APOE genotype."""
    width, height = 400, 180

    diplotype = finding.get("diplotype") or detail.get("diplotype", "")
    phenotype = finding.get("phenotype") or detail.get("phenotype", "")
    category = finding.get("category") or ""

    # Determine risk color from diplotype
    has_e4 = "4" in str(diplotype)
    e4_count = detail.get("e4_count", 0)

    if e4_count >= 2:
        risk_color = "#EF4444"  # red
        risk_label = "Increased Risk"
    elif has_e4:
        risk_color = "#F59E0B"  # amber
        risk_label = "Moderately Increased Risk"
    elif "2" in str(diplotype) and "4" not in str(diplotype):
        risk_color = TEAL_PRIMARY
        risk_label = "Potentially Protective"
    else:
        risk_color = TEXT_MUTED
        risk_label = "Average Risk"

    parts: list[str] = []
    parts.append(SVG_HEADER.format(width=width, height=height))

    # Background card
    parts.append(
        f'  <rect width="{width}" height="{height}" rx="12" '
        f'fill="{WHITE}" stroke="{GRAY_TRACK}" stroke-width="1"/>\n'
    )

    # Title
    parts.append(f'  <text x="20" y="32" {FONT_TITLE}>APOE Genotype</text>\n')

    # Diplotype display (large, centered)
    display_text = _escape(diplotype) if diplotype else "N/A"
    parts.append(
        f'  <text x="200" y="82" text-anchor="middle" '
        f'font-size="36" font-weight="700" fill="{TEAL_DARK}" '
        f'letter-spacing="2">{display_text}</text>\n'
    )

    # Risk badge
    badge_y = 100
    badge_label = risk_label
    badge_w = min(len(badge_label) * 8 + 24, 260)
    parts.append(
        f'  <rect x="{200 - badge_w / 2:.0f}" y="{badge_y}" '
        f'width="{badge_w}" height="26" rx="13" '
        f'fill="{risk_color}" opacity="0.15"/>\n'
    )
    parts.append(
        f'  <text x="200" y="{badge_y + 18}" text-anchor="middle" '
        f'font-size="12" font-weight="600" '
        f'fill="{risk_color}">{_escape(badge_label)}</text>\n'
    )

    # Phenotype / category context
    context = phenotype or category
    if context:
        parts.append(
            f'  <text x="200" y="150" text-anchor="middle" {FONT_SMALL}>'
            f"{_escape(context)}</text>\n"
        )

    # Evidence stars
    ev = finding.get("evidence_level")
    if ev is not None and 1 <= ev <= 4:
        parts.append(_star_group(ev, x=340, y=12))

    parts.append(SVG_FOOTER)
    return "".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# Bulk save function
# ═══════════════════════════════════════════════════════════════════════════


def generate_svgs_for_sample(
    sample_engine: Any,
    sample_dir: str | Path,
) -> int:
    """Post-analysis SVG generation: read all findings, render SVGs, update DB.

    Queries all findings from the sample database, generates SVGs for each
    finding that has a visual representation, writes them to
    ``{sample_dir}/svgs/``, and updates the ``svg_path`` column in the DB.

    Parameters
    ----------
    sample_engine:
        SQLAlchemy engine for the sample database.
    sample_dir:
        Path to the sample directory (parent of the ``.db`` file).

    Returns
    -------
    Number of SVGs generated.
    """
    import sqlalchemy as sa

    from backend.db.tables import findings

    # 1. Read all findings from the sample DB
    with sample_engine.connect() as conn:
        rows = conn.execute(sa.select(findings)).fetchall()

    if not rows:
        logger.info("svg_generation_skipped", reason="no_findings")
        return 0

    # 2. Convert rows to dicts
    column_names = [c.key for c in findings.columns]
    finding_dicts = [dict(zip(column_names, row)) for row in rows]

    # 3. Generate SVGs and persist to disk
    updated = save_finding_svgs(finding_dicts, sample_dir)

    # 4. Update svg_path in the DB for findings that got an SVG
    updates = [{"_fid": f["id"], "_svg": f["svg_path"]} for f in updated if f.get("svg_path")]
    if updates:
        with sample_engine.begin() as conn:
            for upd in updates:
                conn.execute(
                    findings.update()
                    .where(findings.c.id == upd["_fid"])
                    .values(svg_path=upd["_svg"])
                )

    generated = len(updates)
    logger.info(
        "generate_svgs_for_sample_complete",
        total_findings=len(finding_dicts),
        svgs_generated=generated,
        sample_dir=str(sample_dir),
    )
    return generated


def save_finding_svgs(
    finding_dicts: list[dict[str, Any]],
    sample_dir: str | Path,
) -> list[dict[str, Any]]:
    """Generate SVGs for findings and persist to disk.

    For each finding dict, attempts to render an SVG.  If successful,
    writes the SVG file to ``{sample_dir}/svgs/{finding_id}.svg`` and
    sets the ``svg_path`` key on the finding dict.

    Parameters
    ----------
    finding_dicts:
        List of dicts matching the ``findings`` table columns.
        Each must have an ``id`` key (integer).
    sample_dir:
        Path to the sample directory (e.g., ``data/samples/sample_001``).

    Returns
    -------
    The same list of finding dicts, with ``svg_path`` populated for
    those that have a visual representation.
    """
    sample_dir = Path(sample_dir)
    svg_dir = sample_dir / "svgs"
    svg_dir.mkdir(parents=True, exist_ok=True)

    generated = 0
    for finding in finding_dicts:
        finding_id = finding.get("id")
        if finding_id is None:
            logger.warning("finding_missing_id", finding=finding)
            continue

        svg_content = render_finding_svg(finding)
        if svg_content is None:
            continue

        svg_filename = f"{finding_id}.svg"
        svg_path = svg_dir / svg_filename
        svg_path.write_text(svg_content, encoding="utf-8")

        # Store relative path from sample_dir for portability
        finding["svg_path"] = f"svgs/{svg_filename}"
        generated += 1

    logger.info(
        "svg_generation_complete",
        total_findings=len(finding_dicts),
        svgs_generated=generated,
        svg_dir=str(svg_dir),
    )
    return finding_dicts


# ═══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════


def _parse_detail_json(raw: str | None) -> dict[str, Any]:
    """Safely parse the detail_json column."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _escape(text: str) -> str:
    """Escape text for safe SVG/XML embedding."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _percentile_to_angle(percentile: float) -> float:
    """Convert a 0-100 percentile to an angle on the semicircular gauge.

    0th percentile = 180 degrees (left side).
    100th percentile = 0 degrees (right side).
    Angles are in degrees, measured counter-clockwise from 3 o'clock
    (standard SVG/math convention).
    """
    return 180.0 - (percentile / 100.0) * 180.0


def _arc_path(
    cx: float,
    cy: float,
    radius: float,
    start_angle_deg: float,
    end_angle_deg: float,
    *,
    stroke: str,
    stroke_width: float,
    opacity: float = 1.0,
) -> str:
    """Generate an SVG ``<path>`` element for a circular arc.

    Uses the standard SVG arc command. Angles are in degrees measured
    counter-clockwise from the positive X axis (3 o'clock position).
    The arc is drawn from ``start_angle_deg`` to ``end_angle_deg``.
    """
    start_rad = math.radians(start_angle_deg)
    end_rad = math.radians(end_angle_deg)

    # SVG arc: start and end points
    x1 = cx + radius * math.cos(start_rad)
    y1 = cy - radius * math.sin(start_rad)
    x2 = cx + radius * math.cos(end_rad)
    y2 = cy - radius * math.sin(end_rad)

    # Determine sweep: we go from start_angle to end_angle.
    # For the semicircular gauge, start > end (180 -> smaller angle).
    angle_span = start_angle_deg - end_angle_deg
    large_arc = 1 if abs(angle_span) > 180 else 0
    # Sweep direction: clockwise in SVG (sweep-flag=1) for decreasing angle
    sweep = 1 if angle_span > 0 else 0

    opacity_attr = f' opacity="{opacity}"' if opacity < 1.0 else ""

    return (
        f'  <path d="M {x1:.1f} {y1:.1f} '
        f"A {radius:.1f} {radius:.1f} 0 {large_arc} {sweep} "
        f'{x2:.1f} {y2:.1f}" '
        f'fill="none" stroke="{stroke}" '
        f'stroke-width="{stroke_width}" '
        f'stroke-linecap="round"{opacity_attr}/>\n'
    )


def _star_group(level: int, *, x: float, y: float) -> str:
    """Render a group of 4 stars (filled/empty) at the given position.

    Each star is a 16x16 SVG polygon. Stars are spaced 18px apart.

    Parameters
    ----------
    level:
        Number of filled stars (1-4).
    x:
        X position of the rightmost edge of the star group.
    y:
        Y position of the top of the stars.
    """
    star_size = 14
    star_spacing = 17
    total_width = 4 * star_spacing
    start_x = x - total_width

    parts: list[str] = []
    for i in range(4):
        sx = start_x + i * star_spacing
        fill = AMBER_STAR if i < level else AMBER_STAR_EMPTY
        # 5-pointed star polygon centered in a 14x14 box
        # Points computed for a star with outer radius 7, inner radius 3
        points = _star_polygon_points(sx + star_size / 2, y + star_size / 2, 7, 3)
        parts.append(f'  <polygon points="{points}" fill="{fill}"/>\n')

    return "".join(parts)


def _star_polygon_points(
    cx: float,
    cy: float,
    outer_r: float,
    inner_r: float,
) -> str:
    """Compute SVG polygon points for a 5-pointed star.

    Parameters
    ----------
    cx, cy:
        Center of the star.
    outer_r:
        Radius to the outer tips.
    inner_r:
        Radius to the inner vertices.
    """
    points: list[str] = []
    for i in range(10):
        # Alternate outer and inner radii; start at top (-90 degrees)
        angle = math.radians(-90 + i * 36)
        r = outer_r if i % 2 == 0 else inner_r
        px = cx + r * math.cos(angle)
        py = cy + r * math.sin(angle)
        points.append(f"{px:.1f},{py:.1f}")
    return " ".join(points)
