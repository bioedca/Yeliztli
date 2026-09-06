"""Cross-panel guard: no ``finding_text`` may name the rsID ``{genotype}`` already supplies.

The shared risk-genotype caller expands ``{genotype}`` to ``"<rsid> <call>"`` (``"; "``-joined
for multi-locus models), so a template written as ``(rs13129697 {genotype})`` renders
``(rs13129697 rs13129697 GT)``. That text is persisted in ``findings.finding_text`` and so
reaches every module page, All Findings, the Dashboard, and generated reports. Nineteen
templates across six disease panels did exactly that (issue #2051).

Two self-discovering guards over ``backend/data/panels/*.json``:

* the template guard walks every ``finding_text`` string at any depth and rejects a literal
  ``rs<digits>`` immediately before ``{genotype}``;
* the render guard pushes every genotype model of every risk-genotype panel through the
  production formatter and rejects an rsID repeated back-to-back in the emitted text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.analysis.risk_genotype import (
    PROBE_TYPED,
    ProbeReadout,
    _model_rsids,
    _render_finding,
    load_risk_panel,
)

PANEL_DIR = Path(__file__).resolve().parents[2] / "backend" / "data" / "panels"
PANEL_PATHS = sorted(PANEL_DIR.glob("*.json"))
RISK_PANEL_PATHS = [
    path
    for path in PANEL_PATHS
    if json.loads(path.read_text(encoding="utf-8")).get("genotype_models")
]
# The six panels the issue enumerated; the guards must be proven to look at them.
ISSUE_PANELS = (
    "amd_panel.json",
    "gout_panel.json",
    "lhon_panel.json",
    "mt_rnr1_panel.json",
    "parkinsons_panel.json",
    "thrombophilia_panel.json",
)

# A literal rsID directly before the placeholder that already carries the rsID.
LITERAL_RSID_BEFORE_GENOTYPE = re.compile(r"\brs\d+\s*\{genotype\}")
# The user-visible symptom: one rsID printed twice in a row.
ADJACENT_REPEATED_RSID = re.compile(r"\b(rs\d+) \1\b")


def _finding_text_templates(node: object) -> list[str]:
    """Every ``finding_text`` string value at any depth of a panel document."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "finding_text" and isinstance(value, str):
                found.append(value)
            found.extend(_finding_text_templates(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_finding_text_templates(item))
    return found


def _panel_id(path: Path) -> str:
    return path.name


def test_guards_cover_the_panels_named_in_the_issue() -> None:
    """The self-discovery must find templates in each of the six affected panels."""
    templates_by_panel = {
        path.name: _finding_text_templates(json.loads(path.read_text(encoding="utf-8")))
        for path in PANEL_PATHS
    }
    assert templates_by_panel
    for name in ISSUE_PANELS:
        assert templates_by_panel[name], f"{name} exposes no finding_text template"
    assert {path.name for path in RISK_PANEL_PATHS} >= set(ISSUE_PANELS)


@pytest.mark.parametrize(
    ("template", "offends"),
    [
        ("SLC2A9 urate-raising allele, one copy (rs13129697 {genotype}) — one copy.", True),
        ("MT-RNR1 m.1555A>G detected (rs267606617 {genotype}).", True),
        ("LRRK2 G2019S (rs34637584{genotype}) detected.", True),
        ("HFE C282Y heterozygous ({genotype}) — you carry one copy.", False),
        (
            "You carry one copy each of Factor V Leiden (rs6025) and Prothrombin G20210A "
            "(rs1799963) ({genotype}) — double heterozygous.",
            False,
        ),
        ("A note that mentions rs6025 but never substitutes a genotype.", False),
    ],
)
def test_template_guard_discriminates(template: str, offends: bool) -> None:
    assert bool(LITERAL_RSID_BEFORE_GENOTYPE.search(template)) is offends


@pytest.mark.parametrize("panel_path", PANEL_PATHS, ids=_panel_id)
def test_no_template_names_the_rsid_genotype_already_supplies(panel_path: Path) -> None:
    templates = _finding_text_templates(json.loads(panel_path.read_text(encoding="utf-8")))
    offenders = [
        template for template in templates if LITERAL_RSID_BEFORE_GENOTYPE.search(template)
    ]
    assert offenders == [], (
        f"{panel_path.name}: {len(offenders)} finding_text template(s) write a literal rsID "
        "before {genotype}, which already expands to '<rsid> <call>'"
    )


@pytest.mark.parametrize(
    ("rendered", "offends"),
    [
        ("CFH Y402H homozygous (rs1061170 rs1061170 CC) — both copies.", True),
        ("CFH Y402H homozygous (rs1061170 CC) — both copies.", False),
        ("Double heterozygous (rs6025 GA; rs1799963 GA).", False),
        (
            "Factor V Leiden (rs6025) with Prothrombin (rs1799963) (rs6025 GA; rs1799963 GA).",
            False,
        ),
    ],
)
def test_render_guard_discriminates(rendered: str, offends: bool) -> None:
    assert bool(ADJACENT_REPEATED_RSID.search(rendered)) is offends


@pytest.mark.parametrize("panel_path", RISK_PANEL_PATHS, ids=_panel_id)
def test_rendered_finding_never_repeats_an_rsid_back_to_back(panel_path: Path) -> None:
    """Every genotype model, rendered by the production formatter, names each rsID once."""
    panel = load_risk_panel(panel_path)
    assert panel.genotype_models, f"{panel_path.name} loads with no genotype models"

    offenders: dict[str, str] = {}
    for model in panel.genotype_models:
        rsids = _model_rsids(model)
        assert rsids, f"{panel_path.name}:{model.id} matches no rsID"
        readouts: dict[str, ProbeReadout] = {}
        dosages: dict[str, int | None] = {}
        for rsid in rsids:
            locus = panel.locus(rsid)
            allele = locus.risk_allele if locus is not None and locus.risk_allele else "A"
            readouts[rsid] = ProbeReadout(rsid=rsid, genotype=allele * 2, status=PROBE_TYPED)
            dosages[rsid] = 2
        call = _render_finding(model, panel, dosages, readouts, sex=None)
        repeated = ADJACENT_REPEATED_RSID.search(call.finding_text)
        if repeated is not None:
            offenders[model.id] = repeated.group(0)

    assert offenders == {}, (
        f"{panel_path.name}: rendered finding_text repeats an rsID back-to-back: {offenders}"
    )
